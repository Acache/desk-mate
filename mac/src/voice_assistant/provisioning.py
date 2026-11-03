"""USB provisioning: bounded config bundle, serial handshake and token creation."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import serial
from serial.tools import list_ports

from .config import AppConfig, generate_device_token, utc_now
from .storage import Database

MAX_BUNDLE = 16 * 1024
SETUP_COMMAND = b"VA_SETUP\n"
SETUP_READY = "VA_SETUP_READY"
OK_PREFIX = "VA_OK"
ERR_PREFIX = "VA_ERR"
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


class ProvisioningError(RuntimeError):
    pass


@dataclass
class PortInfo:
    device: str
    description: str
    hwid: str


def list_serial_ports() -> list[PortInfo]:
    return [
        PortInfo(p.device, p.description or "", p.hwid or "")
        for p in sorted(list_ports.comports(), key=lambda p: p.device)
    ]


def choose_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    ports = [p for p in list_serial_ports() if "usbmodem" in p.device or "usbserial" in p.device]
    if len(ports) == 1:
        return ports[0].device
    if not ports:
        raise ProvisioningError("no USB serial port found; connect the ESP32 and retry with --port")
    raise ProvisioningError("multiple USB serial ports; choose one with --port: " + ", ".join(p.device for p in ports))


def build_bundle(
    cfg: AppConfig,
    db: Database,
    ssid: str,
    password: str,
    manual_host: str | None = None,
    display_name: str = "Desk-Mate ESP32-C6",
    trusted_time: int | None = None,
) -> tuple[dict[str, Any], str, str]:
    if cfg.server is None:
        raise ProvisioningError("server identity is missing")
    device_id = str(__import__("uuid").uuid4())
    token = generate_device_token()
    if not TOKEN_RE.match(token):
        raise ProvisioningError("internal token generation failed")
    ca_pem = cfg.server.ca_pem
    if "BEGIN CERTIFICATE" not in ca_pem:
        raise ProvisioningError("CA certificate is invalid")
    bundle = {
        "schema_version": 1,
        "device_id": device_id,
        "server_id": cfg.server.server_id,
        "wifi": {"ssid": ssid, "password": password},
        "server": {
            "ca_pem": ca_pem,
            "tls_name": cfg.server.tls_name,
            "display_name": __import__("socket").gethostname().split(".")[0][:32],
            "port": cfg.port,
            "manual_host": manual_host or "",
        },
        "device_token": token,
        "trusted_time_unix_s": int(trusted_time if trusted_time is not None else utc_now()),
    }
    encoded = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BUNDLE:
        raise ProvisioningError(f"bundle is {len(encoded)} bytes, exceeds {MAX_BUNDLE}")
    return bundle, device_id, token


def _read_line(port: serial.Serial, timeout: float) -> str:
    deadline = time.time() + timeout
    buf = bytearray()
    while time.time() < deadline:
        chunk = port.readline()
        if chunk:
            buf.extend(chunk)
            if buf.endswith(b"\n"):
                return buf.decode("utf-8", "replace").strip()
        else:
            time.sleep(0.05)
    return buf.decode("utf-8", "replace").strip()


def send_bundle(port_name: str, bundle: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    encoded = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BUNDLE:
        raise ProvisioningError("bundle too large")
    try:
        port = serial.Serial(None, 115200, timeout=0.3, write_timeout=5, dsrdtr=False, rtscts=False)
        port.dtr = False
        port.rts = False
        port.port = port_name
        port.open()
    except serial.SerialException as exc:
        raise ProvisioningError(f"cannot open {port_name}: {exc}") from exc
    try:
        time.sleep(0.4)
        port.reset_input_buffer()
        port.write(SETUP_COMMAND)
        port.flush()
        deadline = time.time() + timeout
        ready = False
        while time.time() < deadline:
            line = _read_line(port, 1.5)
            if not line:
                # The device may have rebooted when the port opened. Retry the
                # setup request once the boot banner has settled.
                port.write(SETUP_COMMAND)
                port.flush()
                continue
            if SETUP_READY in line:
                ready = True
                break
            if ERR_PREFIX in line:
                raise ProvisioningError(line)
        if not ready:
            raise ProvisioningError("device did not enter USB setup mode (hold the device awake and retry)")
        port.write(f"VA_BUNDLE {len(encoded)}\n".encode())
        port.flush()
        # USB CDC has a small RX ring on the device; pacing avoids a burst
        # overrun while the single reader task drains it.
        for offset in range(0, len(encoded), 48):
            port.write(encoded[offset:offset + 48])
            port.flush()
            time.sleep(0.008)
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = _read_line(port, 1.0)
            if line.startswith(OK_PREFIX):
                parts = line.split()
                return {"ok": True, "device_id": parts[1] if len(parts) > 1 else bundle["device_id"],
                        "server_id": parts[2] if len(parts) > 2 else bundle["server_id"],
                        "message": line}
            if line.startswith(ERR_PREFIX):
                raise ProvisioningError(line)
        raise ProvisioningError("timed out waiting for device provisioning acknowledgement")
    finally:
        port.close()


def pair_device(
    cfg: AppConfig,
    db: Database,
    port_name: str,
    ssid: str,
    password: str,
    manual_host: str | None = None,
    display_name: str = "Desk-Mate ESP32-C6",
) -> dict[str, Any]:
    bundle, device_id, token = build_bundle(cfg, db, ssid, password, manual_host, display_name)
    ack = send_bundle(port_name, bundle)
    # Persist the token hash only after the device acknowledged the bundle.
    # token_hash is the only server-side copy of the secret.
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO devices(id, token_hash, created_at, display_name) VALUES (?,?,?,?)",
            (device_id, __import__("hashlib").sha256(token.encode()).hexdigest(), utc_now(), display_name),
        )
    summary = {
        "device_id": device_id,
        "server_id": cfg.server.server_id if cfg.server else None,
        "tls_name": cfg.server.tls_name if cfg.server else None,
        "port": cfg.port,
        "ssid": ssid,
        "manual_host": manual_host or "",
        "provisioned_at": utc_now(),
        "ack": ack,
    }
    private_summary = cfg.private_dir / "last-provision.json"
    from .config import write_private
    write_private(private_summary, json.dumps(summary, indent=2, ensure_ascii=False).encode(), 0o600)
    return summary
