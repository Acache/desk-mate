"""USB registration history uses real SQLite without opening any device port."""
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from voice_assistant.config import AppConfig
from voice_assistant.storage import Database
from voice_assistant import provisioning as provision


USB_A = "a" * 64
USB_B = "b" * 64


@pytest.fixture
def registration(tmp_path, monkeypatch):
    cfg = AppConfig(root=tmp_path)
    cfg.server = SimpleNamespace(
        server_id=str(uuid4()), tls_name="voice.local",
        ca_pem="-----BEGIN CERTIFICATE-----test-----END CERTIFICATE-----",
    )
    cfg.ensure_layout()
    db = Database(cfg.db_path)
    sent = []
    monkeypatch.setattr(provision, "usb_port_identity", lambda port: USB_A if port == "board-a" else USB_B, raising=False)

    def send(port, bundle):
        sent.append((port, bundle))
        return {"ok": True, "device_id": bundle["device_id"],
                "server_id": bundle["server_id"], "message": "VA_OK"}

    monkeypatch.setattr(provision, "send_bundle", send)
    yield cfg, db, sent
    db.close()


def history(db):
    with db._lock:
        rows = db._conn.execute("SELECT * FROM usb_registrations ORDER BY usb_identity,server_id").fetchall()
    registrations = {}
    for row in rows:
        registrations.setdefault(row["usb_identity"], {})[row["server_id"]] = {
            "device_id": row["device_id"], "provisioned_at": row["provisioned_at"],
        }
    return {"registrations": registrations}


def test_first_registration_persists_private_history_without_token(registration):
    cfg, db, sent = registration
    result = provision.pair_device(cfg, db, "board-a", "Home", "wifi-secret")
    assert result["unchanged"] is False
    assert result["authorization_verified"] is False
    assert db.authenticate_device(result["device_id"], sent[0][1]["device_token"])
    record = history(db)["registrations"][USB_A][cfg.server.server_id]
    assert record["device_id"] == result["device_id"]
    assert record["provisioned_at"] == result["provisioned_at"]
    summary = cfg.private_dir / "last-provision.json"
    assert summary.stat().st_mode & 0o777 == 0o600
    assert sent[0][1]["device_token"] not in summary.read_text()
    assert "wifi-secret" not in summary.read_text()
    assert sent[0][1]["device_token"] not in json.dumps(history(db))
    assert not (cfg.private_dir / "usb-authorizations.json").exists()


def test_repeat_is_history_only_without_opening_port_or_rotating_token(registration, monkeypatch):
    cfg, db, sent = registration
    first = provision.pair_device(cfg, db, "board-a", "", "")
    rows = db.list_devices()
    before = history(db)
    monkeypatch.setattr(provision, "build_bundle", lambda *a, **kw: pytest.fail("must not generate credentials"))
    monkeypatch.setattr(provision, "send_bundle", lambda *a, **kw: pytest.fail("must not open USB"))
    second = provision.pair_device(cfg, db, "board-a", "", "")
    assert second["unchanged"] is True and second["authorization_verified"] is False
    assert second["device_id"] == first["device_id"]
    assert second["server_id"] == cfg.server.server_id
    assert second["provisioned_at"] == first["provisioned_at"]
    assert db.list_devices() == rows
    assert history(db) == before
    assert len(sent) == 1


def test_another_usb_board_can_be_added_without_disturbing_first(registration):
    cfg, db, sent = registration
    first = provision.pair_device(cfg, db, "board-a", "", "")
    second = provision.pair_device(cfg, db, "board-b", "", "")
    assert second["device_id"] != first["device_id"] and len(sent) == 2
    assert db.device_exists(first["device_id"]) and db.device_exists(second["device_id"])
    assert set(history(db)["registrations"]) == {USB_A, USB_B}


