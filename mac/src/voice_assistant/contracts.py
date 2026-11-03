"""Strict, versioned contracts for the Desk-Mate Bilibili voice feature.

This module is the single source of truth for:
  * control-plane JSON messages (MASTER_SPEC §6.3)
  * the local decision proposal / validated plan (MASTER_SPEC §7)
  * canonical plan hashing used by replay and R2 confirmation

Everything is deliberately strict: unknown keys, booleans where an integer is
required, non-finite numbers, duplicate JSON keys, over-long strings and
forward/cyclic dependencies are rejected rather than coerced.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

PROTOCOL_VERSION = 1
CAPABILITY_VERSION = "r1-2026-10-17"
SCHEMA_VERSION = 1
MAX_CONTROL_BYTES = 2048
MAX_FRAME_PCM_BYTES = 640
MAX_PCM_BYTES = 1_920_000
MAX_FRAMES = 3_000
MAX_CAPTURE_MS = 60_000
MIN_HOLD_MS = 500
CANCEL_WINDOW_MS = 800
MAX_STEPS = 5
MAX_ACTIONS = 18

R1_ACTIONS: tuple[str, ...] = (
    "bilibili.open",
    "bilibili.search",
    "bilibili.play",
    "bilibili.pause",
    "bilibili.resume",
)
R2_ACTIONS: tuple[str, ...] = (
    "bilibili.next",
    "bilibili.prev",
    "bilibili.home",
    "bilibili.favorite",
    "bilibili.like",
    "bilibili.coin",
    "bilibili.comments",
    "bilibili.seek",
    "bilibili.history",
    "bilibili.danmaku_on",
    "bilibili.danmaku_off",
    "bilibili.send_danmaku",
    "system.open_app",
    "system.vol_up",
    "system.vol_down",
    "system.volume",
    "system.mute",
    "system.screenshot",
)
ALL_ACTIONS: tuple[str, ...] = R1_ACTIONS + R2_ACTIONS

# Actions the host advertises to the device by default. The media controls are
# executed against the live player and verified by reading the UI back.
ENABLED_ACTIONS: tuple[str, ...] = R1_ACTIONS + (
    "bilibili.like",
    "bilibili.coin",
    "bilibili.favorite",
    "bilibili.comments",
    "bilibili.seek",
    "bilibili.send_danmaku",
    "bilibili.next",
    "bilibili.prev",
    "bilibili.home",
    "system.vol_up",
    "system.vol_down",
    "system.volume",
)
REPLAY_ACTIONS: tuple[str, ...] = (
    "bilibili.pause",
    "bilibili.resume",
    "bilibili.danmaku_on",
    "bilibili.danmaku_off",
    "system.mute",
)
# Actions which are pure set-state and safe to replay without a public effect.
ACCOUNT_ACTIONS = {"bilibili.favorite", "bilibili.like", "bilibili.send_danmaku"}
PUBLIC_ACTIONS = {"bilibili.send_danmaku"}
# The only (action, parameter) pairs that may contain output placeholders.
REFERENCE_FIELDS = {
    ("bilibili.play", "search_result_id"),
    ("bilibili.favorite", "video_id"),
    ("bilibili.like", "video_id"),
    ("bilibili.send_danmaku", "video_id"),
}

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
BV_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class ContractError(ValueError):
    """Any protocol/schema violation. `code` is a stable MASTER_SPEC code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CommandState(str, Enum):
    STAGING = "STAGING"
    SEALED = "SEALED"
    COMMITTED = "COMMITTED"
    TRANSCRIBING = "TRANSCRIBING"
    PLANNING = "PLANNING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    ABANDONED = "ABANDONED"
    INTERRUPTED = "INTERRUPTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


TERMINAL_STATES = {
    CommandState.SUCCEEDED,
    CommandState.PARTIAL,
    CommandState.FAILED,
    CommandState.CANCELED,
    CommandState.ABANDONED,
    CommandState.INTERRUPTED,
    CommandState.OUTCOME_UNKNOWN,
}


