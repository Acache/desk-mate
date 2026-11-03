"""Local speech-to-text with strict audio validation and a killable worker.

The default backend is SenseVoiceSmall loaded by FunASR in a separate worker
process. If the model/runtime is not installed the pipeline fails closed with
MODEL_UNAVAILABLE; it never falls back to a cloud service.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .contracts import ErrorCode, normalize_text

SENSEVOICE_MODEL = "iic/SenseVoiceSmall"
TAG_RE = re.compile(r"<\|[^|>]*\|>")
BRACKET_RE = re.compile(r"\[[^\]]{0,40}\]")


class STTError(RuntimeError):
    def __init__(self, code: ErrorCode, summary: str):
        super().__init__(summary)
        self.code = code
        self.summary = summary


@dataclass
class Transcript:
    raw_model_text: str
    normalized_text: str
    language: str | None
    segments: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    model_digest: str | None = None
    latency_ms: int = 0
    quality_flags: dict[str, Any] = field(default_factory=dict)


@dataclass
class AudioValidation:
    duration_ms: int
    rms: float
    peak: float
    clipped_ratio: float
    dc_offset: float
    silence_ratio: float
    quality_label: str
    sample_rate: int
    channels: int
    bits: int
    active_ms: int = 0
    active_rms: float = 0.0

    def is_speech_like(self) -> bool:
        # Push-to-talk recordings normally start with a silent lead-in while
        # the user waits for the device to show LISTENING. Whole-file RMS then
        # understates the level and rejects real speech (observed: 0.00247 vs
        # the 0.0025 threshold on a valid "搜索机器学习" take). Accept audio
        # whose *active* frames are loud enough for long enough instead.
        return (
            self.duration_ms >= 500
            and self.quality_label not in {"silent", "clipped"}
            and (self.rms >= 0.0025
                 or (self.active_ms >= 300 and self.active_rms >= 0.005))
        )


def validate_audio(path: Path) -> AudioValidation:
    """Validate the WAV header and compute bounded acoustic quality metrics."""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            bits = wf.getsampwidth() * 8
            frames = wf.getnframes()
            raw = wf.readframes(frames)
    except (wave.Error, EOFError, OSError) as exc:
        raise STTError(ErrorCode.AUDIO_INVALID, f"invalid WAV: {exc}") from exc
    if rate != 16000 or channels != 1 or bits != 16:
        raise STTError(ErrorCode.AUDIO_INVALID, "WAV must be 16 kHz mono 16-bit")
    if frames < 8000 or len(raw) != frames * 2:
        raise STTError(ErrorCode.AUDIO_INVALID, "WAV is too short or truncated")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(np.square(samples))))
    peak = float(np.max(np.abs(samples)))
    clipped = float(np.mean(np.abs(samples) >= 0.999))
    dc = float(np.mean(samples))
    # Frame-level silence ratio (20 ms frames below -50 dBFS).
    frame = 320
    usable = (samples.size // frame) * frame
    active_ms, active_rms = 0, 0.0
    if usable:
        blocks = samples[:usable].reshape(-1, frame)
        block_rms = np.sqrt(np.mean(np.square(blocks), axis=1))
        silence = float(np.mean(block_rms < 0.0032))
        active = block_rms[block_rms >= 0.0032]
        active_ms = int(active.size * 20)
        if active.size:
            active_rms = float(np.sqrt(np.mean(np.square(active))))
    else:
        silence = 1.0
    if clipped > 0.01:
        quality = "clipped"
    elif rms < 1e-3:
        quality = "silent"
    elif rms < 0.004 or silence > 0.92:
        quality = "noisy"
    else:
        quality = "good"
    return AudioValidation(
        duration_ms=int(round(frames * 1000 / rate)),
        rms=round(rms, 6), peak=round(peak, 6), clipped_ratio=round(clipped, 6),
        dc_offset=round(dc, 6), silence_ratio=round(silence, 6), quality_label=quality,
        sample_rate=rate, channels=channels, bits=bits,
        active_ms=active_ms, active_rms=round(active_rms, 6),
    )


def clean_model_text(text: str) -> tuple[str, str | None]:
    """Strip SenseVoice metadata tags without changing spoken content."""
    language = None
    match = re.search(r"<\|([a-zA-Z]{2,8})\|>", text or "")
    if match:
        language = match.group(1).lower()
    cleaned = TAG_RE.sub("", text or "")
    cleaned = BRACKET_RE.sub("", cleaned)
    return normalize_text(cleaned), language


class STTAdapter(Protocol):
    model_digest: str | None

    def transcribe(self, wav_path: Path, deadline: float) -> Transcript:
        ...

    def health(self) -> dict[str, Any]:
        ...


class FakeSTT:
    """Test double; text is always supplied by the test, never inferred."""

    def __init__(self, text: str = "暂停", model_digest: str = "fake-stt"):
        self.text = text
        self.model_digest = model_digest
        self.calls: list[Path] = []

    def transcribe(self, wav_path: Path, deadline: float) -> Transcript:
        self.calls.append(wav_path)
        validation = validate_audio(wav_path)
        normalized, language = clean_model_text(self.text)
        return Transcript(
            raw_model_text=self.text,
            normalized_text=normalized,
            language=language,
            model_digest=self.model_digest,
            latency_ms=1,
            quality_flags=validation.__dict__,
        )

    def health(self) -> dict[str, Any]:
        return {"backend": "fake", "available": True, "model_digest": self.model_digest}


class SenseVoiceWorker:
    """A single, prewarmed FunASR worker process with a kill/restart path."""

    def __init__(self, model: str = SENSEVOICE_MODEL, start_timeout: float = 300.0):
        self.model = os.environ.get("VOICE_STT_MODEL", model)
        self.start_timeout = start_timeout
        self.proc: subprocess.Popen | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._ready = False
        self._digest: str | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            env = dict(os.environ)
            env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            cmd = [sys.executable, "-m", "voice_assistant.stt_worker", "--model", self.model]
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=env,
            )
            self._queue = queue.Queue()
            self._reader = threading.Thread(target=self._read_loop, args=(self.proc.stdout,self._queue), daemon=True)
            self._reader.start()
            # FunASR writes loader diagnostics to stderr. Drain continuously;
            # an unread PIPE can fill and deadlock offline model startup.
            proc = self.proc
            def drain_stderr():
                assert proc and proc.stderr
                for line in proc.stderr:
                    logging.getLogger(__name__).debug("STT worker: %s",line.rstrip()[:1000])
            threading.Thread(target=drain_stderr,daemon=True).start()
            deadline = time.time() + self.start_timeout
            msg = None
            while time.time() < deadline:
                try:
                    line = self._queue.get(timeout=max(0.5, deadline - time.time()))
                except queue.Empty as exc:
                    self.stop()
                    raise STTError(ErrorCode.MODEL_TIMEOUT, "SenseVoice worker did not become ready") from exc
                if line is None:
                    self.stop()
                    raise STTError(ErrorCode.MODEL_UNAVAILABLE, "SenseVoice worker failed to start")
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue  # third-party banner; protocol JSON follows
                if isinstance(candidate, dict) and "ready" in candidate:
                    msg = candidate
                    break
            if msg is None:
                self.stop()
                raise STTError(ErrorCode.MODEL_TIMEOUT, "SenseVoice worker did not become ready")
            if not msg.get("ready"):
                self.stop()
                raise STTError(ErrorCode.MODEL_UNAVAILABLE, msg.get("error", "SenseVoice unavailable"))
            self._ready = True
            self._digest = msg.get("model_digest")

    def _read_loop(self, stream, output) -> None:
        for line in stream:
            output.put(line.strip())
        output.put(None)

    def transcribe(self, wav_path: Path, deadline: float) -> Transcript:
        if not self._ready:
            self.start()
        assert self.proc and self.proc.stdin
        payload = json.dumps({"audio_path": str(wav_path)})
        try:
            self.proc.stdin.write(payload + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.stop()
            raise STTError(ErrorCode.MODEL_UNAVAILABLE, "SenseVoice worker pipe closed") from exc
        remaining = max(0.1, deadline - time.time())
        try:
            line = self._queue.get(timeout=min(remaining, 90.0))
        except queue.Empty as exc:
            self.stop()
            raise STTError(ErrorCode.MODEL_TIMEOUT, "SenseVoice transcription timed out") from exc
        if line is None:
            self.stop()
            raise STTError(ErrorCode.MODEL_UNAVAILABLE, "SenseVoice worker exited")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            self.stop()
            raise STTError(ErrorCode.MODEL_UNAVAILABLE, "invalid SenseVoice worker response") from exc
        if "error" in msg:
            raise STTError(ErrorCode.MODEL_UNAVAILABLE, str(msg["error"]))
        normalized, language = clean_model_text(msg.get("text", ""))
        return Transcript(
            raw_model_text=msg.get("text", ""),
            normalized_text=normalized,
            language=msg.get("language") or language,
            segments=msg.get("segments", []),
            confidence=None,
            model_digest=msg.get("model_digest") or self._digest,
            latency_ms=int(msg.get("latency_ms", 0)),
        )

    def stop(self) -> None:
        with self._lock:
            self._ready = False
            if self.proc:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
                except (OSError,subprocess.TimeoutExpired):
                    pass
                self.proc = None

    def health(self) -> dict[str, Any]:
        return {
            "backend": "sensevoice",
            "available": self._ready,
            "model": self.model,
            "model_digest": self._digest,
            "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
        }


class SenseVoiceSTT:
    def __init__(self, model: str = SENSEVOICE_MODEL):
        self.worker = SenseVoiceWorker(model)
        self.model_digest: str | None = None

    def transcribe(self, wav_path: Path, deadline: float) -> Transcript:
        validation = validate_audio(wav_path)
        if not validation.is_speech_like():
            raise STTError(ErrorCode.NO_SPEECH, "no speech detected")
        transcript = self.worker.transcribe(wav_path, deadline)
        transcript.quality_flags = validation.__dict__
        self.model_digest = transcript.model_digest
        if not transcript.normalized_text:
            raise STTError(ErrorCode.NO_SPEECH, "empty transcription")
        return transcript

    def health(self) -> dict[str, Any]:
        if not self.worker._ready:
            try:
                self.worker.start()
            except STTError as exc:
                return {"backend": "sensevoice", "available": False,
                        "model": self.model_digest or SENSEVOICE_MODEL,
                        "reason": exc.summary}
        return self.worker.health()


def build_stt(backend: str = "auto") -> STTAdapter:
    if backend in {"fake", "none"}:
        return FakeSTT()
    # "auto" selects SenseVoice when the runtime is importable; it does not
    # silently substitute a network service.
    try:
        import importlib.util
        if importlib.util.find_spec("funasr") is None:
            raise STTError(ErrorCode.MODEL_UNAVAILABLE, "funasr is not installed")
        return SenseVoiceSTT()
    except STTError:
        return UnavailableSTT("SenseVoice/FunASR is not installed")


class UnavailableSTT:
    model_digest = None

    def __init__(self, reason: str):
        self.reason = reason

    def transcribe(self, wav_path: Path, deadline: float) -> Transcript:
        raise STTError(ErrorCode.MODEL_UNAVAILABLE, self.reason)

    def health(self) -> dict[str, Any]:
        return {"backend": "unavailable", "available": False, "reason": self.reason}
