"""Bounded BLE message framing and mutually authenticated USB credential proofs."""
from __future__ import annotations
import hashlib
import hmac
import struct

SERVICE = 'd35c1001-69b0-4b89-9a72-5fd60d200001'
RX = 'd35c1002-69b0-4b89-9a72-5fd60d200001'
TX = 'd35c1003-69b0-4b89-9a72-5fd60d200001'
STATUS = 'd35c1004-69b0-4b89-9a72-5fd60d200001'
HEADER = struct.Struct('<BBIIHHH')
MAX_MESSAGE = 2048
WINDOW = 24

class WireError(ValueError):
    pass

def packet(kind, epoch, sequence, offset=0, total=0, payload=b''):
    return HEADER.pack(1, kind, epoch, sequence, offset, total, len(payload)) + payload

class Assembler:
    def __init__(self, epoch):
        self.epoch, self.next, self.partial, self.total, self.kind = epoch, 0, bytearray(), 0, 0

    def feed(self, data):
        if len(data) < HEADER.size:
            raise WireError('short packet')
        version, kind, epoch, seq, off, total, size = HEADER.unpack_from(data)
        if epoch != self.epoch:
            return None
        if version != 1 or kind not in (1, 2) or seq != self.next:
            raise WireError('packet sequence or version')
        if not size or size + HEADER.size != len(data) or not 0 < total <= MAX_MESSAGE or off != len(self.partial) or off + size > total:
            raise WireError('packet bounds')
        if off and (total != self.total or kind != self.kind):
            raise WireError('interleaved message')
        self.total, self.kind = total, kind
        self.partial.extend(data[HEADER.size:]); self.next += 1
        if len(self.partial) == total:
            message = bytes(self.partial); self.partial.clear()
            return kind, message
        return None

def transcript(server, device, epoch, device_nonce, host_nonce):
    from uuid import UUID
    try:
        UUID(server); UUID(device)
        for nonce in (device_nonce, host_nonce):
            if len(nonce) != 64 or bytes.fromhex(nonce).hex() != nonce:
                raise ValueError('nonce')
    except (ValueError, TypeError) as exc:
        raise WireError('invalid authentication identity or nonce') from exc
    if not 0 < epoch <= 0xffffffff:
        raise WireError('invalid epoch')
    return f'desk-mate-ble-v1|{server}|{device}|{epoch}|{device_nonce}|{host_nonce}'

def proof(key, role, text):
    if role not in ('server', 'device', 'complete') or len(key) != 32:
        raise WireError('invalid proof inputs')
    return hmac.new(key, f'{role}|{text}'.encode('ascii'), hashlib.sha256).hexdigest()
