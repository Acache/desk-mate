"""Schema 2 upgrades retain every existing table and its archived data."""
import sqlite3

import pytest

from voice_assistant import storage


def test_schema2_upgrade_preserves_all_prior_tables_and_rows(tmp_path, monkeypatch):
    path = tmp_path / "learning.db"
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 2)
    old = storage.Database(path)
    device, _ = old.register_device("existing board", token="a" * 64)
    command = "11111111-1111-4111-8111-111111111111"
    records = {
        "commands": dict(id=command, device_id=device, boot_id="boot", kind="audio", source="esp32_ptt",
                         state="SUCCEEDED", created_at=1, updated_at=2, raw_text="原始语音", normalized_text="原始语音", summary="完成"),
        "action_steps": dict(command_id=command, step_index=0, action="bilibili.open", state="SUCCEEDED", output_json='{"kept":true}'),
        "audio_samples": dict(command_id=command, relative_path="audio/kept.wav", hash="b" * 64,
                              size_bytes=32000, sample_rate=16000, channels=1, bit_depth=16,
                              duration_ms=1000, quality_label="good"),
        "feedback": dict(id="feedback", command_id=command, kind="explicit_success", created_at=3,
                         rating="good", reason="保留反馈", context_json='{"page":"home"}'),
        "preferences": dict(key="kept", value_json='{"theme":"dark"}', source="manual", updated_at=4),
        "model_versions": dict(id="model", model_digest="kept-model", manifest_json='{"kept":true}', lifecycle="approved"),
        "training_runs": dict(id="training", model_version_id="model", dataset_hash="e" * 64,
                              started_at=5, status="complete", metrics_json='{"kept":1}'),
        "command_tombstones": dict(id="removed-command", device_id=device, immutable_hash="c" * 64,
                                   terminal_state="SUCCEEDED", completed_at=6),
        "snapshots": dict(id="snapshot", kind="player", device_id=device, command_id=command,
                          app_id="bilibili", generation="kept-generation", observed_at=7, expires_at=8, payload_json='{"paused":false}'),
        "confirmations": dict(id="confirmation", command_id=command, device_id=device,
                              frozen_plan_json='{"actions":[]}', plan_hash="d" * 64, expires_at=9, status="expired"),
        "account_rate_limits": dict(account_id="account", reserved_command_id=command,
                                    reserved_step_index=0, last_attempt_at=10, blocked_until=11),
        "device_sessions": dict(boot_id="boot", device_id=device, firmware_version="existing-firmware",
                                first_seen_at=12, last_seen_at=13),
        "feedback_analysis_runs": dict(id="analysis", created_at=14, groups=1, explicit_labels=1,
                                       model="existing-model", report_json='{"kept":true}', status="complete"),
    }
    with old.transaction() as tx:
        for table, values in records.items():
            fields = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            tx.execute(f"INSERT INTO {table}({fields}) VALUES ({placeholders})", tuple(values.values()))
    tables = [row[0] for row in old._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    assert set(tables) == {"schema_migrations", "devices", *records}
    before = {table: [tuple(row) for row in old._conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
              for table in tables}
    columns = {table: [tuple(row) for row in old._conn.execute(f"PRAGMA table_info({table})")]
               for table in tables}
    old.close()

    monkeypatch.setattr(storage, "SCHEMA_VERSION", 3)
    upgraded = storage.Database(path)
    try:
        for table in tables:
            rows = [tuple(row) for row in upgraded._conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            assert (rows[:2] if table == "schema_migrations" else rows) == before[table]
            assert [tuple(row) for row in upgraded._conn.execute(f"PRAGMA table_info({table})")] == columns[table]
        assert upgraded._conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3
        assert upgraded._conn.execute("SELECT COUNT(*) FROM usb_registrations").fetchone()[0] == 0
        assert upgraded.authenticate_device(device, "a" * 64)
        assert upgraded._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        upgraded.close()


def test_registry_foreign_key_and_hardware_identity_constraints(tmp_path):
    db = storage.Database(tmp_path / "learning.db")
    try:
        device, _ = db.register_device("board")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with db.transaction() as tx:
                tx.execute("INSERT INTO usb_registrations VALUES (?,?,?,?)", ("a" * 64, "server", "missing", 1))
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            with db.transaction() as tx:
                tx.execute("INSERT INTO usb_registrations VALUES (?,?,?,?)", ("not hardware", "server", device, 1))
        assert db._conn.execute("SELECT COUNT(*) FROM usb_registrations").fetchone()[0] == 0
        assert not db._conn.in_transaction
    finally:
        db.close()