def test_explicit_reauthorization_revokes_only_the_same_board_and_server(registration):
    cfg, db, sent = registration
    original_server = cfg.server.server_id
    old = provision.pair_device(cfg, db, "board-a", "", "")
    other_board = provision.pair_device(cfg, db, "board-b", "", "")
    cfg.server.server_id = str(uuid4())
    other_server = provision.pair_device(cfg, db, "board-a", "", "")
    cfg.server.server_id = original_server
    replaced = provision.pair_device(cfg, db, "board-a", "", "", force=True)
    assert replaced["unchanged"] is False and replaced["device_id"] != old["device_id"]
    assert not db.device_exists(old["device_id"])
    assert db.device_exists(replaced["device_id"])
    assert db.device_exists(other_board["device_id"])
    assert db.device_exists(other_server["device_id"])
    assert len(history(db)["registrations"][USB_A]) == 2


def test_revoked_registration_can_be_recreated(registration):
    cfg, db, sent = registration
    first = provision.pair_device(cfg, db, "board-a", "", "")
    db.revoke_device(first["device_id"])
    second = provision.pair_device(cfg, db, "board-a", "", "")
    assert second["unchanged"] is False and second["device_id"] != first["device_id"]
    assert db.device_exists(second["device_id"]) and len(sent) == 2


def test_failed_ack_leaves_credentials_and_history_untouched(registration, monkeypatch):
    cfg, db, sent = registration
    first = provision.pair_device(cfg, db, "board-a", "", "")
    rows = db.list_devices()
    before = history(db)
    last = (cfg.private_dir / "last-provision.json").read_bytes()

    def fail(*args, **kwargs):
        raise provision.ProvisioningError("VA_ERR CONFIG")

    monkeypatch.setattr(provision, "send_bundle", fail)
    with pytest.raises(provision.ProvisioningError, match="VA_ERR CONFIG"):
        provision.pair_device(cfg, db, "board-a", "", "", force=True)
    assert db.device_exists(first["device_id"]) and db.list_devices() == rows
    assert history(db) == before
    assert (cfg.private_dir / "last-provision.json").read_bytes() == last


def test_mismatched_ack_never_creates_authorization(registration, monkeypatch):
    cfg, db, sent = registration
    monkeypatch.setattr(provision, "send_bundle", lambda port, bundle: {
        "ok": True, "device_id": str(uuid4()), "server_id": bundle["server_id"],
    })
    with pytest.raises(provision.ProvisioningError, match="acknowledgement"):
        provision.pair_device(cfg, db, "board-a", "", "")
    assert db.list_devices() == []
    assert history(db) == {"registrations": {}}


def test_busy_command_blocks_new_credentials_but_not_history_lookup(registration, monkeypatch):
    cfg, db, sent = registration
    first = provision.pair_device(cfg, db, "board-a", "", "")
    monkeypatch.setattr(db, "active_command", lambda: {"state": "RECEIVING"})
    assert provision.pair_device(cfg, db, "board-a", "", "")["device_id"] == first["device_id"]
    with pytest.raises(provision.ProvisioningError, match="语音"):
        provision.pair_device(cfg, db, "board-b", "", "")
    with pytest.raises(provision.ProvisioningError, match="语音"):
        provision.pair_device(cfg, db, "board-a", "", "", force=True)
    assert len(sent) == 1


def test_broken_registry_is_not_replaced_with_empty_file_history(registration):
    cfg, db, sent = registration
    with db.transaction() as tx:
        tx.execute("DROP TABLE usb_registrations")
    with pytest.raises(provision.ProvisioningError) as error:
        provision.pair_device(cfg, db, "board-a", "", "")
    assert error.value.code == "USB_AUTH_HISTORY_INVALID"
    assert isinstance(error.value.__cause__, sqlite3.OperationalError)
    assert "usb_registrations" in str(error.value.__cause__)
    assert db.list_devices() == [] and not sent
    assert not (cfg.private_dir / "usb-authorizations.json").exists()


