"""Transport-independent command authorization/cancellation; no real STT/actions."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from voice_assistant.remote_session import serve_remote_session
from voice_assistant.contracts import CommandState

class Channel:
    def __init__(self,messages):self.messages=iter(messages);self.sent=[];self.closed=False
    async def receive(self):return next(self.messages,{'type':'websocket.disconnect'})
    async def send_text(self,text):self.sent.append(json.loads(text))
    async def close(self,**kw):self.closed=True

class Manager:
    def __init__(self):self.attached=None
    async def attach(self,device,channel):self.attached=channel
    async def detach(self,device,channel):self.attached=None

def test_cross_device_status_cancel_feedback_rejected():
    async def run(kind):
        server,device,other,command=map(str,[uuid4(),uuid4(),uuid4(),uuid4()])
        row={'device_id':other,'state':'STAGING'}
        db=SimpleNamespace(get_command=lambda _:row,record_session=lambda *a:None)
        called=[]
        state=SimpleNamespace(cfg=SimpleNamespace(server=SimpleNamespace(server_id=server)),db=db,
            stages=SimpleNamespace(cancel=lambda *a:called.append(a)),service=None,manager=Manager(),
            ready=lambda:(True,None),capabilities=lambda:())
        msg={'v':1,'type':kind,'msg_id':2,'command_id':command}
        if kind=='cancel':msg['reason']='user'
        if kind in ('seal','commit'):msg['sha256']='0'*64
        if kind=='seal':msg.update(frame_count=25,pcm_bytes=16000)
        if kind=='feedback':msg.update(rating='good',dismissed_by='tap',touch_count=1,countdown_at_dismiss=0,page=2)
        channel=Channel([{'text':json.dumps(dict(v=1,type='hello',msg_id=1,device_id=device,server_id=server,firmware_version='test',boot_id=str(uuid4())))},{'text':json.dumps(msg)}])
        await serve_remote_session(state,channel,device)
        assert channel.sent[-1]['code']=='AUTH_FAILED'
        assert channel.closed and not called and state.manager.attached is None
    # Canonical protocol spellings, including cross-device status recovery.
    for kind in ('status','cancel','feedback','seal','commit'):
        asyncio.run(run(kind))

@pytest.mark.parametrize('finish', ['cancel','disconnect'])
def test_synthetic_staging_never_executes(tmp_path,finish):
    import hashlib
    import struct
    from voice_assistant.config import load_config
    from voice_assistant.storage import Database
    from voice_assistant.sessions import StageStore, CommandService
    async def run():
        cfg=load_config(tmp_path);db=Database(cfg.db_path)
        device,_=db.register_device('synthetic BLE fixture')
        stages=StageStore(cfg,db);command=str(uuid4());pcm=b'\x01\x00'*320
        base={'v':1,'msg_id':1}
        def text(**kw):return {'text':json.dumps(base|kw)}
        messages=[text(type='hello',device_id=device,server_id=cfg.server.server_id,firmware_version='fixture',boot_id=str(uuid4())),
            text(type='begin',command_id=command,kind='audio',format=dict(encoding='s16le',rate=16000,channels=1),trigger='esp32_ptt')]
        messages.extend({'bytes':struct.pack('<IHH',i,640,0)+pcm} for i in range(25))
        messages.append(text(type='seal',command_id=command,frame_count=25,pcm_bytes=16000,sha256=hashlib.sha256(pcm*25).hexdigest()))
        if finish=='cancel':messages.append(text(type='cancel',command_id=command,reason='user'))
        state=SimpleNamespace(cfg=cfg,db=db,stages=stages,service=CommandService(cfg,db,stages),manager=Manager(),ready=lambda:(True,None),capabilities=lambda:())
        channel=Channel(messages)
        await serve_remote_session(state,channel,device)
        assert any(m['type']=='seal_ack' for m in channel.sent)
        assert db.get_command(command)['state']=='CANCELED'
        assert state.manager.attached is None
        db.close()
    asyncio.run(run())

def test_adpcm_frames_are_decoded_before_staging(tmp_path):
    import hashlib
    import struct
    from voice_assistant import adpcm
    from voice_assistant.config import load_config
    from voice_assistant.storage import Database
    from voice_assistant.sessions import StageStore, CommandService
    async def run():
        cfg=load_config(tmp_path);db=Database(cfg.db_path)
        device,_=db.register_device('synthetic ima4 fixture')
        stages=StageStore(cfg,db);command=str(uuid4())
        pcm=b'\x20\x00'*320
        state=(0,0);blocks=[];recon_all=b''
        for _ in range(5):
            block,state,recon=adpcm.encode_block(pcm,state)
            blocks.append(block);recon_all+=recon
        base={'v':1,'msg_id':1}
        def text(**kw):return {'text':json.dumps(base|kw)}
        messages=[text(type='hello',device_id=device,server_id=cfg.server.server_id,firmware_version='fixture',boot_id=str(uuid4())),
            text(type='begin',command_id=command,kind='audio',format=dict(encoding='s16le',rate=16000,channels=1),trigger='esp32_ptt',codec='ima4')]
        messages.extend({'bytes':struct.pack('<IHH',i,adpcm.BLOCK_BYTES,0)+blocks[i]} for i in range(5))
        messages.append(text(type='seal',command_id=command,frame_count=5,pcm_bytes=len(recon_all),sha256=hashlib.sha256(recon_all).hexdigest()))
        messages.append(text(type='cancel',command_id=command,reason='user'))
        captured={}
        original_seal=stages.seal
        def spy(cid,frame_count,pcm_bytes,sha):
            captured['pcm']=(cfg.staging_dir/f'{cid}.pcm.part').read_bytes()
            return original_seal(cid,frame_count,pcm_bytes,sha)
        stages.seal=spy
        state_ns=SimpleNamespace(cfg=cfg,db=db,stages=stages,service=CommandService(cfg,db,stages),manager=Manager(),ready=lambda:(True,None),capabilities=lambda:())
        channel=Channel(messages)
        await serve_remote_session(state_ns,channel,device)
        assert any(m['type']=='seal_ack' for m in channel.sent), channel.sent
        assert captured['pcm']==recon_all and len(captured['pcm'])==5*640
        db.close()
    asyncio.run(run())


def test_seal_starts_early_stt_and_cancel_clears_it(tmp_path):
    import hashlib
    import struct
    from voice_assistant.config import load_config
    from voice_assistant.storage import Database
    from voice_assistant.sessions import StageStore

    async def run(finish):
        cfg = load_config(tmp_path)
        db = Database(cfg.db_path)
        device, _ = db.register_device("early STT fixture")
        stages = StageStore(cfg, db)
        command = str(uuid4())
        pcm = b"\x01\x00" * 320
        calls: list[str] = []

        class Stt:
            def transcribe(self, path, deadline):
                calls.append(str(path))
                return SimpleNamespace(normalized_text="测试")

        state = SimpleNamespace(
            cfg=cfg, db=db, stages=stages, service=None, manager=Manager(),
            ready=lambda: (True, None), capabilities=lambda: (),
            stt=Stt(), early_stt={},
        )
        base = {"v": 1, "msg_id": 1}

        def text(**kw):
            return {"text": json.dumps(base | kw)}

        messages = [
            text(type="hello", device_id=device, server_id=cfg.server.server_id,
                 firmware_version="fixture", boot_id=str(uuid4())),
            text(type="begin", command_id=command, kind="audio",
                 format=dict(encoding="s16le", rate=16000, channels=1), trigger="esp32_ptt"),
        ]
        messages.extend({"bytes": struct.pack("<IHH", i, 640, 0) + pcm} for i in range(25))
        messages.append(text(type="seal", command_id=command, frame_count=25,
                             pcm_bytes=16000, sha256=hashlib.sha256(pcm * 25).hexdigest()))
        if finish == "cancel":
            messages.append(text(type="cancel", command_id=command, reason="user"))
        await serve_remote_session(state, Channel(messages), device)
        return calls, state.early_stt

    calls, early = asyncio.run(run("disconnect"))
    assert early, "seal must start an early STT task"
    task = next(iter(early.values()))
    assert hasattr(task, "cancel")
