"""Opt-in BLE companion using the same authenticated dispatcher as WSS.

The CoreBluetooth connected-peripheral shim is isolated here and requires Bleak
3.0.2. No scanning result is chosen implicitly: the operator supplies its UUID.
"""
from __future__ import annotations
import asyncio
import contextlib
import hmac
import json
import logging
import secrets
import struct
import sys
from .ble_wire import Assembler, HEADER, MAX_MESSAGE, WINDOW, RX, TX, STATUS, SERVICE, WireError, packet, transcript, proof
from .remote_session import serve_remote_session
log = logging.getLogger(__name__)

# Acknowledgements advance the peripheral's credit window. Each GATT write
# costs a connection event, so acknowledging every assembled message (audio
# messages are 3 packets) stalls the sender at ~1 packet/event and silently
# overflows its 1.28 s PCM ring. Batch four packets per ACK, as the lab
# receiver does, and acknowledge control messages immediately for low latency.
ACK_EVERY = 4
# The peripheral pings every 5 s. macOS CoreBluetooth has been observed to stop
# delivering notifications to the app for tens of seconds after a long burst
# (audio upload), which used to tear down the session. Re-arm the subscription
# locally before the peripheral's own keepalive watchdog gives up.
IDLE_RECHECK_SECONDS = 7.0
MAX_IDLE_KICKS = 4

async def resolve_device(address):
    from bleak import BleakScanner
    if sys.platform == 'darwin':
        from CoreBluetooth import CBUUID
        from bleak.backends.device import BLEDevice
        async with BleakScanner(service_uuids=[SERVICE]) as scanner:
            delegate = scanner._backend._manager
            # A peripheral owned by macOS HID can report state=disconnected
            # until THIS central attaches. Trust the connected-peripheral API,
            # not the per-central CBPeripheral.state property. Battery/device
            # info cover native HID service filtering and old GATT caches.
            peers = list(delegate.central_manager.retrieveConnectedPeripheralsWithServices_([
                CBUUID.UUIDWithString_(u) for u in ('1812', '180f', '180a', SERVICE,
                    'd35c0001-69b0-4b89-9a72-5fd60d200001')]))
            for peer in peers:
                if str(peer.identifier().UUIDString()).lower() == address.lower():
                    return BLEDevice(address, str(peer.name() or 'Desk-Mate'), (peer, delegate))
        raise WireError("Connect this Mac on Desk-Mate page 2 first")
    return address

