from __future__ import annotations

import hashlib
import json
import struct
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from voice_assistant.config import load_config  # noqa: E402
from voice_assistant.contracts import CommandState, ErrorCode, StepState  # noqa: E402
from voice_assistant.sessions import CommandService, SessionError, StageStore  # noqa: E402
from voice_assistant.storage import ConflictError, Database  # noqa: E402

CID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture()
def env(tmp_path: Path):
    cfg = load_config(tmp_path)
    db = Database(cfg.db_path)
    device_id, token = db.register_device("test device")
    stages = StageStore(cfg, db)
    service = CommandService(cfg, db, stages)
    yield cfg, db, device_id, stages, service
    db.close()


def frame(seq: int, n: int = 640) -> bytes:
    pcm = bytes([seq % 256, (seq * 7) % 256]) * (n // 2)
    return struct.pack("<IHH", seq, n, 0) + pcm


def test_duplicate_id_and_conflict(env):
    cfg, db, device_id, stages, service = env
    stages.begin(CID, device_id, "boot")
    with pytest.raises((ConflictError, SessionError)) as exc:
        stages.begin(CID, device_id, "other-boot")
    assert getattr(exc.value, "code", None) in {"ID_CONFLICT", "BUSY", ErrorCode.ID_CONFLICT}


def test_second_active_command_busy(env):
    cfg, db, device_id, stages, service = env
    stages.begin(CID, device_id, "boot")
    with pytest.raises(ConflictError) as exc:
        stages.begin("22222222-2222-4222-8222-222222222222", device_id, "boot")
    assert exc.value.code == "BUSY"


def test_frame_sequence_and_short_frame_rules(env):
    cfg, db, device_id, stages, service = env
    stages.begin(CID, device_id, "boot")
    stages.append(CID, frame(0))
    with pytest.raises(SessionError) as exc:
        stages.append(CID, frame(2))
    assert exc.value.code == ErrorCode.AUDIO_INVALID
    with pytest.raises(SessionError):
        stages.append(CID, frame(1, 641))
    stages.append(CID, frame(1, 320))
    with pytest.raises(SessionError):
        stages.append(CID, frame(2, 320))


def test_short_frame_seal_and_commit(env):
    cfg, db, device_id, stages, service = env
    stages.begin(CID, device_id, "boot")
    frames = [frame(0), frame(1)]
    for f in frames:
        stages.append(CID, f)
    pcm = b"".join(f[8:] for f in frames)
    h = hashlib.sha256(pcm).hexdigest()
    stages.seal(CID, 2, 1280, h)
    assert db.get_state(CID) is CommandState.SEALED
    row = stages.commit(CID, h)
    assert row["state"] == "COMMITTED"
    sample = db._conn.execute("SELECT * FROM audio_samples WHERE command_id=?", (CID,)).fetchone()
    assert sample["duration_ms"] == 40
    assert sample["relative_path"].startswith("audio/")


def test_hash_mismatch_and_seal_validation(env):
    cfg, db, device_id, stages, service = env
    stages.begin(CID, device_id, "boot")
    stages.append(CID, frame(0))
    with pytest.raises(SessionError) as exc:
        stages.seal(CID, 1, 640, "0" * 64)
    assert exc.value.code == ErrorCode.HASH_MISMATCH
    pcm = frame(0)[8:]
    stages.seal(CID, 1, 640, hashlib.sha256(pcm).hexdigest())
    with pytest.raises(SessionError) as exc:
        stages.commit(CID, "1" * 64)
    assert exc.value.code == ErrorCode.HASH_MISMATCH


def test_cancel_before_execution_has_zero_effects(env):
    cfg, db, device_id, stages, service = env
    stages.begin(CID, device_id, "boot")
    stages.append(CID, frame(0))
    outcome, state = stages.cancel(CID, "user")
    assert outcome == "canceled"
    assert db.get_state(CID) is CommandState.CANCELED
    assert not list((cfg.staging_dir).glob(f"{CID}*"))


def test_cancel_after_external_action_is_too_late(env):
    cfg, db, device_id, stages, service = env
    stages.begin(CID, device_id, "boot")
    pcm = frame(0)[8:]
    stages.append(CID, frame(0))
    h = hashlib.sha256(pcm).hexdigest()
    stages.seal(CID, 1, 640, h)
    stages.commit(CID, h)
    db.transition(CID, CommandState.PLANNING)
    db.journal_plan(CID, {"actions": [{"action": "bilibili.pause", "params": {}}]}, "test")
    db.transition(CID, CommandState.EXECUTING)
    assert db.start_step(CID, 0)
    outcome, state = stages.cancel(CID, "user")
    assert outcome == "too_late"
    assert db.get_state(CID) is CommandState.EXECUTING


def test_restart_reconciliation(env):
    cfg, db, device_id, stages, service = env
    # STARTED step -> OUTCOME_UNKNOWN, never resumed
    stages.begin(CID, device_id, "boot")
    pcm = frame(0)[8:]
    stages.append(CID, frame(0))
    h = hashlib.sha256(pcm).hexdigest()
    stages.seal(CID, 1, 640, h)
    stages.commit(CID, h)
    db.transition(CID, CommandState.EXECUTING)
    db.journal_plan(CID, {"actions": [{"action": "bilibili.pause", "params": {}}]}, "test")
    db.start_step(CID, 0)
    report = db.reconcile_startup()
    assert CID in report["unknown"]
    assert db.get_state(CID) is CommandState.OUTCOME_UNKNOWN

    # SEALED -> ABANDONED
    cid2 = "22222222-2222-4222-8222-222222222222"
    stages.begin(cid2, device_id, "boot")
    stages.append(cid2, frame(0))
    stages.seal(cid2, 1, 640, hashlib.sha256(frame(0)[8:]).hexdigest())
    db.reconcile_startup()
    assert db.get_state(cid2) is CommandState.ABANDONED


def test_replay_allowlist_and_target_freshness(env):
    cfg, db, device_id, stages, service = env
    # Seed a successful pause command with a fresh player snapshot.
    stages.begin(CID, device_id, "boot")
    pcm = frame(0)[8:]
    stages.append(CID, frame(0))
    h = hashlib.sha256(pcm).hexdigest()
    stages.seal(CID, 1, 640, h)
    stages.commit(CID, h)
    db.transition(CID, CommandState.EXECUTING)
    db.journal_plan(CID, {"actions": [{"action": "bilibili.pause", "params": {}}]}, "test")
    db.finish_step(CID, 0, StepState.SUCCEEDED)
    db.finalize(CID, CommandState.SUCCEEDED, None, "paused", True)
    db.save_snapshot(
        "player", "com.bilibili.bilibiliPC", "gen-1", {"paused": False, "position_ms": 1000, "account_id": "acct"},
        1000.0, 2000.0, video_id="BV1LqJC6iEZV",
    )
    new_id = "33333333-3333-4333-8333-333333333333"
    ready = service.prepare_replay(new_id, device_id, CID, "boot", now=1500.0)
    assert ready["sha256"]
    assert db.get_state(new_id) is CommandState.SEALED
    # Replay of a non-allowlisted action is refused.
    cid2 = "44444444-4444-4444-8444-444444444444"
    db.journal_plan(CID, {"actions": [{"action": "bilibili.search", "params": {"query": "x"}}]}, "test")
    with pytest.raises(SessionError) as exc:
        service.prepare_replay("55555555-5555-4555-8555-555555555555", device_id, CID, "boot", now=1500.0)
    assert exc.value.code in {ErrorCode.CAPABILITY_UNAVAILABLE, ErrorCode.ACTION_FAILED}


def test_foreign_keys_enforced(env):
    cfg, db, device_id, stages, service = env
    with pytest.raises(Exception):
        db._conn.execute(
            "INSERT INTO action_steps(command_id, step_index, action, state) VALUES ('missing',0,'bilibili.pause','PENDING')"
        )


def test_single_writer_concurrency(env):
    cfg, db, device_id, stages, service = env
    stages.begin(CID, device_id, "boot")
    errors = []

    def writer(start: int):
        try:
            for i in range(start, start + 5):
                stages.append(CID, frame(i))
        except Exception as exc:  # expected ordering conflicts
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i * 5,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # No crash, and every accepted frame is contiguous in the staging file.
    pcm = (cfg.staging_dir / f"{CID}.pcm.part").read_bytes()
    count = len(pcm) // 640
    assert 1 <= count <= 10 and len(pcm) % 640 == 0
    assert pcm == b"".join(frame(i)[8:] for i in range(count))
    # A losing writer can leave fewer than the ten-frame durable checkpoint.
    # Sealing must persist the actual accepted prefix in either schedule.
    stages.seal(CID,count,len(pcm),hashlib.sha256(pcm).hexdigest())
    row = db.get_command(CID)
    assert row["frame_count"] == count and row["pcm_bytes"] == len(pcm)