class StepState(str, Enum):
    PENDING = "PENDING"
    PREPARED = "PREPARED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    NOOP_VERIFIED = "NOOP_VERIFIED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class ErrorCode(str, Enum):
    READY_FALSE = "READY_FALSE"
    BUSY = "BUSY"
    AUTH_FAILED = "AUTH_FAILED"
    PAIRING_REQUIRED = "PAIRING_REQUIRED"
    CLOCK_OR_CERT_ERROR = "CLOCK_OR_CERT_ERROR"
    DISCOVERY_FAILED = "DISCOVERY_FAILED"
    WIFI_LOST = "WIFI_LOST"
    MAC_OFFLINE = "MAC_OFFLINE"
    MAC_LOCKED = "MAC_LOCKED"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    ID_CONFLICT = "ID_CONFLICT"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    AUDIO_INVALID = "AUDIO_INVALID"
    AUDIO_OVERRUN = "AUDIO_OVERRUN"
    UPLOAD_TIMEOUT = "UPLOAD_TIMEOUT"
    HASH_MISMATCH = "HASH_MISMATCH"
    SHORT_PRESS = "SHORT_PRESS"
    USER_CANCELED = "USER_CANCELED"
    RECORDING_TIMEOUT = "RECORDING_TIMEOUT"
    DISK_FULL = "DISK_FULL"
    STORAGE_ERROR = "STORAGE_ERROR"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    NO_SPEECH = "NO_SPEECH"
    INVALID_PLAN = "INVALID_PLAN"
    CLARIFY_NEEDED = "CLARIFY_NEEDED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    TARGET_STALE = "TARGET_STALE"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONFIRM_EXPIRED = "CONFIRM_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    ACTION_FAILED = "ACTION_FAILED"
    INTERRUPTED = "INTERRUPTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class CancelReason(str, Enum):
    user = "user"
    short_press = "short_press"
    page_exit = "page_exit"
    timeout = "timeout"
    audio_error = "audio_error"
    network_error = "network_error"


class CancelOutcome(str, Enum):
    canceled = "canceled"
    too_late = "too_late"
    already_terminal = "already_terminal"


class DecisionStatus(str, Enum):
    ok = "ok"
    clarify_needed = "clarify_needed"
    out_of_scope = "out_of_scope"


class ReasonCode(str, Enum):
    none = "none"
    ambiguous = "ambiguous"
    missing_target = "missing_target"
    unsupported = "unsupported"
    too_many_steps = "too_many_steps"
    policy_denied = "policy_denied"


class AudioFormat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    encoding: Literal["s16le"] = "s16le"
    rate: Literal[16000] = 16000
    channels: Literal[1] = 1


AUDIO_FORMAT = AudioFormat()


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_pcm_bytes: int = MAX_PCM_BYTES
    max_frames: int = MAX_FRAMES
    frame_pcm_bytes: int = MAX_FRAME_PCM_BYTES
    max_control_bytes: int = MAX_CONTROL_BYTES
    max_capture_ms: int = MAX_CAPTURE_MS
    min_hold_ms: int = MIN_HOLD_MS
    cancel_window_ms: int = CANCEL_WINDOW_MS


