"""Production framing/auth tests use synthetic data and fake channels only."""
import asyncio
import hashlib
import json
import struct
from types import SimpleNamespace
import pytest
from voice_assistant.ble_wire import Assembler, HEADER, WireError, packet, proof, transcript
from voice_assistant.ble_bridge import BleChannel, authenticate

SERVER='11111111-1111-4111-8111-111111111111'
DEVICE='22222222-2222-4222-8222-222222222222'

def test_golden_packet_and_partial_message():
    wire=packet(2,0x12345678,0,0,5,b'abc')
    assert wire.hex()=='01027856341200000000000005000300616263'
    a=Assembler(0x12345678)
    assert a.feed(wire) is None
    assert a.feed(packet(2,a.epoch,1,3,5,b'de'))==(2,b'abcde')
    assert a.feed(packet(1,a.epoch,2,0,2,b'{}'))==(1,b'{}')

@pytest.mark.parametrize('wire',[b'x',packet(1,1,1,0,1,b'a'),packet(1,1,0,1,2,b'a'),packet(1,1,0,0,2049,b'a'),packet(1,1,0,0,1,b'ab'),packet(4,1,0,0,1,b'a')])
def test_malformed(wire):
    with pytest.raises(WireError): Assembler(1).feed(wire)

def test_epoch_and_interleaving():
    a=Assembler(1)
    assert a.feed(packet(1,2,0,0,1,b'a')) is None
    a.feed(packet(1,1,0,0,2,b'a'))
    with pytest.raises(WireError):a.feed(packet(2,1,1,1,2,b'b'))

def test_proof_roles_and_nonce():
    text=transcript(SERVER,DEVICE,7,'00'*32,'11'*32)
    key=hashlib.sha256(b'secret').digest()
    assert proof(key,'server',text)!=proof(key,'device',text)
    assert proof(key,'complete',text)!=proof(key,'server',text)
    with pytest.raises(WireError):transcript(SERVER,DEVICE,7,'x'*64,'11'*32)

class AuthChannel:
    epoch=7
    def __init__(self,key,bad=False):self.key=key;self.bad=bad;self.stage=0;self.sent=[]
    async def send_text(self,text):self.sent.append(json.loads(text))
    async def receive(self):
        if self.stage==0:
            self.stage=1
            return {'text':json.dumps(dict(type='ble_challenge',server_id=SERVER,device_id=DEVICE,epoch=7,nonce='00'*32))}
        text=transcript(SERVER,DEVICE,7,'00'*32,self.sent[0]['nonce'])
        assert self.sent[1]['proof']==proof(self.key,'server',text)
        return {'text':json.dumps(dict(type='ble_auth_proof',proof='00'*32 if self.bad else proof(self.key,'device',text)))}

def test_mutual_auth_and_revoked_credentials():
    async def run():
        key=hashlib.sha256(b'token').digest()
        state=SimpleNamespace(cfg=SimpleNamespace(server=SimpleNamespace(server_id=SERVER)),db=SimpleNamespace(ble_auth_key=lambda d:key if d==DEVICE else None))
        c=AuthChannel(key);assert await authenticate(c,state)==(DEVICE,key)
        assert c.sent[-1]['type']=='ble_auth_complete'
        with pytest.raises(WireError):await authenticate(AuthChannel(key,True),state)
        state.db.ble_auth_key=lambda d:None
        with pytest.raises(WireError):await authenticate(AuthChannel(key),state)
    asyncio.run(run())

class Radio:
    is_connected=True
    def __init__(self):self.writes=[];self.channel=None
    async def write_gatt_char(self,uuid,data,response):
        self.writes.append(data)
        if data[1]!=3:
            seq=HEADER.unpack_from(data)[3]+1
            self.channel.notification(None,packet(3,7,seq))
    async def stop_notify(self,uuid):pass

def test_channel_fragment_credit_and_receive():
    async def run():
        radio=Radio();c=BleChannel(radio,7,247);radio.channel=c;c.worker=asyncio.create_task(c._pump())
        payload=b'a'*2048
        for _ in range(4):await c.send(2,payload)
        a=Assembler(7);messages=[]
        for data in radio.writes:
            value=a.feed(data)
            if value:messages.append(value)
        assert messages==[(2,payload)]*4
        for seq in range(3):c.notification(None,packet(2,7,seq,seq*228,648,b'b'*min(228,648-seq*228)))
        assert (await asyncio.wait_for(c.receive(),1))['bytes']==b'b'*648
        await c.close()
    asyncio.run(run())

def test_stale_epoch_closes_channel():
    async def run():
        c=BleChannel(Radio(),7,247);c.worker=asyncio.create_task(c._pump())
        c.notification(None,packet(1,8,0,0,2,b'{}'))
        assert (await asyncio.wait_for(c.receive(),1))['type']=='websocket.disconnect'
        await c.close()
    asyncio.run(run())

def test_credit_window_stops_sender_without_ack():
    class SilentRadio(Radio):
        async def write_gatt_char(self,uuid,data,response):self.writes.append(data)
    async def run():
        radio=SilentRadio();c=BleChannel(radio,7,247)
        async def flood():
            for _ in range(4):await c.send(2,b'a'*2048)
        with pytest.raises(asyncio.TimeoutError):await asyncio.wait_for(flood(),.03)
        assert len(radio.writes)==24 and c.sent==24 and c.acked==0
        await c.close()
    asyncio.run(run())

def test_notification_overflow_fails_closed():
    async def run():
        c=BleChannel(Radio(),7,247)
        for _ in range(65):c.notification(None,packet(1,7,0,0,2,b'{}'))
        assert c.closed and (await c.receive())['type']=='websocket.disconnect'
        await c.close()
    asyncio.run(run())

def test_mac_hid_peripheral_can_be_connected_with_state_zero(monkeypatch):
    import sys
    from types import SimpleNamespace
    import voice_assistant.ble_bridge as bridge
    class Peer:
        def identifier(self):return SimpleNamespace(UUIDString=lambda:'known-uuid')
        def name(self):return 'Desk-Mate'
        def state(self):return 0  # observed: OS owns HID, this central has not attached
    class Central:
        def retrieveConnectedPeripheralsWithServices_(self,services):
            assert '180f' in services and bridge.SERVICE in services
            return [Peer()]
    delegate=SimpleNamespace(central_manager=Central())
    class Scanner:
        def __init__(self,**kw):self._backend=SimpleNamespace(_manager=delegate)
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
    class Device:
        def __init__(self,address,name,details):self.address=address;self.details=details
    monkeypatch.setattr(sys,'platform','darwin')
    monkeypatch.setitem(sys.modules,'CoreBluetooth',SimpleNamespace(CBUUID=SimpleNamespace(UUIDWithString_=lambda u:u)))
    monkeypatch.setitem(sys.modules,'bleak',SimpleNamespace(BleakScanner=Scanner))
    monkeypatch.setitem(sys.modules,'bleak.backends.device',SimpleNamespace(BLEDevice=Device))
    device=asyncio.run(bridge.resolve_device('known-uuid'))
    assert device.address=='known-uuid' and device.details[1] is delegate
