"""Durable command journal and SQLite storage.

One writer queue/lock serializes conditional state transitions. Every
connection enables foreign keys, WAL and synchronous=FULL. No code path
creates a fresh empty database to hide corruption.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .contracts import (
    CommandState,
    ErrorCode,
    StepState,
    TERMINAL_STATES,
    canonical_json,
)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SCHEMA_VERSION = 2


class StorageError(RuntimeError):
    pass


class ConflictError(StorageError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_now() -> float:
    return time.time()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


ACTIVE_STATES = tuple(
    s.value for s in CommandState if s not in TERMINAL_STATES
)


class Database:
    def __init__(self, path: Path, timeout: float = 5.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), timeout=timeout, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._configure(self._conn)
            self._migrate()

    @staticmethod
    def _configure(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 5000")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise StorageError("SQLite foreign keys are not enabled")

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
        )
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        current = row["v"] or 0
        if current > SCHEMA_VERSION:
            raise StorageError(
                f"database schema {current} is newer than this application ({SCHEMA_VERSION}); refusing to run"
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            suffix = "initial.sql" if version == 1 else "feedback.sql"
            path = MIGRATIONS_DIR / f"{version:03d}_{suffix}"
            # Only migration 001 exists under its numbered name.
            if version == 1:
                path = MIGRATIONS_DIR / "001_initial.sql"
            if not path.exists():
                raise StorageError(f"missing migration {path}")
            script = path.read_text(encoding="utf-8").replace("PRAGMA foreign_keys = ON;", "")
            with self._lock:
                self._conn.executescript(
                    "BEGIN IMMEDIATE;\n" + script
                    + f"\nINSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES ({version}, {utc_now()});\nCOMMIT;"
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # devices
    # ------------------------------------------------------------------
    def register_device(self, display_name: str, token: str | None = None) -> tuple[str, str]:
        token = token or os.urandom(32).hex()
        device_id = str(uuid.uuid4())
        with self.transaction() as tx:
            tx.execute(
                "INSERT INTO devices(id, token_hash, created_at, display_name) VALUES (?,?,?,?)",
                (device_id, token_hash(token), utc_now(), display_name),
            )
        return device_id, token

    def device_exists(self, device_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM devices WHERE id=? AND revoked_at IS NULL", (device_id,)).fetchone()
        return row is not None

    def ble_auth_key(self, device_id: str) -> bytes | None:
        """Secret equivalent of the USB token; never serialize or log this value."""
        with self._lock:
            row = self._conn.execute(
                "SELECT token_hash FROM devices WHERE id=? AND revoked_at IS NULL", (device_id,)
            ).fetchone()
        return bytes.fromhex(row["token_hash"]) if row else None

    def authenticate_device(self, device_id: str, token: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT token_hash FROM devices WHERE id=? AND revoked_at IS NULL", (device_id,)
            ).fetchone()
        if not row or not token:
            return False
        return hmac.compare_digest(row["token_hash"], token_hash(token))

    def revoke_device(self, device_id: str) -> None:
        with self.transaction() as tx:
            tx.execute("UPDATE devices SET revoked_at=? WHERE id=?", (utc_now(), device_id))

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, revoked_at, display_name FROM devices ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def record_session(self, boot_id: str, device_id: str, firmware_version: str) -> None:
        now = utc_now()
        with self.transaction() as tx:
            tx.execute(
                """INSERT INTO device_sessions(boot_id, device_id, firmware_version, first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(boot_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,
                     firmware_version=excluded.firmware_version""",
                (boot_id, device_id, firmware_version[:64], now, now),
            )

    # ------------------------------------------------------------------
    # command lifecycle
    # ------------------------------------------------------------------
    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row:
                return None
            steps = self._conn.execute(
                "SELECT * FROM action_steps WHERE command_id=? ORDER BY step_index", (command_id,)
            ).fetchall()
        result = dict(row)
        result["steps"] = [dict(s) for s in steps]
        return result

    def get_state(self, command_id: str) -> CommandState | None:
        with self._lock:
            row = self._conn.execute("SELECT state FROM commands WHERE id=?", (command_id,)).fetchone()
        return CommandState(row["state"]) if row else None

    def active_command(self) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM commands WHERE state IN ({placeholders}) ORDER BY created_at LIMIT 1",
                ACTIVE_STATES,
            ).fetchone()
        return dict(row) if row else None

    def create_audio_command(
        self,
        command_id: str,
        device_id: str,
        boot_id: str | None,
        fingerprint: str,
        expires_at: float,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.transaction() as tx:
            existing = tx.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if existing:
                if existing["request_fingerprint"] != fingerprint:
                    raise ConflictError("ID_CONFLICT", "same command id with different immutable fields")
                return dict(existing)
            active = tx.execute(
                f"SELECT id FROM commands WHERE state IN ({','.join('?' for _ in ACTIVE_STATES)}) LIMIT 1",
                ACTIVE_STATES,
            ).fetchone()
            if active:
                raise ConflictError("BUSY", "another command is active")
            tx.execute(
                """INSERT INTO commands(id, device_id, boot_id, kind, source, state, created_at, updated_at,
                       expires_at, request_fingerprint)
                   VALUES (?,?,?,'audio','esp32_ptt','STAGING',?,?,?,?)""",
                (command_id, device_id, boot_id, now, now, expires_at, fingerprint),
            )
        return self.get_command(command_id) or {}

    def create_replay_command(
        self,
        command_id: str,
        device_id: str,
        boot_id: str | None,
        replay_of: str,
        fingerprint: str,
        plan_hash: str,
        plan_json: str,
        expires_at: float,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.transaction() as tx:
            existing = tx.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if existing:
                if existing["request_fingerprint"] != fingerprint:
                    raise ConflictError("ID_CONFLICT", "same command id with different immutable fields")
                return dict(existing)
            source = tx.execute(
                "SELECT * FROM commands WHERE id=? AND state='SUCCEEDED'", (replay_of,)
            ).fetchone()
            if not source:
                raise ConflictError("COMMAND_NOT_FOUND", "replay target is not a successful command")
            active = tx.execute(
                f"SELECT id FROM commands WHERE state IN ({','.join('?' for _ in ACTIVE_STATES)}) LIMIT 1",
                ACTIVE_STATES,
            ).fetchone()
            if active:
                raise ConflictError("BUSY", "another command is active")
            tx.execute(
                """INSERT INTO commands(id, device_id, boot_id, kind, source, state, created_at, updated_at,
                       expires_at, request_fingerprint, pcm_sha256, payload_sha256, plan_json, replay_of)
                   VALUES (?,?,?,'replay','esp32_replay','SEALED',?,?,?,?,?,?,?,?)""",
                (
                    command_id, device_id, boot_id, now, now, expires_at, fingerprint,
                    plan_hash, plan_hash, plan_json, replay_of,
                ),
            )
            plan = json.loads(plan_json)
            for index, action in enumerate(plan.get("actions", [])):
                tx.execute(
                    """INSERT INTO action_steps(command_id, step_index, action, resolved_params_json, state)
                       VALUES (?,?,?,?,'PENDING')""",
                    (command_id, index, action["action"], canonical_json(action.get("params", {})).decode()),
                )
        return self.get_command(command_id) or {}

    def append_progress(self, command_id: str, next_seq: int, pcm_bytes: int) -> None:
        with self.transaction() as tx:
            row = tx.execute("SELECT state FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row or row["state"] != CommandState.STAGING.value:
                raise ConflictError("PROTOCOL_ERROR", "append outside STAGING")
            tx.execute(
                "UPDATE commands SET frame_count=?, pcm_bytes=?, updated_at=? WHERE id=?",
                (next_seq, pcm_bytes, utc_now(), command_id),
            )

    def seal(
        self,
        command_id: str,
        frame_count: int,
        pcm_bytes: int,
        sha256: str,
        state: CommandState = CommandState.SEALED,
    ) -> dict[str, Any]:
        with self.transaction() as tx:
            row = tx.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row:
                raise ConflictError("COMMAND_NOT_FOUND", "unknown command")
            if row["state"] == state.value:
                if (row["frame_count"], row["pcm_bytes"], row["pcm_sha256"]) != (frame_count, pcm_bytes, sha256):
                    raise ConflictError("ID_CONFLICT", "duplicate seal with different values")
                return dict(row)
            if row["state"] != CommandState.STAGING.value:
                raise ConflictError("PROTOCOL_ERROR", "seal outside STAGING")
            tx.execute(
                """UPDATE commands SET state=?, frame_count=?, pcm_bytes=?, pcm_sha256=?,
                       payload_sha256=?, updated_at=? WHERE id=?""",
                (state.value, frame_count, pcm_bytes, sha256, sha256, utc_now(), command_id),
            )
        return self.get_command(command_id) or {}

    def attach_wav(self, command_id: str, wav_path: str) -> None:
        with self.transaction() as tx:
            tx.execute("UPDATE commands SET wav_path=? WHERE id=?", (wav_path, command_id))

    def commit(self, command_id: str, sha256: str, wav_relative_path: str, audio_meta: dict[str, Any]) -> dict[str, Any]:
        """Durably accept a SEALED command. Must run after WAV rename+fsync."""
        now = utc_now()
        with self.transaction() as tx:
            row = tx.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row:
                raise ConflictError("COMMAND_NOT_FOUND", "unknown command")
            state = CommandState(row["state"])
            if state in TERMINAL_STATES:
                return dict(row)
            if state in (
                CommandState.COMMITTED, CommandState.TRANSCRIBING,
                CommandState.PLANNING, CommandState.EXECUTING,
                CommandState.AWAITING_CONFIRMATION,
            ):
                if row["pcm_sha256"] != sha256 and row["kind"] == "audio":
                    raise ConflictError("HASH_MISMATCH", "commit hash differs from sealed hash")
                return dict(row)
            if state is not CommandState.SEALED:
                raise ConflictError("PROTOCOL_ERROR", "commit before seal")
            if row["kind"] == "audio" and row["pcm_sha256"] != sha256:
                raise ConflictError("HASH_MISMATCH", "commit hash differs from sealed hash")
            if row["kind"] == "replay" and row["payload_sha256"] != sha256:
                raise ConflictError("HASH_MISMATCH", "replay plan hash differs")
            tx.execute(
                "UPDATE commands SET state='COMMITTED', committed_at=?, updated_at=?, payload_sha256=? WHERE id=?",
                (now, now, sha256, command_id),
            )
            if row["kind"] == "audio":
                tx.execute(
                    """INSERT OR REPLACE INTO audio_samples(
                         command_id, relative_path, hash, size_bytes, sample_rate, channels, bit_depth,
                         duration_ms, quality_label, retention_until, pinned, deletion_state)
                       VALUES (?,?,?,?,16000,1,16,?,?,?,0,'active')""",
                    (
                        command_id, wav_relative_path, sha256,
                        int(audio_meta.get("size_bytes", row["pcm_bytes"] or 0)),
                        int(audio_meta.get("duration_ms", 0)),
                        audio_meta.get("quality_label", "unknown"),
                        audio_meta.get("retention_until"),
                    ),
                )
        return self.get_command(command_id) or {}

    def cancel(
        self, command_id: str, reason: str, code: ErrorCode = ErrorCode.USER_CANCELED
    ) -> tuple[str, CommandState]:
        """Return (outcome, state). External effects start only at EXECUTING."""
        with self.transaction() as tx:
            row = tx.execute("SELECT state FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row:
                raise ConflictError("COMMAND_NOT_FOUND", "unknown command")
            state = CommandState(row["state"])
            if state in TERMINAL_STATES:
                return "already_terminal", state
            started = tx.execute(
                "SELECT COUNT(*) AS n FROM action_steps WHERE command_id=? AND state IN ('STARTED','SUCCEEDED','NOOP_VERIFIED')",
                (command_id,),
            ).fetchone()["n"]
            if state is CommandState.EXECUTING or started:
                return "too_late", state
            if reason not in {"user", "short_press", "page_exit", "timeout", "audio_error", "network_error"}:
                reason = "user"
            tx.execute(
                "UPDATE commands SET state='CANCELED', error_code=?, updated_at=?, summary=? WHERE id=?",
                (code.value, utc_now(), f"canceled: {reason}", command_id),
            )
            self._tombstone_tx(tx, command_id)
        return "canceled", CommandState.CANCELED

    def transition(self, command_id: str, new_state: CommandState, code: ErrorCode | None = None) -> bool:
        with self.transaction() as tx:
            row = tx.execute("SELECT state FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row:
                return False
            current = CommandState(row["state"])
            if current in TERMINAL_STATES:
                return current is new_state
            tx.execute(
                "UPDATE commands SET state=?, error_code=COALESCE(?, error_code), updated_at=? WHERE id=?",
                (new_state.value, code.value if code else None, utc_now(), command_id),
            )
        return True

    def set_transcript(self, command_id: str, raw_text: str, normalized_text: str, model_digest: str | None) -> None:
        with self.transaction() as tx:
            tx.execute(
                "UPDATE commands SET raw_text=?, normalized_text=?, model_digest=?, updated_at=? WHERE id=?",
                (raw_text[:4096], normalized_text[:4096], model_digest, utc_now(), command_id),
            )

    def journal_plan(self, command_id: str, plan: dict[str, Any], prompt_version: str = "intent-v1") -> None:
        actions = plan.get("actions", [])
        with self.transaction() as tx:
            tx.execute(
                "UPDATE commands SET plan_json=?, prompt_version=?, schema_version=1, updated_at=? WHERE id=?",
                (canonical_json(plan).decode(), prompt_version, utc_now(), command_id),
            )
            tx.execute("DELETE FROM action_steps WHERE command_id=?", (command_id,))
            for index, action in enumerate(actions):
                tx.execute(
                    """INSERT INTO action_steps(command_id, step_index, action, resolved_params_json, state)
                       VALUES (?,?,?,?,'PENDING')""",
                    (command_id, index, action["action"], canonical_json(action.get("params", {})).decode()),
                )

    def start_step(self, command_id: str, step_index: int) -> bool:
        with self.transaction() as tx:
            cur = tx.execute(
                """UPDATE action_steps SET state='STARTED', started_at=?
                   WHERE command_id=? AND step_index=? AND state IN ('PENDING','PREPARED')""",
                (utc_now(), command_id, step_index),
            )
        return cur.rowcount == 1

    def finish_step(
        self,
        command_id: str,
        step_index: int,
        state: StepState,
        output: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        code: ErrorCode | None = None,
    ) -> None:
        with self.transaction() as tx:
            tx.execute(
                """UPDATE action_steps SET state=?, completed_at=?, output_json=?, evidence_json=?, error_code=?
                   WHERE command_id=? AND step_index=?""",
                (
                    state.value, utc_now(),
                    canonical_json(output or {}).decode(),
                    canonical_json(evidence or {}).decode(),
                    code.value if code else None, command_id, step_index,
                ),
            )

    def finalize(
        self,
        command_id: str,
        state: CommandState,
        code: ErrorCode | None,
        summary: str,
        replayable: bool,
    ) -> None:
        step_rows = self.steps(command_id)
        step_results = [
            {
                "step_index": int(s["step_index"]),
                "status": s["state"],
                "code": s["error_code"],
            }
            for s in step_rows
        ]
        result = {
            "state": state.value,
            "code": code.value if code else None,
            "summary": summary[:128],
            "step_results": step_results,
            "replayable": bool(replayable),
        }
        with self.transaction() as tx:
            tx.execute(
                "UPDATE commands SET state=?, error_code=?, summary=?, result_json=?, updated_at=? WHERE id=?",
                (state.value, code.value if code else None, summary[:128], canonical_json(result).decode(), utc_now(), command_id),
            )
            self._tombstone_tx(tx, command_id)

    def steps(self, command_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM action_steps WHERE command_id=? ORDER BY step_index", (command_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _tombstone_tx(self, tx: sqlite3.Connection, command_id: str) -> None:
        row = tx.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
        if not row:
            return
        tx.execute(
            """INSERT INTO command_tombstones(id, device_id, immutable_hash, terminal_state, completed_at)
               VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
            (command_id, row["device_id"], row["pcm_sha256"], row["state"], utc_now()),
        )

    def status_snapshot(self, command_id: str) -> dict[str, Any] | None:
        return self.get_command(command_id)

    # ------------------------------------------------------------------
    # snapshots / confirmations / rate limits
    # ------------------------------------------------------------------
    def save_snapshot(
        self,
        kind: str,
        app_id: str,
        generation: str,
        payload: dict[str, Any],
        observed_at: float,
        expires_at: float,
        command_id: str | None = None,
        device_id: str | None = None,
        video_id: str | None = None,
    ) -> str:
        snap_id = str(uuid.uuid4())
        with self.transaction() as tx:
            tx.execute(
                """INSERT INTO snapshots(id, kind, device_id, command_id, app_id, video_id, generation,
                       observed_at, expires_at, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (snap_id, kind, device_id, command_id, app_id,
                 str(video_id) if video_id is not None else None, str(generation),
                 observed_at, expires_at, canonical_json(payload).decode()),
            )
        return snap_id

    def latest_snapshot(self, kind: str, now: float | None = None) -> dict[str, Any] | None:
        now = utc_now() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM snapshots WHERE kind=? AND expires_at>? ORDER BY observed_at DESC LIMIT 1",
                (kind, now),
            ).fetchone()
        return dict(row) if row else None

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
        return dict(row) if row else None

    def current_player(self, now: float | None = None) -> dict[str, Any] | None:
        row = self.latest_snapshot("player", now)
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        return {
            "video_id": row["video_id"],
            "generation": row["generation"],
            "paused": bool(payload.get("paused", True)),
            "position_ms": int(payload.get("position_ms", 0)),
            "observed_at": row["observed_at"],
            "expires_at": row["expires_at"],
            "account_id": payload.get("account_id"),
        }

    def save_confirmation(
        self, confirmation_id: str, command_id: str, device_id: str | None,
        account_id: str | None, frozen_plan: dict[str, Any], plan_hash: str, expires_at: float,
    ) -> None:
        with self.transaction() as tx:
            tx.execute(
                """INSERT INTO confirmations(id, command_id, device_id, account_id, frozen_plan_json,
                       plan_hash, expires_at, status) VALUES (?,?,?,?,?,?,?,'pending')""",
                (confirmation_id, command_id, device_id, account_id,
                 canonical_json(frozen_plan).decode(), plan_hash, expires_at),
            )

    def consume_confirmation(self, confirmation_id: str, command_id: str, plan_hash: str, now: float | None = None) -> bool:
        now = utc_now() if now is None else now
        with self.transaction() as tx:
            cur = tx.execute(
                """UPDATE confirmations SET status='consumed', consumed_at=?
                   WHERE id=? AND command_id=? AND plan_hash=? AND status='pending' AND expires_at>?""",
                (now, confirmation_id, command_id, plan_hash, now),
            )
            if cur.rowcount == 1:
                return True
            tx.execute(
                "UPDATE confirmations SET status='expired' WHERE id=? AND status='pending' AND expires_at<=?",
                (confirmation_id, now),
            )
        return False

    def confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM confirmations WHERE id=?", (confirmation_id,)).fetchone()
        return dict(row) if row else None

    def reserve_account_action(self, account_id: str, command_id: str, step_index: int) -> bool:
        now = utc_now()
        with self.transaction() as tx:
            row = tx.execute("SELECT * FROM account_rate_limits WHERE account_id=?", (account_id,)).fetchone()
            if row and row["blocked_until"] and row["blocked_until"] > now:
                return False
            tx.execute(
                """INSERT INTO account_rate_limits(account_id, reserved_command_id, reserved_step_index,
                       last_attempt_at, blocked_until) VALUES (?,?,?,?,?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     reserved_command_id=excluded.reserved_command_id,
                     reserved_step_index=excluded.reserved_step_index,
                     last_attempt_at=excluded.last_attempt_at,
                     blocked_until=excluded.blocked_until""",
                (account_id, command_id, step_index, now, now + 30.0),
            )
        return True

    # ------------------------------------------------------------------
    # recovery
    # ------------------------------------------------------------------
    def reconcile_startup(self) -> dict[str, Any]:
        """Repair interrupted state without ever resuming external work."""
        report = {"abandoned": [], "interrupted": [], "unknown": [], "partial": [], "succeeded": []}
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM commands WHERE state IN ({placeholders})", ACTIVE_STATES
            ).fetchall()
        for row in rows:
            command_id = row["id"]
            state = CommandState(row["state"])
            steps = self.steps(command_id)
            if state in (CommandState.STAGING, CommandState.SEALED):
                self.transition(command_id, CommandState.ABANDONED, ErrorCode.INTERRUPTED)
                with self.transaction() as tx:
                    self._tombstone_tx(tx, command_id)
                report["abandoned"].append(command_id)
                continue
            started = [s for s in steps if s["state"] == StepState.STARTED.value]
            finished = [s for s in steps if s["state"] in (StepState.SUCCEEDED.value, StepState.NOOP_VERIFIED.value)]
            if started:
                self.finalize(command_id, CommandState.OUTCOME_UNKNOWN, ErrorCode.OUTCOME_UNKNOWN,
                              "Mac restarted while an external action was in flight", False)
                report["unknown"].append(command_id)
            elif steps and len(finished) == len(steps):
                self.finalize(command_id, CommandState.SUCCEEDED, None, "recovered from journal", False)
                report["succeeded"].append(command_id)
            elif finished:
                with self.transaction() as tx:
                    tx.execute(
                        "UPDATE action_steps SET state='SKIPPED' WHERE command_id=? AND state IN ('PENDING','PREPARED')",
                        (command_id,),
                    )
                self.finalize(command_id, CommandState.PARTIAL, ErrorCode.INTERRUPTED,
                              "Mac restarted after some steps completed", False)
                report["partial"].append(command_id)
            else:
                self.finalize(command_id, CommandState.INTERRUPTED, ErrorCode.INTERRUPTED,
                              "Mac restarted before external execution", False)
                report["interrupted"].append(command_id)
        return report

    # ------------------------------------------------------------------
    # maintenance
    # ------------------------------------------------------------------
    def mkdir_prepare(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # ------------------------------------------------------------------
    # Feedback (device overlay / server fallback)
    # ------------------------------------------------------------------
    def record_feedback(self, command_id: str, device_id: str, *,
                        rating: str, dismissed_by: str, touch_count: int,
                        countdown_at_dismiss: int, page: int | None = None,
                        source: str = "device") -> dict[str, Any]:
        if rating not in ("good", "bad", "unfeedback"):
            raise ConflictError("INVALID_PLAN", "invalid feedback rating")
        if source not in ("device", "server"):
            raise ConflictError("INVALID_PLAN", "invalid feedback source")
        kind = {"good": "explicit_success", "bad": "correction",
                "unfeedback": "cancel_after"}[rating]
        with self.transaction() as tx:
            row = tx.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row:
                raise ConflictError("COMMAND_NOT_FOUND", "feedback command not found")
            if row["device_id"] and str(row["device_id"]) != str(device_id):
                raise ConflictError("AUTH_FAILED", "feedback device mismatch")
            if row["state"] not in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "OUTCOME_UNKNOWN"):
                raise ConflictError("TARGET_STALE", "feedback command is not terminal")
            steps = tx.execute(
                "SELECT step_index, action, state, output_json, evidence_json FROM action_steps "
                "WHERE command_id=? ORDER BY step_index", (command_id,)
            ).fetchall()
            player = tx.execute(
                "SELECT payload_json FROM snapshots WHERE kind='player' ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            search = tx.execute(
                "SELECT payload_json FROM snapshots WHERE kind='search' ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
            context = {
                "page": page,
                "command": {
                    "normalized_text": row["normalized_text"],
                    "raw_text": row["raw_text"],
                    "plan_json": row["plan_json"],
                    "state": row["state"],
                    "summary": row["summary"],
                    "error_code": row["error_code"],
                },
                "steps": [dict(x) for x in steps],
                "player": json.loads(player["payload_json"]) if player else None,
                "search": json.loads(search["payload_json"]) if search else None,
            }
            feedback_id = str(uuid.uuid4())
            now = utc_now()
            tx.execute(
                """INSERT INTO feedback
                   (id, command_id, kind, corrected_text, approved_plan_json,
                    human_reviewed, created_at, rating, reason, dismissed_by,
                    touch_count, countdown_at_dismiss, context_json, source, received_at)
                   VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(command_id) DO UPDATE SET
                     rating=excluded.rating, reason=excluded.reason,
                     dismissed_by=excluded.dismissed_by,
                     touch_count=excluded.touch_count,
                     countdown_at_dismiss=excluded.countdown_at_dismiss,
                     context_json=excluded.context_json,
                     source=excluded.source, received_at=excluded.received_at,
                     kind=excluded.kind
                   WHERE feedback.source != 'device' OR excluded.source = 'device'""",
                (feedback_id, command_id, kind, None, None, now, rating, None,
                 dismissed_by, touch_count, countdown_at_dismiss,
                 json.dumps(context, ensure_ascii=False, sort_keys=True), source, now),
            )
            saved = tx.execute("SELECT * FROM feedback WHERE command_id=?", (command_id,)).fetchone()
            return dict(saved) if saved else {}

    def feedback_for_command(self, command_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM feedback WHERE command_id=?", (command_id,)).fetchone()
        return dict(row) if row else None

    def feedback_groups(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.state, c.normalized_text, c.raw_text, c.plan_json, "
                "c.summary, c.error_code, c.created_at, f.rating, f.dismissed_by, "
                "f.touch_count, f.context_json, f.source "
                "FROM commands c JOIN feedback f ON f.command_id=c.id "
                "WHERE c.state IN ('SUCCEEDED','PARTIAL','FAILED','CANCELED','OUTCOME_UNKNOWN') "
                "ORDER BY c.created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

