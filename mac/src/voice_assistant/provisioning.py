"""USB provisioning: bounded config bundle, serial handshake and token creation."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import serial
from serial.tools import list_ports

from .config import AppConfig, generate_device_token, utc_now, write_private
from .storage import Database
from .usb_serial import open_provisioning_port, usb_port_identity

MAX_BUNDLE = 16 * 1024
SETUP_COMMAND = b"VA_SETUP\n"
SETUP_READY = "VA_SETUP_READY"
OK_PREFIX = "VA_OK"
ERR_PREFIX = "VA_ERR"
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


class ProvisioningError(RuntimeError):
    def __init__(self, message: str, code: str = "USB_PAIR_FAILED"):
        super().__init__(message)
        self.code = code


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
        port = open_provisioning_port(port_name)
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
                if (len(parts) != 3 or parts[0] != OK_PREFIX
                        or parts[1] != bundle["device_id"] or parts[2] != bundle["server_id"]):
                    raise ProvisioningError("device provisioning acknowledgement does not match the requested identity")
                return {"ok": True, "device_id": parts[1], "server_id": parts[2],
                        "message": line}
            if line.startswith(ERR_PREFIX):
                raise ProvisioningError(line)
        raise ProvisioningError("timed out waiting for device provisioning acknowledgement")
    finally:
        port.close()


def _usb_registration(db: Database, identity: str | None, server_id: str) -> dict[str, Any] | None:
    """Read durable local history, not proof of the device's present consent."""
    if identity is None:
        return None
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT device_id, provisioned_at FROM usb_registrations WHERE usb_identity=? AND server_id=?",
                (identity, server_id),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ProvisioningError("本机 USB 授权记录无法读取；未打开串口或修改设备授权",
                                "USB_AUTH_HISTORY_INVALID") from exc
    return dict(row) if row is not None else None


def pair_device(
    cfg: AppConfig,
    db: Database,
    port_name: str,
    ssid: str,
    password: str,
    manual_host: str | None = None,
    display_name: str = "Desk-Mate ESP32-C6",
    *,
    force: bool = False,
) -> dict[str, Any]:
    if cfg.server is None:
        raise ProvisioningError("server identity is missing")
    identity = usb_port_identity(port_name)
    server_id = cfg.server.server_id
    previous = _usb_registration(db, identity, server_id)
    if previous and not force and db.device_exists(previous["device_id"]):
        # No serial open and no new secret: this is historical local evidence,
        # not proof that the current firmware still possesses its credential.
        return {
            "device_id": previous["device_id"], "server_id": server_id,
            "tls_name": cfg.server.tls_name, "port": cfg.port,
            "provisioned_at": previous["provisioned_at"], "usb_identity": identity,
            "unchanged": True, "authorization_verified": False,
        }
    if db.active_command():
        raise ProvisioningError("正在处理语音任务，请结束当前录音或执行后再授权", "COMMAND_BUSY")
    bundle, device_id, token = build_bundle(cfg, db, ssid, password, manual_host, display_name)
    ack = send_bundle(port_name, bundle)
    if (ack.get("ok") is not True or ack.get("device_id") != device_id
            or ack.get("server_id") != server_id):
        raise ProvisioningError("device provisioning acknowledgement does not match the requested identity")
    provisioned_at = utc_now()
    # Persist the token hash only after the device acknowledged the bundle.
    # The USB mapping and replacement belong to the same transaction. A
    # diagnostic file must never become authoritative after this commit.
    try:
        with db.transaction() as tx:
            current = tx.execute(
                "SELECT device_id FROM usb_registrations WHERE usb_identity=? AND server_id=?",
                (identity, server_id),
            ).fetchone() if identity else None
            tx.execute(
                "INSERT INTO devices(id, token_hash, created_at, display_name) VALUES (?,?,?,?)",
                (device_id, hashlib.sha256(token.encode()).hexdigest(), provisioned_at, display_name),
            )
            if current:
                # The mapping is scoped to this physical USB board and server.
                # Do not revoke other boards or another server's history.
                tx.execute("UPDATE devices SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                           (provisioned_at, current["device_id"]))
            if identity:
                tx.execute(
                    """INSERT INTO usb_registrations(usb_identity, server_id, device_id, provisioned_at)
                       VALUES (?,?,?,?) ON CONFLICT(usb_identity, server_id) DO UPDATE SET
                       device_id=excluded.device_id, provisioned_at=excluded.provisioned_at""",
                    (identity, server_id, device_id, provisioned_at),
                )
    except sqlite3.Error as exc:
        # SQLite can roll back its own changes, but the board already ACKed
        # the new secret. Preserve that distinction in the recovery guidance.
        raise ProvisioningError(
            "设备已接收新凭据，但本机记录未保存；请修复存储后，到高级设置中重新授权",
            "USB_AUTH_SAVE_FAILED",
        ) from exc
    summary = {
        "device_id": device_id,
        "server_id": server_id,
        "tls_name": cfg.server.tls_name,
        "port": cfg.port,
        "ssid": ssid,
        "manual_host": manual_host or "",
        "provisioned_at": provisioned_at,
        "ack": ack,
        "usb_identity": identity,
        "unchanged": False,
        "authorization_verified": False,
    }
    private_summary = cfg.private_dir / "last-provision.json"
    try:
        write_private(private_summary, json.dumps(summary, indent=2, ensure_ascii=False).encode(), 0o600)
    except OSError:
        # The authoritative SQLite transaction already succeeded. Reporting
        # this as authorization failure would invite another secret rotation.
        summary["warning_code"] = "USB_SUMMARY_NOT_SAVED"
        summary["warning"] = "授权已保存，但诊断摘要未能保存；无需重复授权"
    return summary