def test_unknown_usb_identity_is_not_misrepresented_as_known_history(registration, monkeypatch):
    cfg, db, sent = registration
    monkeypatch.setattr(provision, "usb_port_identity", lambda port: None)
    first = provision.pair_device(cfg, db, "board-a", "", "")
    second = provision.pair_device(cfg, db, "board-a", "", "")
    assert first["device_id"] != second["device_id"] and len(sent) == 2
    assert first["unchanged"] is False and second["unchanged"] is False
    assert history(db) == {"registrations": {}}


def test_send_bundle_uses_safe_factory_and_checks_ack(monkeypatch):
    device_id, server_id = str(uuid4()), str(uuid4())

    class Port:
        closed = False
        def __init__(self):
            self.lines = iter([b"VA_SETUP_READY\n", f"VA_OK {device_id} {server_id}\n".encode()])
            self.writes = []
        def readline(self): return next(self.lines)
        def reset_input_buffer(self): pass
        def write(self, value): self.writes.append(value)
        def flush(self): pass
        def close(self): self.closed = True

    port = Port()
    opened = []
    monkeypatch.setattr(provision, "open_provisioning_port", lambda name: opened.append(name) or port, raising=False)
    monkeypatch.setattr(provision.time, "sleep", lambda value: None)
    # The legacy constructor must not be used by the protocol path.
    monkeypatch.setattr(provision.serial, "Serial", lambda *a, **kw: pytest.fail("unsafe legacy open"))
    bundle = {"device_id": device_id, "server_id": server_id}
    ack = provision.send_bundle("board-a", bundle)
    assert ack["device_id"] == device_id and ack["server_id"] == server_id
    assert opened == ["board-a"] and port.closed
    assert port.writes[0] == b"VA_SETUP\n"
    assert b"".join(port.writes[2:]) == json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.mark.parametrize("bad_ack", ["VA_OK", "VA_OK wrong-device wrong-server", "VA_OKAY wrong-device wrong-server"])
def test_bad_serial_ack_is_rejected_and_port_is_closed(monkeypatch, bad_ack):
    class Port:
        closed = False
        def __init__(self): self.lines = iter([b"VA_SETUP_READY\n", (bad_ack + "\n").encode()])
        def readline(self): return next(self.lines)
        def reset_input_buffer(self): pass
        def write(self, value): pass
        def flush(self): pass
        def close(self): self.closed = True

    port = Port()
    monkeypatch.setattr(provision, "open_provisioning_port", lambda name: port)
    monkeypatch.setattr(provision.time, "sleep", lambda value: None)
    with pytest.raises(provision.ProvisioningError, match="acknowledgement"):
        provision.send_bundle("board-a", {"device_id": str(uuid4()), "server_id": str(uuid4())})
    assert port.closed


def test_safe_open_failure_is_not_retried_with_legacy_serial(monkeypatch):
    def unsupported(name):
        raise provision.serial.SerialException("atomic control unavailable")
    monkeypatch.setattr(provision, "open_provisioning_port", unsupported)
    monkeypatch.setattr(provision.serial, "Serial", lambda *a, **kw: pytest.fail("must not fall back to unsafe open"))
    with pytest.raises(provision.ProvisioningError, match="atomic control unavailable"):
        provision.send_bundle("board-a", {})


def test_legacy_file_is_not_read_as_authoritative_registration(registration, tmp_path):
    cfg, db, sent = registration
    target = tmp_path / "unrelated.json"
    target.write_text("untouched")
    path = cfg.private_dir / "usb-authorizations.json"
    path.symlink_to(target)
    result = provision.pair_device(cfg, db, "board-a", "", "")
    assert path.is_symlink() and target.read_text() == "untouched"
    assert result["unchanged"] is False and len(sent) == 1
    assert history(db)["registrations"][USB_A][cfg.server.server_id]["device_id"] == result["device_id"]


