"""Staged audio, WAV sealing, durable commit and replay preparation.

The firmware may stream PCM into staging, but nothing can start STT or touch
the Mac until `commit()` has durably written COMMITTED. A cancel before any
external action leaves zero effects.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import adpcm, contracts
from .config import AppConfig, unix_to_iso, utc_now
from .contracts import (
    AUDIO_FORMAT,
    CAPABILITY_VERSION,
    CommandState,
    ErrorCode,
    MAX_FRAME_PCM_BYTES,
    MAX_PCM_BYTES,
    REPLAY_ACTIONS,
    StepState,
    canonical_json,
    replay_plan_digest,
    sha256_hex,
)
from .storage import ConflictError, Database, StorageError


class SessionError(RuntimeError):
    def __init__(self, code: ErrorCode | str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class AppendReceipt:
    next_seq: int
    pcm_bytes: int
    should_ack: bool


def _safe_relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if root_resolved not in resolved.parents and resolved != root_resolved:
        raise SessionError(ErrorCode.STORAGE_ERROR, "path escapes data root")
    return resolved.relative_to(root_resolved).as_posix()


def _open_private(path: Path, mode: str) -> Any:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, mode)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def wav_header(pcm_bytes: int, rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = rate * channels * bits // 8
    block_align = channels * bits // 8
    return b"RIFF" + struct.pack("<I", 36 + pcm_bytes) + b"WAVE" + b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, rate, byte_rate, block_align, bits
    ) + b"data" + struct.pack("<I", pcm_bytes)


def analyze_pcm(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 2:
        raise SessionError(ErrorCode.AUDIO_INVALID, "audio too short")
    if len(data) % 2:
        raise SessionError(ErrorCode.AUDIO_INVALID, "odd PCM byte count")
    samples = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
    if samples.size == 0:
        raise SessionError(ErrorCode.AUDIO_INVALID, "empty audio")
    rms = float(np.sqrt(np.mean(np.square(samples))))
    peak = float(np.max(np.abs(samples)))
    clipped = float(np.mean(np.abs(samples) >= 0.999))
    if clipped > 0.01:
        quality = "clipped"
    elif rms < 1e-3:
        quality = "silent"
    elif rms < 0.004:
        quality = "noisy"
    else:
        quality = "good"
    return {
        "samples": int(samples.size),
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "clipped_ratio": round(clipped, 6),
        "quality_label": quality,
        "duration_ms": int(round(samples.size * 1000 / 16000)),
        "size_bytes": len(data),
    }


class StageStore:
    def __init__(self, cfg: AppConfig, db: Database):
        self.cfg = cfg
        self.db = db
        self._append_locks: dict[str, Any] = {}
        self._short_frame_seen: set[str] = set()
        self._progress: dict[str, tuple[int, int]] = {}
        self._append_fhs: dict[str, Any] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def _staging_pcm(self, command_id: str) -> Path:
        return self.cfg.staging_dir / f"{command_id}.pcm.part"

    def _close_append(self, command_id: str) -> None:
        fh = self._append_fhs.pop(command_id, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def _staging_wav(self, command_id: str) -> Path:
        return self.cfg.staging_dir / f"{command_id}.wav"

    def _check_disk(self) -> None:
        usage = shutil.disk_usage(self.cfg.root)
        needed = max(512 * 1024 * 1024, 2 * MAX_PCM_BYTES)
        if usage.free < needed:
            raise SessionError(ErrorCode.DISK_FULL, "not enough free disk for a command")

    def begin(self, command_id: str, device_id: str, boot_id: str | None, now: float | None = None) -> dict[str, Any]:
        fingerprint = sha256_hex(canonical_json({
            "kind": "audio",
            "device_id": device_id,
            "boot_id": boot_id or "",
            "format": AUDIO_FORMAT.model_dump(),
            "trigger": "esp32_ptt",
        }))
        if self.db.get_state(command_id) is not None:
            existing = self.db.get_command(command_id)
            if not existing or existing["request_fingerprint"] != fingerprint:
                raise SessionError(ErrorCode.ID_CONFLICT, "same command id with different immutable fields")
            # A repeated begin never reopens writable audio.
            if existing["state"] != CommandState.STAGING.value:
                raise SessionError(ErrorCode.ID_CONFLICT, "command already exists outside STAGING")
            return existing
        self._check_disk()
        expires_at = (now or utc_now()) + 75.0
        row = self.db.create_audio_command(command_id, device_id, boot_id, fingerprint, expires_at)
        self._progress[command_id] = (0, 0)
        path = self._staging_pcm(command_id)
        if not path.exists():
            with _open_private(path, "wb") as fh:
                fh.flush()
                os.fsync(fh.fileno())
        return row

    def append(self, command_id: str, data: bytes, codec: str = "s16le") -> AppendReceipt:
        with self._lock:
            return self._append_locked(command_id, data, codec)

    def _append_locked(self, command_id: str, data: bytes, codec: str = "s16le") -> AppendReceipt:
        if command_id not in self._progress:
            row = self.db.get_command(command_id)
            if not row:
                raise SessionError(ErrorCode.COMMAND_NOT_FOUND, "unknown command")
            if row["state"] != CommandState.STAGING.value:
                raise SessionError(ErrorCode.PROTOCOL_ERROR, "append outside STAGING")
            self._progress[command_id] = (int(row["frame_count"] or 0), int(row["pcm_bytes"] or 0))
        if len(data) < 8:
            raise SessionError(ErrorCode.AUDIO_INVALID, "frame header truncated")
        seq, pcm_len, flags = struct.unpack_from("<IHH", data, 0)
        if flags != 0:
            raise SessionError(ErrorCode.AUDIO_INVALID, "unknown frame flags")
        payload = data[8:]
        if pcm_len != len(payload):
            raise SessionError(ErrorCode.AUDIO_INVALID, "pcm_len does not match payload")
        if codec == "ima4":
            # One 164-byte ADPCM block always reconstructs 640 PCM bytes; the
            # frame sequence and byte budget below are tracked in decoded PCM so
            # seal/commit keep their existing semantics.
            if pcm_len != adpcm.BLOCK_BYTES:
                raise SessionError(ErrorCode.AUDIO_INVALID, "adpcm block length")
            payload = adpcm.decode_block(payload)
            pcm_len = len(payload)
        elif codec != "s16le":
            raise SessionError(ErrorCode.AUDIO_INVALID, "unsupported audio codec")
        if pcm_len < 2 or pcm_len % 2 or pcm_len > MAX_FRAME_PCM_BYTES:
            raise SessionError(ErrorCode.AUDIO_INVALID, "invalid PCM frame length")
        expected_seq, expected_bytes = self._progress[command_id]
        if seq != expected_seq:
            raise SessionError(ErrorCode.AUDIO_INVALID, "frame sequence gap, duplicate or reordering")
        if command_id in self._short_frame_seen:
            raise SessionError(ErrorCode.AUDIO_INVALID, "audio after a short frame")
        new_bytes = expected_bytes + pcm_len
        if new_bytes > MAX_PCM_BYTES or seq + 1 > contracts.MAX_FRAMES:
            raise SessionError(ErrorCode.AUDIO_OVERRUN, "audio exceeds protocol limits")
        path = self._staging_pcm(command_id)
        if path.is_symlink():
            raise SessionError(ErrorCode.STORAGE_ERROR, "staging path is a symlink")
        fh = self._append_fhs.get(command_id)
        if fh is None:
            fh = path.open("ab")
            self._append_fhs[command_id] = fh
        fh.write(payload)
        fh.flush()
        self._progress[command_id] = (seq + 1, new_bytes)
        if pcm_len < MAX_FRAME_PCM_BYTES:
            self._short_frame_seen.add(command_id)
        # Updating the durable row on every 20 ms frame turns 50 fsyncs/s into
        # the staging bottleneck. Persist progress every 10 frames and at seal;
        # the PCM file itself is flushed per append.
        if (seq + 1) % 10 == 0 or pcm_len < MAX_FRAME_PCM_BYTES:
            self.db.append_progress(command_id, seq + 1, new_bytes)
        receipt = AppendReceipt(seq + 1, new_bytes, (seq + 1) % 10 == 0)
        return receipt

    def seal(self, command_id: str, frame_count: int, pcm_bytes: int, sha256: str) -> dict[str, Any]:
        row = self.db.get_command(command_id)
        if not row:
            raise SessionError(ErrorCode.COMMAND_NOT_FOUND, "unknown command")
        if row["state"] == CommandState.SEALED.value:
            if (row["frame_count"], row["pcm_bytes"], row["pcm_sha256"]) != (frame_count, pcm_bytes, sha256):
                raise SessionError(ErrorCode.ID_CONFLICT, "duplicate seal differs")
            return row
        if row["state"] != CommandState.STAGING.value:
            raise SessionError(ErrorCode.PROTOCOL_ERROR, "seal outside STAGING")
        self._close_append(command_id)
        path = self._staging_pcm(command_id)
        if not path.exists():
            raise SessionError(ErrorCode.AUDIO_INVALID, "staging audio missing")
        actual_size = path.stat().st_size
        if actual_size != pcm_bytes:
            raise SessionError(ErrorCode.AUDIO_INVALID, "sealed byte count does not match staging file")
        expected = self._progress.get(command_id)
        if expected is not None and expected != (frame_count, pcm_bytes):
            raise SessionError(ErrorCode.AUDIO_INVALID, "sealed frame count does not match appends")
        # Accept a reconnect/restart duplicate seal only if the staging file and
        # requested byte count agree; frame count is derivable from 640-byte
        # frames with a possible short final frame.
        derived_frames = (pcm_bytes + MAX_FRAME_PCM_BYTES - 1) // MAX_FRAME_PCM_BYTES
        if derived_frames != frame_count:
            raise SessionError(ErrorCode.AUDIO_INVALID, "sealed frame count does not match PCM size")
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        if h != sha256:
            raise SessionError(ErrorCode.HASH_MISMATCH, "PCM hash mismatch")
        # fsync the staged PCM before writing the WAV copy.
        with path.open("rb") as fh:
            os.fsync(fh.fileno())
        wav = self._staging_wav(command_id)
        tmp = wav.with_suffix(".wav.tmp")
        with _open_private(tmp, "wb") as out, path.open("rb") as inp:
            out.write(wav_header(actual_size))
            shutil.copyfileobj(inp, out, length=64 * 1024)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, wav)
        _fsync_dir(self.cfg.staging_dir)
        self.db.attach_wav(command_id, _safe_relative(self.cfg.root, wav))
        return self.db.seal(command_id, frame_count, pcm_bytes, sha256)

    def commit(self, command_id: str, sha256: str) -> dict[str, Any]:
        row = self.db.get_command(command_id)
        if not row:
            raise SessionError(ErrorCode.COMMAND_NOT_FOUND, "unknown command")
        state = CommandState(row["state"])
        if state in (CommandState.COMMITTED, CommandState.TRANSCRIBING, CommandState.PLANNING,
                     CommandState.EXECUTING, CommandState.AWAITING_CONFIRMATION):
            return row
        if state is not CommandState.SEALED:
            raise SessionError(ErrorCode.PROTOCOL_ERROR, "commit before seal")
        if row["expires_at"] is not None and float(row["expires_at"]) < utc_now():
            raise SessionError(ErrorCode.COMMAND_NOT_FOUND, "command expired before commit")
        if row["kind"] == "audio":
            if row["pcm_sha256"] != sha256:
                raise SessionError(ErrorCode.HASH_MISMATCH, "commit hash mismatch")
            wav = self.cfg.staging_dir / f"{command_id}.wav"
            if not wav.exists() or wav.is_symlink():
                raise SessionError(ErrorCode.STORAGE_ERROR, "sealed WAV missing")
            meta = analyze_pcm(self.cfg.staging_dir / f"{command_id}.pcm.part")
            month = time.strftime("%Y-%m", time.localtime())
            dest_dir = self.cfg.audio_dir / month
            dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            dest = dest_dir / f"{command_id}.wav"
            os.replace(wav, dest)
            _fsync_dir(self.cfg.staging_dir)
            _fsync_dir(dest_dir)
            relative = _safe_relative(self.cfg.root, dest)
            meta["retention_until"] = utc_now() + self.cfg.retention_days_success * 86400
            committed = self.db.commit(command_id, sha256, relative, meta)
            # Staging PCM is removed only after the durable COMMITTED row.
            try:
                (self.cfg.staging_dir / f"{command_id}.pcm.part").unlink(missing_ok=True)
            except OSError:
                pass
            self._short_frame_seen.discard(command_id)
            return committed
        # replay: no audio, plan hash already stored
        if row["payload_sha256"] != sha256:
            raise SessionError(ErrorCode.HASH_MISMATCH, "replay plan hash mismatch")
        return self.db.commit(command_id, sha256, "", {})

    def cancel(self, command_id: str, reason: str) -> tuple[str, CommandState]:
        row = self.db.get_command(command_id)
        if not row:
            raise SessionError(ErrorCode.COMMAND_NOT_FOUND, "unknown command")
        outcome, state = self.db.cancel(command_id, reason)
        self._progress.pop(command_id, None)
        self._close_append(command_id)
        if outcome == "canceled" and CommandState(row["state"]) in (
            CommandState.STAGING, CommandState.SEALED
        ):
            for path in (self._staging_pcm(command_id), self._staging_wav(command_id)):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._short_frame_seen.discard(command_id)
        return outcome, state


class CommandService:
    def __init__(self, cfg: AppConfig, db: Database, stages: StageStore):
        self.cfg = cfg
        self.db = db
        self.stages = stages

    def status(self, command_id: str) -> dict[str, Any] | None:
        return self.db.get_command(command_id)

    def prepare_replay(self, command_id: str, device_id: str, replay_of: str, boot_id: str | None = None,
                       now: float | None = None) -> dict[str, Any]:
        now = now or utc_now()
        source = self.db.get_command(replay_of)
        if not source or source["device_id"] != device_id:
            raise SessionError(ErrorCode.COMMAND_NOT_FOUND, "replay source not found for this device")
        if source["state"] != CommandState.SUCCEEDED.value:
            raise SessionError(ErrorCode.ACTION_FAILED, "only a successful command can be replayed")
        plan = json.loads(source["plan_json"] or "{}")
        actions = plan.get("actions", [])
        if len(actions) != 1 or actions[0].get("action") not in REPLAY_ACTIONS:
            raise SessionError(ErrorCode.CAPABILITY_UNAVAILABLE, "this command cannot be replayed")
        action = actions[0]["action"]
        params = actions[0].get("params", {})
        player = self.db.current_player(now)
        if action in {"bilibili.pause", "bilibili.resume", "bilibili.danmaku_on", "bilibili.danmaku_off"}:
            if not player or (player["expires_at"] < now):
                raise SessionError(ErrorCode.TARGET_STALE, "player state is not fresh enough to replay")
            target_video = player["video_id"]
            generation = player["generation"]
            account = player["account_id"]
        elif action == "system.mute":
            target_video = None
            generation = None
            account = None
        else:  # pragma: no cover - REPLAY_ACTIONS is exhaustive
            raise SessionError(ErrorCode.CAPABILITY_UNAVAILABLE, "action is not replayable")
        digest = replay_plan_digest(
            uuid.UUID(command_id), uuid.UUID(device_id), uuid.UUID(replay_of), action, params,
            target_video, generation, account, CAPABILITY_VERSION, int(now),
        )
        frozen = {
            "schema_version": 1,
            "kind": "replay",
            "replay_of": replay_of,
            "action": action,
            "params": params,
            "target_video_id": target_video,
            "player_generation": generation,
            "account_id": account,
            "capability_version": CAPABILITY_VERSION,
            "frozen_at": int(now),
        }
        fp = sha256_hex(canonical_json({
            "kind": "replay", "device_id": device_id, "replay_of": replay_of,
            "action": action, "params": params,
        }))
        row = self.db.create_replay_command(
            command_id, device_id, boot_id, replay_of, fp, digest,
            canonical_json(frozen).decode(), now + 10.0,
        )
        return {
            "command": row,
            "sha256": digest,
            "summary": f"Replay {action}",
            "expires_in_ms": 10_000,
        }

    def set_failure_retention(self, command_id: str) -> None:
        try:
            with self.db.transaction() as tx:
                tx.execute(
                    "UPDATE audio_samples SET retention_until=? WHERE command_id=? AND pinned=0",
                    (utc_now() + self.cfg.retention_days_failure * 86400, command_id),
                )
        except Exception as exc:  # pragma: no cover - defensive
            raise SessionError(ErrorCode.STORAGE_ERROR, str(exc)) from exc

