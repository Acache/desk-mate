"""Runtime configuration, private data root and TLS server identity."""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
import stat
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "voice-assistant"
DEFAULT_PORT = 8765
APP_ALLOWLIST_DEFAULT = {
    "bilibili": "com.bilibili.bilibiliPC",
    "vscode": "com.microsoft.VSCode",
    "terminal": "com.apple.Terminal",
    "chrome": "com.google.Chrome",
    "safari": "com.apple.Safari",
}

BILIBILI_BUNDLE_ID = "com.bilibili.bilibiliPC"


def utc_now() -> float:
    return datetime.now(timezone.utc).timestamp()


def unix_to_iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def data_root() -> Path:
    raw = os.environ.get("VOICE_ASSISTANT_DATA_DIR")
    root = Path(raw).expanduser() if raw else DEFAULT_DATA_DIR
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(root, 0o700)
    except PermissionError:
        pass
    return root


def secure_makedirs(path: Path, mode: int = 0o700) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    try:
        os.chmod(path, mode)
    except PermissionError:
        pass


def write_private(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    try:
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def lan_ip() -> str | None:
    """Best-effort primary LAN IP (no packets are sent)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return None


@dataclass
class ServerIdentity:
    server_id: str
    tls_name: str
    ca_cert_path: Path
    ca_key_path: Path
    server_cert_path: Path
    server_key_path: Path
    port: int = DEFAULT_PORT

    @property
    def ca_pem(self) -> str:
        return self.ca_cert_path.read_text(encoding="utf-8")

    @property
    def cert_pem(self) -> str:
        return self.server_cert_path.read_text(encoding="utf-8")

    def summary(self) -> dict[str, Any]:
        cert = x509.load_pem_x509_certificate(self.server_cert_path.read_bytes())
        return {
            "server_id": self.server_id,
            "tls_name": self.tls_name,
            "port": self.port,
            "not_valid_before": cert.not_valid_before_utc.isoformat(),
            "not_valid_after": cert.not_valid_after_utc.isoformat(),
            "ca_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        }


@dataclass
class AppConfig:
    root: Path
    port: int = DEFAULT_PORT
    server: ServerIdentity | None = None
    app_allowlist: dict[str, str] = field(default_factory=lambda: dict(APP_ALLOWLIST_DEFAULT))
    retention_days_success: int = 1825
    retention_days_failure: int = 1825
    audio_quota_bytes: int = 16 * 1024 * 1024 * 1024
    stt_backend: str = "auto"
    ollama_model: str = "qwen2.5:1.5b"
    ollama_url: str = "http://127.0.0.1:11434"
    ready: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.root / "learning.db"

    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def private_dir(self) -> Path:
        return self.root / "private"

    @property
    def staging_dir(self) -> Path:
        return self.root / "staging"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def screenshots_dir(self) -> Path:
        return self.root / "screenshots"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def ensure_layout(self) -> None:
        for path, mode in (
            (self.root, 0o700),
            (self.private_dir, 0o700),
            (self.staging_dir, 0o700),
            (self.audio_dir, 0o700),
            (self.screenshots_dir, 0o700),
            (self.logs_dir, 0o700),
        ):
            secure_makedirs(path, mode)

    def save(self) -> None:
        data = {
            "schema_version": 1,
            "server_id": self.server.server_id if self.server else None,
            "tls_name": self.server.tls_name if self.server else None,
            "port": self.port,
            "app_allowlist": self.app_allowlist,
            "retention_days_success": self.retention_days_success,
            "retention_days_failure": self.retention_days_failure,
            "audio_quota_bytes": self.audio_quota_bytes,
            "stt_backend": self.stt_backend,
            "ollama_model": self.ollama_model,
            "ollama_url": self.ollama_url,
            "ready": self.ready,
            "extra": self.extra,
        }
        write_private(self.config_path, json.dumps(data, indent=2, ensure_ascii=False).encode(), 0o600)


def _new_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem_key(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    write_private(path, cert.public_bytes(serialization.Encoding.PEM), 0o644)


def generate_identity(root: Path, port: int = DEFAULT_PORT, force: bool = False) -> ServerIdentity:
    """Create or load the application CA and server certificate.

    The CA private key never leaves the private/ directory (0600). The server
    certificate SAN contains the stable tls_name and the current LAN IP as a
    convenience, but the device is required to verify the tls_name.
    """
    priv = root / "private"
    secure_makedirs(priv, 0o700)
    ca_key_path = priv / "ca.key"
    ca_cert_path = priv / "ca.pem"
    srv_key_path = priv / "server.key"
    srv_cert_path = priv / "server.pem"
    meta_path = priv / "identity.json"
    if meta_path.exists() and not force:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return ServerIdentity(
            server_id=meta["server_id"],
            tls_name=meta["tls_name"],
            ca_cert_path=ca_cert_path,
            ca_key_path=ca_key_path,
            server_cert_path=srv_cert_path,
            server_key_path=srv_key_path,
            port=int(meta.get("port", port)),
        )
    if force:
        for p in (ca_key_path, ca_cert_path, srv_key_path, srv_cert_path, meta_path):
            if p.exists():
                p.unlink()

    server_id = str(uuid.uuid4())
    tls_name = f"va-{server_id.split('-')[0]}.local"
    now = datetime.now(timezone.utc)
    ca_key = _new_rsa_key()
    ca_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Desk-Mate Voice Assistant Private CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Desk-Mate Local"),
    ])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    write_private(ca_key_path, _pem_key(ca_key), 0o600)
    _write_cert(ca_cert_path, ca_cert)

    srv_key = _new_rsa_key()
    san = [x509.DNSName(tls_name), x509.DNSName("localhost")]
    ip = lan_ip()
    if ip:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    server_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, tls_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Desk-Mate Local"),
    ])
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    write_private(srv_key_path, _pem_key(srv_key), 0o600)
    _write_cert(srv_cert_path, srv_cert)
    meta = {
        "server_id": server_id,
        "tls_name": tls_name,
        "port": port,
        "created_at": now.isoformat(),
        "ca_fingerprint_sha256": ca_cert.fingerprint(hashes.SHA256()).hex(),
        "server_fingerprint_sha256": srv_cert.fingerprint(hashes.SHA256()).hex(),
    }
    write_private(meta_path, json.dumps(meta, indent=2).encode(), 0o600)
    return ServerIdentity(server_id, tls_name, ca_cert_path, ca_key_path, srv_cert_path, srv_key_path, port)


def load_config(root: Path | None = None, create: bool = True) -> AppConfig:
    root = (root or data_root()).resolve()
    cfg = AppConfig(root=root)
    paths = {
        "config.json": root / "config.json",
        "private/identity.json": root / "private" / "identity.json",
    }
    if paths["config.json"].exists():
        data = json.loads(paths["config.json"].read_text(encoding="utf-8"))
        cfg.port = int(data.get("port", DEFAULT_PORT))
        cfg.app_allowlist = dict(data.get("app_allowlist", APP_ALLOWLIST_DEFAULT))
        cfg.retention_days_success = int(data.get("retention_days_success", 1825))
        cfg.retention_days_failure = int(data.get("retention_days_failure", 1825))
        cfg.audio_quota_bytes = int(data.get("audio_quota_bytes", 16 * 1024 * 1024 * 1024))
        cfg.stt_backend = data.get("stt_backend", "auto")
        cfg.ollama_model = data.get("ollama_model", "qwen2.5:1.5b")
        cfg.ollama_url = data.get("ollama_url", "http://127.0.0.1:11434")
        cfg.ready = bool(data.get("ready", True))
        cfg.extra = dict(data.get("extra", {}))
    cfg.ensure_layout()
    # Bundle supervisor supplies its own loopback inference service. Do not
    # persist a developer machine's runtime paths into portable configuration.
    cfg.ollama_url = os.environ.get("VOICE_ASSISTANT_OLLAMA_URL", cfg.ollama_url)
    cfg.ollama_model = os.environ.get("VOICE_ASSISTANT_OLLAMA_MODEL", cfg.ollama_model)
    if create:
        cfg.server = generate_identity(root, cfg.port)
        if not paths["config.json"].exists():
            cfg.save()
    return cfg


def server_identity_paths(root: Path) -> dict[str, Path]:
    priv = root / "private"
    return {
        "ca": priv / "ca.pem",
        "ca_key": priv / "ca.key",
        "cert": priv / "server.pem",
        "key": priv / "server.key",
        "meta": priv / "identity.json",
    }


def generate_device_token() -> str:
    return secrets.token_hex(32)