def test_summary_failure_is_a_warning_and_repeat_does_not_rotate_again(registration, monkeypatch):
    cfg, db, sent = registration
    old = provision.pair_device(cfg, db, "board-a", "", "")
    other = provision.pair_device(cfg, db, "board-b", "", "")

    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(provision, "write_private", disk_full)
    replaced = provision.pair_device(cfg, db, "board-a", "", "", force=True)
    assert replaced["warning_code"] == "USB_SUMMARY_NOT_SAVED"
    assert replaced["unchanged"] is False
    assert not db.device_exists(old["device_id"])
    assert db.device_exists(replaced["device_id"]) and db.device_exists(other["device_id"])
    assert history(db)["registrations"][USB_A][cfg.server.server_id]["device_id"] == replaced["device_id"]
    count = len(db.list_devices())
    monkeypatch.setattr(provision, "build_bundle", lambda *a, **kw: pytest.fail("must not make another key"))
    monkeypatch.setattr(provision, "send_bundle", lambda *a, **kw: pytest.fail("must not touch USB again"))
    again = provision.pair_device(cfg, db, "board-a", "", "")
    assert again["unchanged"] is True and again["device_id"] == replaced["device_id"]
    assert len(db.list_devices()) == count and len(sent) == 3


def test_registry_write_failure_rolls_back_new_key_and_old_revocation(registration):
    cfg, db, sent = registration
    first = provision.pair_device(cfg, db, "board-a", "", "")
    rows, before = db.list_devices(), history(db)
    with db.transaction() as tx:
        tx.execute("""CREATE TRIGGER reject_registry_update BEFORE UPDATE ON usb_registrations
                      BEGIN SELECT RAISE(ABORT, 'registry write rejected'); END""")
    with pytest.raises(provision.ProvisioningError, match="设备已接收新凭据") as error:
        provision.pair_device(cfg, db, "board-a", "", "", force=True)
    assert error.value.code == "USB_AUTH_SAVE_FAILED"
    assert isinstance(error.value.__cause__, sqlite3.IntegrityError)
    assert db.list_devices() == rows and history(db) == before
    assert db.device_exists(first["device_id"]) and not db._conn.in_transaction


def test_commit_failure_rolls_back_and_connection_can_retry(registration):
    cfg, db, sent = registration
    first = provision.pair_device(cfg, db, "board-a", "", "")
    rows, before = db.list_devices(), history(db)

    def reject_commit(action, arg1, arg2, database, source):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db._conn.set_authorizer(reject_commit)
    try:
        with pytest.raises(provision.ProvisioningError, match="设备已接收新凭据") as error:
            provision.pair_device(cfg, db, "board-a", "", "", force=True)
        assert error.value.code == "USB_AUTH_SAVE_FAILED"
        assert isinstance(error.value.__cause__, sqlite3.DatabaseError)
    finally:
        db._conn.set_authorizer(None)
    assert not db._conn.in_transaction
    assert db.list_devices() == rows and history(db) == before
    assert db.device_exists(first["device_id"])
    replaced = provision.pair_device(cfg, db, "board-a", "", "", force=True)
    assert db.device_exists(replaced["device_id"]) and not db.device_exists(first["device_id"])
    assert len([row for row in db.list_devices() if row["revoked_at"] is None]) == 1


def test_unreadable_registration_is_not_misreported_as_usb_failure(registration):
    cfg, db, sent = registration
    def reject_history_read(action, table, column, database, source):
        return (sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'usb_registrations'
                else sqlite3.SQLITE_OK)
    db._conn.set_authorizer(reject_history_read)
    try:
        with pytest.raises(provision.ProvisioningError) as error:
            provision.pair_device(cfg, db, 'board-a', '', '')
        assert error.value.code == 'USB_AUTH_HISTORY_INVALID'
        assert not sent
    finally:
        db._conn.set_authorizer(None)
    assert not db.list_devices()