class BleChannel:
    def __init__(self, client, epoch, mtu):
        self.client, self.epoch = client, epoch
        self.payload = min(mtu, 247) - 3 - HEADER.size
        if self.payload <= 0:
            raise WireError('BLE MTU is too small')
        self.assembler = Assembler(epoch)
        self.incoming = asyncio.Queue(maxsize=8)
        self.packets = asyncio.Queue(maxsize=64)
        self.sent = self.acked = 0
        self.ack_seq = 0
        self.credit = asyncio.Event()
        self.credit.set()
        self.lock = asyncio.Lock()
        self.closed = False
        self.worker = None

    def notification(self, _, data):
        if self.closed:
            return
        try:
            self.packets.put_nowait(bytes(data))
        except asyncio.QueueFull:
            self.closed = True
            self.credit.set()
            self._wake_disconnect()

    def _wake_disconnect(self):
        while not self.incoming.empty():
            self.incoming.get_nowait()
        self.incoming.put_nowait({'type': 'websocket.disconnect'})

    async def start(self):
        self.worker = asyncio.create_task(self._pump())
        await self.client.start_notify(TX, self.notification)

    async def acknowledge(self):
        await asyncio.wait_for(self.client.write_gatt_char(
            RX, packet(3, self.epoch, self.assembler.next), response=False), 2)
        self.ack_seq = self.assembler.next

    async def _rearm_subscription(self) -> bool:
        # Re-register without unsubscribing: a CCCD 0->1 toggle would make the
        # peripheral treat the drop as a lost transport and restart auth.
        try:
            await asyncio.wait_for(self.client.start_notify(TX, self.notification), 5)
            return True
        except Exception:
            return False

    async def _pump(self):
        idle_kicks = 0
        # Cumulative re-arms on this channel. Stray status packets used to reset
        # idle_kicks forever, leaving auth stuck on a half-armed subscription;
        # after a few rounds force a reconnect (fresh subscription + epoch).
        rearms = 0
        try:
            while not self.closed:
                try:
                    data = await asyncio.wait_for(self.packets.get(), timeout=IDLE_RECHECK_SECONDS)
                except asyncio.TimeoutError:
                    if idle_kicks >= MAX_IDLE_KICKS:
                        raise
                    idle_kicks += 1
                    rearms += 1
                    if rearms > 6:
                        raise WireError('notify stalled repeatedly; reconnect')
                    log.warning('BLE notify stalled; re-arming subscription (%d/%d, total %d)',
                                idle_kicks, MAX_IDLE_KICKS, rearms)
                    # Failing to re-arm is not fatal: the peripheral retries its
                    # own notifications, so keep the authenticated session open
                    # and give it time to recover instead of tearing it down.
                    await self._rearm_subscription()
                    continue
                idle_kicks = 0
                if len(data) < HEADER.size:
                    raise WireError('short notification')
                version, kind, epoch, seq, off, total, size = HEADER.unpack_from(data)
                if epoch != self.epoch:
                    raise WireError('BLE session changed')
                if kind == 3:
                    if version != 1 or len(data) != 16 or off or total or size or not self.acked <= seq <= self.sent:
                        raise WireError('invalid acknowledgement')
                    self.acked = seq; self.credit.set()
                    continue
                result = self.assembler.feed(data)
                # Control messages (hello/seal/commit/ping) are rare: ack them
                # at once so the peripheral's tail is never left unacknowledged.
                # Bulk audio data is acknowledged in batches of ACK_EVERY.
                if (self.assembler.next - self.ack_seq >= ACK_EVERY
                        or (result is not None and result[0] == 1)):
                    await self.acknowledge()
                if result is not None:
                    kind, payload = result
                    await asyncio.wait_for(self.incoming.put({'text': payload.decode('utf-8')} if kind == 1 else {'bytes': payload}), 2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning('BLE channel stopped: %s (recv=%d sent=%d acked=%d)',
                        type(exc).__name__, self.assembler.next, self.sent, self.acked)
        finally:
            self.closed = True; self.credit.set(); self._wake_disconnect()

    async def send(self, kind, data):
        if not 0 < len(data) <= MAX_MESSAGE:
            raise WireError('message size exceeds BLE limit')
        async with self.lock:
            for off in range(0, len(data), self.payload):
                while self.sent - self.acked >= WINDOW and not self.closed:
                    self.credit.clear()
                    await asyncio.wait_for(self.credit.wait(), 2)
                if self.closed or not self.client.is_connected:
                    raise ConnectionError('BLE channel closed')
                chunk = data[off:off+self.payload]
                wire = packet(kind, self.epoch, self.sent, off, len(data), chunk)
                # Reserve the sequence before the platform yields to callbacks.
                self.sent += 1
                await asyncio.wait_for(self.client.write_gatt_char(RX, wire, response=False), 2)

    async def send_text(self, text):
        await self.send(1, text.encode('utf-8'))

    async def receive(self):
        if self.closed and self.incoming.empty():
            return {'type': 'websocket.disconnect'}
        return await self.incoming.get()

    async def close(self, code=1000, reason=''):
        self.closed = True; self.credit.set(); self._wake_disconnect()
        if self.worker:
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker
        with contextlib.suppress(Exception):
            await self.client.stop_notify(TX)

async def authenticate(channel, state):
    server = state.cfg.server.server_id
    host_nonce = secrets.token_hex(32)
    async def send(doc):
        await channel.send_text(json.dumps(doc, separators=(',', ':')))
    async def read(expected):
        message = await asyncio.wait_for(channel.receive(), 10)
        value = json.loads(message.get('text', ''))
        if value.get('type') != expected:
            raise WireError('unexpected authentication message')
        return value
    await send({'type':'ble_auth_start', 'server_id':server, 'nonce':host_nonce})
    challenge = await read('ble_challenge')
    if challenge.get('server_id') != server or challenge.get('epoch') != channel.epoch:
        raise WireError('server or epoch mismatch')
    device = challenge['device_id']
    key = state.db.ble_auth_key(device)
    if key is None:
        raise WireError('USB authorization required or revoked')
    text = transcript(server, device, channel.epoch, challenge['nonce'], host_nonce)
    await send({'type':'ble_auth_proof', 'proof':proof(key, 'server', text)})
    answer = await read('ble_auth_proof')
    if not hmac.compare_digest(str(answer.get('proof', '')), proof(key, 'device', text)):
        raise WireError('device proof mismatch')
    await send({'type':'ble_auth_complete', 'proof':proof(key, 'complete', text)})
    return device, key

def connection_error(exc,phase):
    message=str(exc).lower()
    if "usb authorization" in message:
        return {"error_code":"BLE_AUTH_REQUIRED","message":"这块设备尚未授权给当前服务端","hint":"通过 USB 连接新板子，选择“通过 USB 授权设备”"}
    if "proof mismatch" in message or "server or epoch mismatch" in message:
        return {"error_code":"BLE_AUTH_MISMATCH","message":"设备与当前服务端的授权不匹配","hint":"确认选择的是目标板子，并通过 USB 重新授权"}
    if "page 2" in message:
        return {"error_code":"BLE_SELECT_MAC","message":"设备尚未选中这台 Mac","hint":"请在设备第2屏选择并连接当前 Mac，然后等待自动重试"}
    if "voice service missing" in message:
        return {"error_code":"BLE_SERVICE_MISSING","message":"设备没有兼容的蓝牙语音服务","hint":"确认固件版本，断开并重新配对设备以刷新蓝牙服务"}
    if any(word in message for word in ("not authorized","unauthorized","permission","denied")):
        return {"error_code":"BLE_PERMISSION","message":"Desk-Mate 没有蓝牙访问权限","hint":"在系统设置的隐私与安全中允许 Desk-Mate 使用蓝牙"}
    if any(word in message for word in ("powered off","poweredoff","bluetooth is off")):
        return {"error_code":"BLUETOOTH_OFF","message":"Mac 蓝牙已关闭","hint":"打开系统蓝牙后会自动重试"}
    if isinstance(exc,(TimeoutError,asyncio.TimeoutError)):
        return {"error_code":"BLE_TIMEOUT","message":"设备连接或认证超时，正在自动重连","hint":"保持设备唤醒并靠近 Mac；持续失败时在设备第2屏重新点击连接这台 Mac（已授权设备无需再次 USB 授权）"}
    return {"error_code":"BLE_CONNECTION_FAILED","message":"蓝牙连接暂时失败，正在重试","hint":"检查设备电源、距离和第2屏连接；详细信息可在诊断中查看"}


async def run_connector(state, address):
    from bleak import BleakClient
    state.ble_status.update(enabled=True,phase="connecting",authenticated=False,address=address)
    logged_error = None
    while True:
        state.ble_status.update(phase="resolving", authenticated=False)
        channel = None
        try:
            device = await resolve_device(address)
            state.ble_status.update(phase="connecting")
            async with BleakClient(device, timeout=15) as client:
                state.ble_status.update(phase="services")
                if not all(client.services.get_characteristic(u) for u in (RX, TX, STATUS)):
                    raise WireError('BLE voice service missing: reconnect Bluetooth to refresh services')
                # Read again after subscription: subscribing establishes a fresh epoch.
                state.ble_status.update(phase="status")
                raw = await client.read_gatt_char(STATUS)
                if len(raw) != 14:
                    raise WireError('invalid BLE status')
                version, flags, mtu, epoch, generation, maximum = struct.unpack('<BBHIIH', raw)
                if version != 1 or maximum != MAX_MESSAGE:
                    raise WireError('unsupported BLE protocol')
                channel = BleChannel(client, epoch, mtu)
                # Wait for the firmware owner loop to publish subscription/epoch.
                state.ble_status.update(phase="subscribing")
                await client.start_notify(TX, channel.notification)
                for _ in range(20):
                    await asyncio.sleep(.05)
                    raw = await client.read_gatt_char(STATUS)
                    version, flags, mtu, epoch, generation, maximum = struct.unpack('<BBHIIH', raw)
                    if flags == 3:
                        break
                if flags != 3:
                    raise WireError('select this Mac on Desk-Mate page 2 first')
                channel.epoch = epoch; channel.assembler = Assembler(epoch)
                channel.payload = min(mtu, 247) - 19
                channel.worker = asyncio.create_task(channel._pump())
                state.ble_status.update(phase="authenticating", mtu=mtu, generation=generation)
                device_id, key = await authenticate(channel, state)
                state.ble_status.update(phase="authenticated", authenticated=True, last_error=None,
                                        error_code=None,message="蓝牙语音输入已连接",hint=None)
                logged_error = None
                log.info('BLE companion authenticated for device %s', device_id)
                session = asyncio.create_task(serve_remote_session(state, channel, device_id))
                try:
                    while not session.done() and client.is_connected and not channel.closed:
                        await asyncio.sleep(1)
                        state.ble_status.update(received_packets=channel.assembler.next, sent_packets=channel.sent, acknowledged_packets=channel.acked)
                        current = state.db.ble_auth_key(device_id)
                        if current is None or not hmac.compare_digest(current, key):
                            break
                finally:
                    await channel.close()
                    session.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await session
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Protocol errors have static descriptions; never log auth payloads.
            error = str(exc) if isinstance(exc, WireError) else type(exc).__name__
            state.ble_status.update(failed_phase=state.ble_status.get('phase'), phase='retry', authenticated=False, last_error=error)
            state.ble_status.update(connection_error(exc,state.ble_status.get("failed_phase")))
            if error != logged_error:
                log.warning('BLE companion (%s): %s', state.ble_status.get('failed_phase'), error)
                logged_error = error
        finally:
            if channel:
                await channel.close()
        await asyncio.sleep(3)
