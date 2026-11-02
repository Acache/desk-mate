"""Data-only BLE laboratory tests: no radio, microphone, STT or software actions."""
import asyncio
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from voice_assistant.ble_lab import (
    CONTROL_UUID, DATA_UUID, INFO_UUID, INFO, HEADER, STATUS, ProbeError, Receiver, decode_status,
    run_probe, validate_parameters,
)


def packet(run, seq, offset, count):
    return HEADER.pack(1, 2, count, run, seq, offset) + bytes(
        ((offset + i) * 31 + 17) & 255 for i in range(count)
    )


def test_receiver_counts_pcm_payload_and_rejects_corruption():
    r = Receiver(4, 300, clock=lambda: 1.0)
    r.feed(packet(3, 0, 0, 100))  # late data from previous run is ignored
    assert r.received == 0
    r.feed(packet(4, 0, 0, 100))
    r.feed(packet(4, 1, 100, 200))
    assert r.done and r.received == 300 and r.next_seq == 2
    r = Receiver(4, 100)
    broken = bytearray(packet(4, 0, 0, 100)); broken[-1] ^= 1
    with pytest.raises(ProbeError, match="corrupt"):
        r.feed(broken)


@pytest.mark.parametrize("data", [b"", b"\x01" * 15, HEADER.pack(2, 2, 1, 4, 0, 0) + b"x",
    HEADER.pack(1, 2, 2, 4, 0, 0) + b"x", packet(4, 1, 0, 10), packet(4, 0, 1, 10), packet(4, 0, 0, 101)])
def test_receiver_rejects_malformed_gap_and_overrun(data):
    with pytest.raises(ProbeError):
        Receiver(4, 100).feed(data)


def test_duplicate_is_not_silently_accepted():
    r = Receiver(4, 100)
    r.feed(packet(4, 0, 0, 50))
    with pytest.raises(ProbeError):
        r.feed(packet(4, 0, 0, 50))


def test_limits_and_status():
    assert validate_parameters(10, 32000) == 320000
    for seconds, rate in [(0, 32000), (61, 32000), (60, 80000), (10, 0)]:
        with pytest.raises(ValueError): validate_parameters(seconds, rate)
    with pytest.raises(ProbeError): decode_status(b"bad")
    assert decode_status(STATUS.pack(1, 2, 166, 4, 3, 335, 3, 335, 32000)).ack_bytes == 335


def test_rate_and_gap_are_part_of_acceptance():
    now = [0.0]
    r = Receiver(4, 100, clock=lambda: now[0])
    r.feed(packet(4, 0, 0, 50)); now[0] = 1.0; r.feed(packet(4, 1, 50, 50))
    assert not r.report(elapsed=1, target_rate=1000, complete=True)["passed"]
    assert not r.report(elapsed=.05, target_rate=1000, complete=True)["passed"]  # receive gap


class FakeClient:
    """Exercises credit/ACK completion without any external application."""
    def __init__(self):
        self.state = 0; self.run = 9; self.sent = 0; self.ack = 0; self.seq = 0; self.ack_seq = 0
        self.total = 0; self.rate = 0; self.subscribed = False; self.task = None

    async def read_gatt_char(self, uuid):
        if uuid == INFO_UUID: return INFO.pack(1, 1, 119, 12, 0, 1)
        assert uuid == CONTROL_UUID
        return STATUS.pack(1, self.state, 100, self.run, self.seq, self.sent, self.ack_seq, self.ack, self.rate)

    async def start_notify(self, uuid, callback):
        assert uuid == DATA_UUID; self.callback = callback; self.subscribed = True

    async def stop_notify(self, uuid):
        assert uuid == DATA_UUID; self.subscribed = False
        if self.task: await self.task

    async def write_gatt_char(self, uuid, data, response):
        assert uuid == CONTROL_UUID
        v, op, window, run, a, b = HEADER.unpack(data)
        assert v == 1
        if op == 1:
            assert response and window == 24 and run == 10
            self.run = run; self.total = a; self.rate = b; self.state = 1
            async def send():
                while self.sent < self.total:
                    while self.seq - self.ack_seq >= window: await asyncio.sleep(.001)
                    n = min(100, self.total-self.sent)
                    self.callback(None, packet(run, self.seq, self.sent, n))
                    self.sent += n; self.seq += 1
                    await asyncio.sleep(.001)
            self.task = asyncio.create_task(send())
        elif op == 2:
            self.ack_seq = a; self.ack = b
            if self.ack == self.total: self.state = 2
        elif op == 3:
            self.state = 3


def test_full_fake_transfer_is_acked_and_unsubscribed():
    async def exercise():
        c = FakeClient(); result = await run_probe(c, seconds=3, rate=1000)
        assert result["passed"] and result["received_bytes"] == 3000
        assert c.ack == 3000 and not c.subscribed and c.state == 2
    asyncio.run(exercise())


def test_bounded_receiver_failure_stops_and_unsubscribes():
    class OverflowClient(FakeClient):
        async def write_gatt_char(self, uuid, data, response):
            await super().write_gatt_char(uuid, data, response)
            if HEADER.unpack(data)[1] == 1:
                for i in range(40): self.callback(None, packet(self.run, i, i*10, 10))
    async def exercise():
        c = OverflowClient()
        with pytest.raises(ProbeError, match="queue overflow"):
            await run_probe(c, seconds=1, rate=1000)
        assert c.state == 3 and not c.subscribed
    asyncio.run(exercise())


def test_reported_multiple_connections_rejects_run_and_cleans_up():
    class WrongLink(FakeClient):
        async def read_gatt_char(self, uuid):
            if uuid == INFO_UUID: return INFO.pack(1, 2, 119, 12, 0, 1)
            return await super().read_gatt_char(uuid)
    async def exercise():
        c = WrongLink()
        with pytest.raises(ProbeError, match="exactly one"):
            await run_probe(c, seconds=1, rate=1000)
        assert c.state == 3 and not c.subscribed
    asyncio.run(exercise())


def test_old_os_gatt_cache_does_not_block_payload_measurement():
    class CachedClient(FakeClient):
        services = type("Services", (), {"get_characteristic": lambda self, uuid: None})()
    async def exercise():
        c = CachedClient(); result = await run_probe(c, seconds=1, rate=1000)
        assert result["passed"] and result["link"]["connections"] is None
        assert "cache" in result["link"]["diagnostics"]
    asyncio.run(exercise())
