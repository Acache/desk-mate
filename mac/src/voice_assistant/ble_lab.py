"""Opt-in synthetic BLE stream probe; independent of STT/DB/execution.

python -m voice_assistant.ble_lab scan
python -m voice_assistant.ble_lab run --address <Mac CoreBluetooth UUID> --rate 32000
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import struct
import sys
import time
from typing import Callable

SERVICE_UUID = "d35c0001-69b0-4b89-9a72-5fd60d200001"
CONTROL_UUID = "d35c0002-69b0-4b89-9a72-5fd60d200001"
DATA_UUID = "d35c0003-69b0-4b89-9a72-5fd60d200001"
INFO_UUID = "d35c0004-69b0-4b89-9a72-5fd60d200001"
HEADER = struct.Struct("<BBHIII")
STATUS = struct.Struct("<BBHIIIIII")
INFO = struct.Struct("<BBHHHI")


class ProbeError(RuntimeError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True)
class Status:
    state: int
    payload: int
    run: int
    sent_seq: int
    sent_bytes: int
    ack_seq: int
    ack_bytes: int
    rate: int


def decode_status(data: bytes) -> Status:
    if len(data) != STATUS.size:
        raise ProbeError("unexpected status length; lab firmware required")
    version, *fields = STATUS.unpack(data)
    if version != 1 or fields[0] > 7:
        raise ProbeError("unsupported lab status")
    return Status(*fields)


def validate_parameters(seconds: int, rate: int) -> int:
    if not 1 <= seconds <= 60 or not 1000 <= rate <= 80000 or seconds * rate > 3200000:
        raise ValueError("duration 1..60 seconds, rate 1000..80000 B/s, total <= 3.2MB")
    return seconds * rate


class Receiver:
    def __init__(self, run: int, total: int, clock: Callable[[], float] = time.monotonic):
        self.run = run
        self.total = total
        self.clock = clock
        self.received = self.next_seq = self.stale_packets = 0
        self.last_at: float | None = None
        self.max_gap = 0.0

    @property
    def done(self) -> bool:
        return self.received == self.total

    def feed(self, data: bytes) -> None:
        if len(data) < HEADER.size:
            raise ProbeError("short packet")
        version, kind, size, run, seq, offset = HEADER.unpack_from(data)
        if version != 1 or kind != 2 or not size or size > 228 or len(data) != HEADER.size + size:
            raise ProbeError("invalid packet header/length")
        if run != self.run:
            self.stale_packets += 1
            return
        if seq != self.next_seq or offset != self.received or offset + size > self.total:
            raise ProbeError("sequence/offset gap, duplicate or overrun")
        expected = bytes(((offset+i)*31+17) & 255 for i in range(size))
        if data[HEADER.size:] != expected:
            raise ProbeError("corrupt payload")
        now = self.clock()
        if self.last_at is not None:
            self.max_gap = max(self.max_gap, now-self.last_at)
        self.last_at = now
        self.next_seq += 1
        self.received += size

    def report(self, *, elapsed: float, target_rate: int, complete: bool) -> dict:
        effective = self.received/max(elapsed, 1e-6)
        return {
            "experiment": "synthetic-ble-pcm-rate", "protocol": 1,
            "target_bytes_per_second": target_rate, "received_bytes": self.received,
            "packets": self.next_seq, "stale_packets": self.stale_packets,
            "elapsed_seconds": round(elapsed, 4), "effective_bytes_per_second": round(effective, 1),
            "longest_receive_gap_ms": round(self.max_gap*1000, 2),
            "receiver_acknowledged_complete": complete,
            "passed": bool(complete and self.done and effective >= target_rate*.95 and self.max_gap <= .250),
            "scope": "data transport only; no microphone, recognition or software actions",
        }


async def run_probe(client, *, seconds: int, rate: int, clock=time.monotonic) -> dict:
    total = validate_parameters(seconds, rate)
    old = decode_status(bytes(await client.read_gatt_char(CONTROL_UUID)))
    if old.state == 1:
        raise ProbeError("another lab run is active; wait for it to finish")
    run = (old.run+1) & 0xffffffff or 1
    receiver = Receiver(run, total, clock)
    incoming: asyncio.Queue[bytes | Exception] = asyncio.Queue(maxsize=32)
    overflow = False

    def on_data(_, data):
        nonlocal overflow
        try:
            incoming.put_nowait(bytes(data))
        except asyncio.QueueFull:
            overflow = True

    await client.start_notify(DATA_UUID, on_data)
    subscribed = True
    started = False
    completed = False
    link = {}
    begin = clock()
    try:
        started = True  # Even a timed-out write may have reached the peripheral.
        await client.write_gatt_char(CONTROL_UUID, HEADER.pack(1, 1, 24, run, total, rate), response=True)
        # Read once to diagnose missing HID-ready connection, MTU or voice busy gate.
        for _ in range(30):
            status = decode_status(bytes(await client.read_gatt_char(CONTROL_UUID)))
            if status.run == run and status.state in (1, 2):
                break
            if status.state in (4, 5, 6, 7):
                raise ProbeError(f"firmware rejected start (state={status.state}); check MTU, selected HID peer and voice idle")
            await asyncio.sleep(.02)
        else:
            raise ProbeError("start not accepted; select this paired Mac as connected media peer and subscribe again")
        services = getattr(client, "services", None)
        if services is not None and services.get_characteristic(INFO_UUID) is None:
            # macOS can retain the old GATT table after an additive firmware
            # update. Optional diagnostics must not require erasing a bond.
            link = {"connections": None, "generation": None, "window_packets": 24,
                    "diagnostics": "info characteristic absent from OS service cache"}
        else:
            raw_info = bytes(await client.read_gatt_char(INFO_UUID))
            if len(raw_info) != INFO.size:
                raise ProbeError("invalid link information")
            version, peers, mtu, interval, reserved, generation = INFO.unpack(raw_info)
            if version != 1 or peers != 1 or reserved or not generation:
                raise ProbeError("expected exactly one live BLE connection")
            link = {"connections": peers, "mtu": mtu, "interval_ms": interval*1.25,
                    "generation": generation, "window_packets": 24}
        last_ack = 0
        deadline = asyncio.get_running_loop().time()+seconds+5
        while not receiver.done:
            if overflow:
                raise ProbeError("bounded receive queue overflow")
            left = deadline-asyncio.get_running_loop().time()
            if left <= 0:
                raise ProbeError("transfer deadline exceeded")
            try:
                packet = await asyncio.wait_for(incoming.get(), min(2.5, left))
            except asyncio.TimeoutError as exc:
                raise ProbeError("BLE stream stalled; no data for 2.5 seconds") from exc
            receiver.feed(packet)
            if receiver.next_seq-last_ack >= 4 or receiver.done:
                await client.write_gatt_char(CONTROL_UUID,
                    HEADER.pack(1, 2, 0, run, receiver.next_seq, receiver.received), response=False)
                last_ack = receiver.next_seq
        elapsed = clock()-begin
        for _ in range(50):
            status = decode_status(bytes(await client.read_gatt_char(CONTROL_UUID)))
            if status.run == run and status.state == 2:
                completed = status.ack_bytes == total and status.sent_bytes == total and status.ack_seq == receiver.next_seq
                break
            if status.state not in (1, 2):
                break
            await asyncio.sleep(.02)
        return {**receiver.report(elapsed=elapsed, target_rate=rate, complete=completed), "link": link}
    except ProbeError as exc:
        exc.details = receiver.report(elapsed=clock()-begin, target_rate=rate, complete=False)
        if link: exc.details["link"] = link
        try:
            raw = await asyncio.wait_for(client.read_gatt_char(CONTROL_UUID), 1)
            exc.details["firmware_status"] = asdict(decode_status(bytes(raw)))
        except Exception:
            pass
        raise
    finally:
        if started and not completed:
            try:
                await client.write_gatt_char(CONTROL_UUID, HEADER.pack(1, 3, 0, run, 0, 0), response=True)
            except Exception:
                pass  # Peripheral also has credit/stall and overall timeouts.
        if subscribed:
            await client.stop_notify(DATA_UUID)


async def _main(args) -> int:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise ProbeError("install optional mac/requirements-ble.txt into a project virtual environment") from exc
    if args.command == "scan":
        found = {}
        connected_count = 0
        async with BleakScanner(service_uuids=["1812"]) as scanner:
            # Bleak 3.0.2 has no public connected-peer enumeration. Keep this
            # version-pinned macOS shim separate from the portable protocol.
            # Apple API retrieves existing physical links; it creates no link.
            if sys.platform == "darwin":
                from CoreBluetooth import CBUUID
                manager = scanner._backend._manager.central_manager
                peers = manager.retrieveConnectedPeripheralsWithServices_([CBUUID.UUIDWithString_("1812"), CBUUID.UUIDWithString_("180f"), CBUUID.UUIDWithString_(SERVICE_UUID), CBUUID.UUIDWithString_("d35c1001-69b0-4b89-9a72-5fd60d200001")])
                connected_count = len(peers)
                for peer in peers:
                    if (peer.name() or "").lower().replace("-", "").replace(" ", "").startswith("deskmate"):
                        address = str(peer.identifier().UUIDString())
                        found[address] = {"address": address, "name": str(peer.name()), "connected": True}
            await asyncio.sleep(args.timeout)
            for d, a in scanner.discovered_devices_and_advertisement_data.values():
                if (a.local_name or d.name or "").startswith("Desk-Mate"):
                    found.setdefault(d.address, {"address": d.address, "name": a.local_name or d.name, "rssi": a.rssi})
        print(json.dumps({"devices": list(found.values()), "connected_hid_candidates": connected_count,
            "note": "macOS identifies peripherals by CoreBluetooth UUID"}, indent=2))
        return 0 if found else 2
    validate_parameters(args.seconds, args.rate)
    device = args.address
    # An OS-connected HID peer stops advertising. Passing only its UUID makes
    # Bleak scan again and miss it; reuse the connected CBPeripheral instead.
    if sys.platform == "darwin":
        from CoreBluetooth import CBUUID
        from bleak.backends.device import BLEDevice
        async with BleakScanner(service_uuids=["1812"]) as scanner:
            delegate = scanner._backend._manager
            peers = delegate.central_manager.retrieveConnectedPeripheralsWithServices_([
                CBUUID.UUIDWithString_("1812"), CBUUID.UUIDWithString_("180f"), CBUUID.UUIDWithString_(SERVICE_UUID), CBUUID.UUIDWithString_("d35c1001-69b0-4b89-9a72-5fd60d200001")])
            for peer in peers:
                if str(peer.identifier().UUIDString()).lower() == args.address.lower():
                    device = BLEDevice(args.address, str(peer.name() or "Desk-Mate"), (peer, delegate))
                    break
    disconnected = False
    def on_disconnect(_):
        nonlocal disconnected
        disconnected = True
    async with BleakClient(device, timeout=30, disconnected_callback=on_disconnect) as client:
        if args.suite:
            results = []
            for rate in (32000, 50000):
                try:
                    measured = await asyncio.wait_for(run_probe(client, seconds=args.seconds, rate=rate), args.seconds+15)
                except ProbeError as exc:
                    measured = {"passed": False, "error": str(exc), **exc.details}
                results.append(measured)
                await asyncio.sleep(.5)
            generations = {r.get("link", {}).get("generation") for r in results}-{None}
            verified = all(r.get("link", {}).get("connections") == 1 for r in results)
            shared = bool(client.is_connected and not disconnected and len(generations)<=1)
            result = {"results": results, "same_single_connection": shared,
                "firmware_connection_counter_verified": verified, "passed": shared and all(r["passed"] for r in results)}
        else:
            result = await asyncio.wait_for(run_probe(client, seconds=args.seconds, rate=args.rate), args.seconds+15)
        result["negotiated_mtu"] = client.mtu_size
    print(json.dumps(result, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2)+"\n")
    return 0 if result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan"); scan.add_argument("--timeout", type=float, default=8)
    run = sub.add_parser("run"); run.add_argument("--address", required=True)
    run.add_argument("--rate", type=int, default=32000); run.add_argument("--seconds", type=int, default=10)
    run.add_argument("--suite", action="store_true", help="measure 32KB/s and 50KB/s on the same physical connection")
    run.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        return asyncio.run(_main(args))
    except (ProbeError, ValueError) as exc:
        failure = {"passed": False, "error": str(exc), **getattr(exc, "details", {})}
        print(json.dumps(failure, indent=2))
        if getattr(args, "report", None):
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(failure, indent=2)+"\n")
        return 1
    except Exception as exc:
        # In particular, surface OS Bluetooth authorization/availability errors.
        print(json.dumps({"passed": False, "error_type": type(exc).__name__, "error": str(exc)})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