def _unwrap(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _unwrap(get_args(annotation)[0])
    return annotation


def _expects_int(annotation: Any) -> bool:
    annotation = _unwrap(annotation)
    origin = get_origin(annotation)
    if origin is Union:
        return any(_expects_int(arg) for arg in get_args(annotation) if arg is not type(None))
    if origin is Literal:
        return any(isinstance(v, int) and not isinstance(v, bool) for v in get_args(annotation))
    return annotation is int


def _expects_bool(annotation: Any) -> bool:
    annotation = _unwrap(annotation)
    origin = get_origin(annotation)
    if origin is Union:
        return any(_expects_bool(arg) for arg in get_args(annotation) if arg is not type(None))
    return annotation is bool


def _expects_float(annotation: Any) -> bool:
    annotation = _unwrap(annotation)
    origin = get_origin(annotation)
    if origin is Union:
        return any(_expects_float(arg) for arg in get_args(annotation) if arg is not type(None))
    return annotation is float


class _Strict(BaseModel):
    """Unknown fields are forbidden and numeric/bool types are never coerced.

    Enums and UUIDs must accept their JSON string representation, so pydantic's
    lax wire decoding is used only for those; the before-validator below rejects
    bools-as-ints, numeric strings for integers, and non-bools for booleans.
    """

    model_config = ConfigDict(extra="forbid", strict=False)

    @model_validator(mode="before")
    @classmethod
    def _reject_wire_coercion(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for name, field in cls.model_fields.items():
            if name not in data:
                continue
            value = data[name]
            if value is None:
                continue
            ann = field.annotation
            if _expects_bool(ann) and not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
            if _expects_int(ann) and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{name} must be an integer")
            if _expects_float(ann) and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError(f"{name} must be a finite number")
        return data


def _check_bytes(value: str, limit: int, field: str) -> str:
    raw = value.encode("utf-8")
    if len(raw) > limit:
        raise ValueError(f"{field} exceeds {limit} UTF-8 bytes")
    return value


def check_hex64(value: str) -> str:
    if not HEX64_RE.match(value):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    return value


def validate_uuid_text(value: str) -> str:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid UUID") from exc
    return value


class Envelope(_Strict):
    v: Literal[1]
    type: str
    msg_id: int = Field(ge=1)
    command_id: Union[UUID, None] = None


# ---------------------------------------------------------------------------
# Client -> server messages
# ---------------------------------------------------------------------------


class Hello(_Strict):
    v: Literal[1] = 1
    type: Literal["hello"] = "hello"
    msg_id: int = Field(ge=1)
    device_id: UUID
    server_id: UUID
    firmware_version: str = Field(min_length=1, max_length=64)
    boot_id: UUID

    @field_validator("firmware_version")
    @classmethod
    def _ascii(cls, v: str) -> str:
        if any(ord(c) > 127 for c in v):
            raise ValueError("firmware_version must be ASCII")
        return v


class Begin(_Strict):
    v: Literal[1] = 1
    type: Literal["begin"] = "begin"
    msg_id: int = Field(ge=1)
    command_id: UUID
    kind: Literal["audio"] = "audio"
    format: AudioFormat
    trigger: Literal["esp32_ptt"] = "esp32_ptt"
    # Wire encoding of the binary frames. The logical audio stays
    # s16le/16000/mono; "ima4" only describes how those PCM bytes travel.
    codec: Literal["s16le", "ima4"] = "s16le"

    @model_validator(mode="after")
    def _format_fixed(self) -> "Begin":
        if self.format != AUDIO_FORMAT:
            raise ValueError("v1 audio format is fixed to s16le/16000/mono")
        return self


class ReplayPrepare(_Strict):
    v: Literal[1] = 1
    type: Literal["replay_prepare"] = "replay_prepare"
    msg_id: int = Field(ge=1)
    command_id: UUID
    kind: Literal["replay"] = "replay"
    replay_of: UUID


class Seal(_Strict):
    v: Literal[1] = 1
    type: Literal["seal"] = "seal"
    msg_id: int = Field(ge=1)
    command_id: UUID
    frame_count: int = Field(ge=1, le=MAX_FRAMES)
    pcm_bytes: int = Field(ge=2, le=MAX_PCM_BYTES)
    sha256: str

    _sha = field_validator("sha256")(classmethod(lambda cls, v: check_hex64(v)))


class Commit(_Strict):
    v: Literal[1] = 1
    type: Literal["commit"] = "commit"
    msg_id: int = Field(ge=1)
    command_id: UUID
    sha256: str

    _sha = field_validator("sha256")(classmethod(lambda cls, v: check_hex64(v)))


class Cancel(_Strict):
    v: Literal[1] = 1
    type: Literal["cancel"] = "cancel"
    msg_id: int = Field(ge=1)
    command_id: UUID
    reason: CancelReason


class Feedback(_Strict):
    v: Literal[1] = 1
    type: Literal["feedback"] = "feedback"
    msg_id: int = Field(ge=1)
    command_id: UUID
    rating: Literal["good", "bad", "unfeedback"]
    dismissed_by: str = Field(min_length=1, max_length=32)
    touch_count: int = Field(ge=0, le=3)
    countdown_at_dismiss: int = Field(ge=0, le=60000)
    page: int | None = Field(default=None, ge=0, le=2)


class StatusQuery(_Strict):
    v: Literal[1] = 1
    type: Literal["status"] = "status"
    msg_id: int = Field(ge=1)
    command_id: UUID


class Ping(_Strict):
    v: Literal[1] = 1
    type: Literal["ping"] = "ping"
    msg_id: int = Field(ge=1)


class Confirm(_Strict):
    v: Literal[1] = 1
    type: Literal["confirm"] = "confirm"
    msg_id: int = Field(ge=1)
    command_id: UUID
    confirmation_id: UUID
    plan_hash: str

    _sha = field_validator("plan_hash")(classmethod(lambda cls, v: check_hex64(v)))


ClientMessage = Union[
    Hello, Begin, ReplayPrepare, Seal, Commit, Cancel, StatusQuery, Ping, Confirm, Feedback
]

MESSAGE_TYPES: dict[str, type[BaseModel]] = {
    "hello": Hello,
    "begin": Begin,
    "replay_prepare": ReplayPrepare,
    "seal": Seal,
    "commit": Commit,
    "cancel": Cancel,
    "status": StatusQuery,
    "ping": Ping,
    "confirm": Confirm,
    "feedback": Feedback,
}


# ---------------------------------------------------------------------------
# Server -> client messages
# ---------------------------------------------------------------------------


class HelloAck(_Strict):
    v: Literal[1] = 1
    type: Literal["hello_ack"] = "hello_ack"
    msg_id: int = Field(ge=1)
    in_reply_to: int = Field(ge=1)
    server_id: UUID
    device_id: UUID
    protocol: Literal[1] = 1
    ready: bool
    code: Union[ErrorCode, None] = None
    audio_format: AudioFormat = AUDIO_FORMAT
    capabilities: list[str] = Field(max_length=MAX_ACTIONS)
    limits: Limits = Field(default_factory=Limits)
    capability_version: str = Field(min_length=1, max_length=64)


class BeginAck(_Strict):
    v: Literal[1] = 1
    type: Literal["begin_ack"] = "begin_ack"
    msg_id: int = Field(ge=1)
    in_reply_to: int = Field(ge=1)
    command_id: UUID
    state: Literal["STAGING"] = "STAGING"
    next_seq: Literal[0] = 0
    pcm_bytes: Literal[0] = 0


class AppendAck(_Strict):
    v: Literal[1] = 1
    type: Literal["append_ack"] = "append_ack"
    msg_id: Literal[0] = 0
    command_id: UUID
    next_seq: int = Field(ge=0)
    pcm_bytes: int = Field(ge=0)


class SealAck(_Strict):
    v: Literal[1] = 1
    type: Literal["seal_ack"] = "seal_ack"
    msg_id: int = Field(ge=1)
    in_reply_to: int = Field(ge=1)
    command_id: UUID
    state: Literal["SEALED"] = "SEALED"
    frame_count: int = Field(ge=1, le=MAX_FRAMES)
    pcm_bytes: int = Field(ge=2, le=MAX_PCM_BYTES)
    sha256: str

    _sha = field_validator("sha256")(classmethod(lambda cls, v: check_hex64(v)))


class ReplayReady(_Strict):
    v: Literal[1] = 1
    type: Literal["replay_ready"] = "replay_ready"
    msg_id: int = Field(ge=1)
    in_reply_to: int = Field(ge=1)
    command_id: UUID
    state: Literal["SEALED"] = "SEALED"
    replay_of: UUID
    sha256: str
    expires_in_ms: int = Field(ge=1, le=10_000)
    summary: str = Field(max_length=128)

    _sha = field_validator("sha256")(classmethod(lambda cls, v: check_hex64(v)))


class CommitAck(_Strict):
    v: Literal[1] = 1
    type: Literal["commit_ack"] = "commit_ack"
    msg_id: int = Field(ge=1)
    in_reply_to: int = Field(ge=1)
    command_id: UUID
    state: CommandState
    code: Union[ErrorCode, None] = None


class CancelAck(_Strict):
    v: Literal[1] = 1
    type: Literal["cancel_ack"] = "cancel_ack"
    msg_id: int = Field(ge=1)
    in_reply_to: int = Field(ge=1)
    command_id: UUID
    state: CommandState
    outcome: CancelOutcome
    code: Union[ErrorCode, None] = None


class StepResult(_Strict):
    step_index: int = Field(ge=0, le=4)
    status: StepState
    code: Union[ErrorCode, None] = None


class Result(_Strict):
    v: Literal[1] = 1
    type: Literal["result"] = "result"
    msg_id: int = Field(ge=0)
    command_id: UUID
    state: CommandState
    code: Union[ErrorCode, None] = None
    summary: str = Field(max_length=128)
    # Short ASCII label for the device screen (Montserrat has no CJK glyphs).
    # The host owns the wording so the device never guesses from Chinese text.
    detail: str = Field(default="", max_length=40)
    step_results: list[StepResult] = Field(max_length=MAX_STEPS)
    replayable: bool


class FeedbackAck(_Strict):
    v: Literal[1] = 1
    type: Literal["feedback_ack"] = "feedback_ack"
    msg_id: int = Field(ge=0)
    in_reply_to: int = Field(ge=1)
    command_id: UUID
    accepted: bool
    source: Literal["device", "server"] = "device"


class StatusSnapshot(_Strict):
    v: Literal[1] = 1
    type: Literal["status_snapshot"] = "status_snapshot"
    msg_id: int = Field(ge=1)
    in_reply_to: int = Field(ge=1)
    command_id: UUID
    known: bool
    state: Union[CommandState, None] = None
    terminal: bool = False
    code: Union[ErrorCode, None] = None
    result: Union[Result, None] = None


class Progress(_Strict):
    v: Literal[1] = 1
    type: Literal["progress"] = "progress"
    msg_id: Literal[0] = 0
    command_id: UUID
    stage: Literal["transcribing", "planning", "executing"]
    step_index: Union[int, None] = Field(default=None, ge=0, le=4)


class ErrorMessage(_Strict):
    v: Literal[1] = 1
    type: Literal["error"] = "error"
    msg_id: int = Field(ge=1)
    in_reply_to: Union[int, None] = None
    command_id: Union[UUID, None] = None
    code: ErrorCode
    summary: str = Field(max_length=128)
    fatal: bool = False


class Pong(_Strict):
    v: Literal[1] = 1
    type: Literal["pong"] = "pong"
    msg_id: int = Field(ge=1)
    in_reply_to: int = Field(ge=1)
    ready: bool
    code: Union[ErrorCode, None] = None


class ConfirmPreview(_Strict):
    text: str = Field(min_length=1, max_length=30)
    video_id: str
    target_label: str = Field(max_length=64)
    account_label: str = Field(max_length=64)


class ConfirmRequired(_Strict):
    v: Literal[1] = 1
    type: Literal["confirm_required"] = "confirm_required"
    msg_id: int = Field(ge=0)
    command_id: UUID
    confirmation_id: UUID
    plan_hash: str
    preview: ConfirmPreview
    expires_in_ms: int = Field(ge=1, le=15_000)

    _sha = field_validator("plan_hash")(classmethod(lambda cls, v: check_hex64(v)))


ServerMessage = Union[
    HelloAck, BeginAck, AppendAck, SealAck, ReplayReady, CommitAck, CancelAck,
    Result, StatusSnapshot, Progress, ErrorMessage, Pong, ConfirmRequired,
]


# ---------------------------------------------------------------------------
# Decision / action contracts
# ---------------------------------------------------------------------------


def _chars_and_bytes(value: str, chars: int, byte_limit: int, field: str) -> str:
    if len(value) < 1:
        raise ValueError(f"{field} must not be empty")
    if len(value) > chars:
        raise ValueError(f"{field} exceeds {chars} characters")
    _check_bytes(value, byte_limit, field)
    return value


QueryStr = Annotated[str, StringConstraints(min_length=1, max_length=128)]
DanmakuText = Annotated[str, StringConstraints(min_length=1, max_length=30)]


class _Params(_Strict):
    pass


class EmptyParams(_Params):
    pass


class SearchParams(_Params):
    query: QueryStr

    @field_validator("query")
    @classmethod
    def _bounded(cls, v: str) -> str:
        return _chars_and_bytes(v, 128, 512, "query")


class PlayParams(_Params):
    bv: Union[str, None] = None
    index: Union[int, None] = Field(default=None, ge=1, le=50)
    search_result_id: Union[str, None] = None
    query: Union[str, None] = None

    @field_validator("bv")
    @classmethod
    def _bv(cls, v: str | None) -> str | None:
        if v is not None and not BV_RE.match(v):
            raise ValueError("bv must be BV followed by 10 ASCII alphanumerics")
        return v

    @field_validator("search_result_id")
    @classmethod
    def _sid(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("$"):
            validate_uuid_text(v)
        return v

    @field_validator("query")
    @classmethod
    def _query(cls, v: str | None) -> str | None:
        if v is not None:
            _chars_and_bytes(v, 128, 512, "query")
        return v

    @model_validator(mode="after")
    def _exactly_one(self) -> "PlayParams":
        bv_form = self.bv is not None
        index_form = self.index is not None or self.search_result_id is not None
        query_form = self.query is not None
        if sum([bv_form, index_form, query_form]) != 1:
            raise ValueError("play requires exactly one of bv, index+search_result_id, query")
        if index_form and (self.index is None or self.search_result_id is None):
            raise ValueError("play(index) requires both index and search_result_id")
        return self


class OpenAppParams(_Params):
    app_id: str

    @field_validator("app_id")
    @classmethod
    def _alias(cls, v: str) -> str:
        if not ALIAS_RE.match(v):
            raise ValueError("app_id must be a configured lowercase alias")
        return v


class VolumeParams(_Params):
    delta: int = Field(default=5, ge=1, le=10)


class SendDanmakuParams(_Params):
    text: DanmakuText
    video_id: Union[str, None] = None

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        return _chars_and_bytes(v, 30, 240, "text")

    @field_validator("video_id")
    @classmethod
    def _vid(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("$") and not BV_RE.match(v):
            raise ValueError("video_id must be a BV id or a step reference")
        return v


ACTION_PARAM_TYPES: dict[str, type[_Params]] = {
    "bilibili.open": EmptyParams,
    "bilibili.search": SearchParams,
    "bilibili.play": PlayParams,
    "bilibili.pause": EmptyParams,
    "bilibili.resume": EmptyParams,
    "bilibili.next": EmptyParams,
    "bilibili.prev": EmptyParams,
    "bilibili.home": EmptyParams,
    "bilibili.favorite": _Params,  # optional video_id handled separately
    "bilibili.like": _Params,
    "bilibili.history": EmptyParams,
    "bilibili.danmaku_on": EmptyParams,
    "bilibili.danmaku_off": EmptyParams,
    "bilibili.send_danmaku": SendDanmakuParams,
    "system.open_app": OpenAppParams,
    "system.vol_up": VolumeParams,
    "system.vol_down": VolumeParams,
    "system.mute": EmptyParams,
    "system.screenshot": EmptyParams,
}


class CoinParams(_Params):
    count: int = Field(default=1, ge=1, le=2)


class CommentsParams(_Params):
    open: bool = True


class SeekParams(_Params):
    delta_s: Union[int, None] = Field(default=None, ge=-3600, le=3600)
    target_s: Union[int, None] = Field(default=None, ge=0, le=86400)

    @model_validator(mode="after")
    def _one_of(self) -> "SeekParams":
        if (self.delta_s is None) == (self.target_s is None):
            raise ValueError("seek requires exactly one of delta_s or target_s")
        return self


class VolumeTargetParams(_Params):
    target: int = Field(ge=0, le=100)


class FavoriteParams(_Params):
    video_id: Union[str, None] = None
    folder: Union[str, None] = Field(default=None, max_length=32)

    @field_validator("folder")
    @classmethod
    def _folder(cls, v: str | None) -> str | None:
        if v is not None:
            _chars_and_bytes(v, 32, 96, "folder")
        return v

    @field_validator("video_id")
    @classmethod
    def _vid(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("$") and not BV_RE.match(v):
            raise ValueError("video_id must be a BV id or a step reference")
        return v


ACTION_PARAM_TYPES["bilibili.favorite"] = FavoriteParams
ACTION_PARAM_TYPES["bilibili.like"] = FavoriteParams
ACTION_PARAM_TYPES["bilibili.coin"] = CoinParams
ACTION_PARAM_TYPES["bilibili.comments"] = CommentsParams
ACTION_PARAM_TYPES["bilibili.seek"] = SeekParams
ACTION_PARAM_TYPES["system.volume"] = VolumeTargetParams


class ActionStep(_Strict):
    id: int = Field(ge=0, le=MAX_STEPS - 1)
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    after: list[int] = Field(default_factory=list, max_length=MAX_STEPS - 1)

    @field_validator("action")
    @classmethod
    def _known_action(cls, v: str) -> str:
        if v not in ALL_ACTIONS:
            raise ValueError(f"unknown action {v!r}")
        return v

    @model_validator(mode="after")
    def _params(self) -> "ActionStep":
        model = ACTION_PARAM_TYPES[self.action]
        # Reject unknown params and wrong types here, before any policy step.
        self.params = model.model_validate(self.params).model_dump(exclude_none=True)
        return self


class DecisionProposal(_Strict):
    schema_version: Literal[1] = 1
    status: DecisionStatus
    reason_code: ReasonCode
    clarification: Union[str, None] = None
    model_confidence: Union[float, None] = None
    actions: list[ActionStep] = Field(max_length=MAX_STEPS)

    @field_validator("clarification")
    @classmethod
    def _clarification(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _chars_and_bytes(v, 128, 512, "clarification")

    @field_validator("model_confidence")
    @classmethod
    def _confidence(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError("model_confidence must be a finite number or null")
        if not math.isfinite(float(v)) or not (0.0 <= float(v) <= 1.0):
            raise ValueError("model_confidence must be within [0,1]")
        return float(v)

    @model_validator(mode="after")
    def _shape(self) -> "DecisionProposal":
        if self.status is DecisionStatus.ok:
            if self.reason_code is not ReasonCode.none:
                raise ValueError("status=ok requires reason_code=none")
            if not (1 <= len(self.actions) <= MAX_STEPS):
                raise ValueError("status=ok requires 1..5 actions")
        else:
            if self.actions:
                raise ValueError("non-ok status requires actions=[]")
            if self.reason_code is ReasonCode.none:
                raise ValueError("non-ok status requires a non-none reason_code")
        ids = [a.id for a in self.actions]
        if ids != list(range(len(self.actions))):
            raise ValueError("action ids must be contiguous from 0")
        for a in self.actions:
            if len(set(a.after)) != len(a.after):
                raise ValueError("after must not contain duplicates")
            for ref in a.after:
                if ref < 0 or ref >= a.id:
                    raise ValueError("after must only reference earlier steps")
        return self


# ---------------------------------------------------------------------------
# Validated plan / execution contracts
# ---------------------------------------------------------------------------


class ValidatedAction(_Strict):
    action: str
    params: dict[str, Any]
    after: list[int]
    binding: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _params(self) -> "ValidatedAction":
        if self.action not in ALL_ACTIONS:
            raise ValueError("unknown action")
        model = ACTION_PARAM_TYPES[self.action]
        self.params = model.model_validate(self.params).model_dump(exclude_none=True)
        return self


class ValidatedPlan(_Strict):
    command_id: UUID
    device_id: UUID
    capability_version: str
    actions: list[ValidatedAction]
    expires_at: float

    def canonical(self) -> dict[str, Any]:
        return {
            "command_id": str(self.command_id),
            "device_id": str(self.device_id),
            "capability_version": self.capability_version,
            "expires_at": int(self.expires_at),
            "actions": [
                {"action": a.action, "params": a.params, "after": sorted(a.after), "binding": a.binding}
                for a in self.actions
            ],
        }


class ActionOutput(_Strict):
    video_id: Union[str, None] = None
    search_result_id: Union[str, None] = None
    app_id: Union[str, None] = None
    player_generation: Union[str, None] = None
    view_generation: Union[str, None] = None


class ActionResult(_Strict):
    step_index: int = Field(ge=0, le=4)
    status: StepState
    code: Union[ErrorCode, None] = None
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = Field(ge=0)


class SearchResult(_Strict):
    video_id: str
    title: str = Field(max_length=256)
    index: int = Field(ge=1, le=50)
    # Optional UI grid metadata; old snapshots remain valid without it.
    row: int | None = Field(default=None, ge=1, le=50)
    column: int | None = Field(default=None, ge=1, le=50)


class SearchSnapshot(_Strict):
    search_result_id: UUID
    results: list[SearchResult] = Field(max_length=50)
    observed_at: float
    expires_at: float
    generation: UUID
    query: str


class PlayerSnapshot(_Strict):
    video_id: Union[str, None] = None
    generation: UUID
    paused: bool
    position_ms: int = Field(ge=0)
    observed_at: float
    expires_at: float
    account_id: Union[str, None] = None


class TrustedContext(_Strict):
    now: float
    search_snapshot: Union[SearchSnapshot, None] = None
    player_snapshot: Union[PlayerSnapshot, None] = None
    recent_commands: list[dict[str, Any]] = Field(default_factory=list, max_length=10)


# ---------------------------------------------------------------------------
# Canonical hashing and JSON decoding
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> bytes:
    """Canonical UTF-8 JSON: sorted keys, no whitespace, no NaN/floats."""
    def reject(v: Any) -> None:
        if isinstance(v, float):
            raise ValueError("canonical plans must not contain floats")
        if isinstance(v, list):
            for item in v:
                reject(item)
        elif isinstance(v, dict):
            for k, item in v.items():
                if not isinstance(k, str):
                    raise ValueError("canonical JSON keys must be strings")
                reject(item)

    reject(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_digest(plan: ValidatedPlan) -> str:
    return sha256_hex(canonical_json(plan.canonical()))


def replay_plan_digest(
    command_id: UUID,
    device_id: UUID,
    replay_of: UUID,
    action: str,
    params: dict[str, Any],
    target_video_id: str | None,
    player_generation: str | None,
    account_id: str | None,
    capability_version: str,
    frozen_at: int,
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "kind": "replay",
                "command_id": str(command_id),
                "device_id": str(device_id),
                "replay_of": str(replay_of),
                "action": action,
                "params": params,
                "target_video_id": target_video_id,
                "player_generation": player_generation,
                "account_id": account_id,
                "capability_version": capability_version,
                "frozen_at": int(frozen_at),
            }
        )
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ContractError("PROTOCOL_ERROR", f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _reject_constant(value: str) -> Any:
    raise ContractError("PROTOCOL_ERROR", f"non-finite JSON number: {value}")


def decode_json(raw: bytes | str, max_bytes: int = MAX_CONTROL_BYTES) -> Any:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > max_bytes:
        raise ContractError("PROTOCOL_ERROR", "control message exceeds size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("PROTOCOL_ERROR", "control message is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ContractError("PROTOCOL_ERROR", f"invalid JSON: {exc}") from exc


def parse_client_message(raw: bytes | str | dict[str, Any]) -> ClientMessage:
    obj = raw if isinstance(raw, dict) else decode_json(raw)
    if not isinstance(obj, dict):
        raise ContractError("PROTOCOL_ERROR", "control message must be a JSON object")
    msg_type = obj.get("type")
    model = MESSAGE_TYPES.get(msg_type)
    if model is None:
        raise ContractError("PROTOCOL_ERROR", f"unknown message type: {msg_type!r}")
    if obj.get("v") != PROTOCOL_VERSION:
        raise ContractError("PROTOCOL_ERROR", "unsupported protocol version")
    try:
        return model.model_validate(obj)  # type: ignore[return-value]
    except Exception as exc:  # pydantic.ValidationError
        raise ContractError("PROTOCOL_ERROR", f"invalid {msg_type} message: {exc}") from exc


def validate_decision_proposal(raw: dict[str, Any] | str | bytes) -> DecisionProposal:
    if not isinstance(raw, dict):
        raw = decode_json(raw, max_bytes=32_768)
    try:
        return DecisionProposal.model_validate(raw)
    except Exception as exc:
        raise ContractError("INVALID_PLAN", f"decision proposal rejected: {exc}") from exc


def validate_plan_structure(proposal: DecisionProposal, enabled_actions: tuple[str, ...]) -> None:
    """Structural + capability pre-check before policy/context resolution."""
    if proposal.status is not DecisionStatus.ok:
        return
    if len(proposal.actions) > MAX_STEPS:
        raise ContractError("INVALID_PLAN", "too_many_steps")
    for step in proposal.actions:
        if step.action not in enabled_actions:
            raise ContractError("CAPABILITY_UNAVAILABLE", f"action not enabled: {step.action}")
        # A dependency set is after ∪ parameter references; parameters can only
        # reference earlier step outputs (checked by planner, not the model).
        deps = set(step.after)
        for key, value in step.params.items():
            if (step.action, key) in REFERENCE_FIELDS and isinstance(value, str) and value.startswith("$"):
                ref = parse_reference(value)
                if ref is not None:
                    deps.add(ref[0])
        for dep in deps:
            if dep >= step.id:
                raise ContractError("INVALID_PLAN", "dependency must point to an earlier step")
    return None


REF_RE = re.compile(r"^\$(prev|step_([0-9]+))\.output\.(video_id|search_result_id)$")


def parse_reference(value: str) -> tuple[int, str] | None:
    """Return (step_index, field) for a complete, finite placeholder.

    A string beginning with '$' that is not a valid reference is rejected by
    the planner; ordinary user text beginning with '$' never reaches this
    helper because only declared reference-capable parameters are inspected.
    """
    match = REF_RE.match(value)
    if not match:
        return None
    if match.group(1) == "prev":
        return (-1, match.group(3))  # resolved by planner to i-1
    return (int(match.group(2)), match.group(3))


def normalize_text(value: str) -> str:
    """Limited whitespace/punctuation normalization; never rewrites content."""
    value = unicodedata.normalize("NFC", value)
    return re.sub(r"\s+", " ", value).strip()


def is_safe_danmaku_text(value: str) -> bool:
    if len(value) > 30 or len(value.encode("utf-8")) > 240:
        return False
    if any(ord(c) < 0x20 for c in value):
        return False
    return True
