"""Authenticated command sessions shared by WSS and BLE channels."""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any
from uuid import UUID
from starlette.websockets import WebSocketDisconnect
from . import contracts
from .sessions import SessionError
from .contracts import (
    CancelAck,
    CancelOutcome,
    BeginAck,
    CommitAck,
    CommandState,
    ConfirmRequired,
    ErrorCode,
    ErrorMessage,
    Feedback,
    FeedbackAck,
    Hello,
    HelloAck,
    Limits,
    Ping,
    Pong,
    Progress,
    ReplayReady,
    Result,
    SealAck,
    StatusSnapshot,
    StepResult,
    StepState,
    TERMINAL_STATES,
    decode_json,
    parse_client_message,
)

log = logging.getLogger(__name__)
def _json(model):
    return model.model_dump_json()

def _error(msg_id: int, code: ErrorCode, summary: str, command_id: UUID | None = None,
           in_reply_to: int | None = None, fatal: bool = False) -> ErrorMessage:
    return ErrorMessage(
        msg_id=max(1, msg_id), in_reply_to=in_reply_to or (msg_id if msg_id else None),
        command_id=command_id, code=code, summary=summary[:128], fatal=fatal,
    )


async def serve_remote_session(state, ws, device_id: str):
    cfg, db, stages, service = state.cfg, state.db, state.stages, state.service
    await state.manager.attach(device_id, ws)
    hello_done = False
    active_command: str | None = None
    last_ack = 0.0
    pending_frames = 0
    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"] is not None:
                data = message["bytes"]
                if not hello_done or not active_command:
                    await ws.send_text(_json(_error(0, ErrorCode.PROTOCOL_ERROR, "binary frame outside begin")))
                    continue
                row = db.get_command(active_command)
                if not row or row["state"] != CommandState.STAGING.value:
                    await ws.send_text(_json(_error(0, ErrorCode.PROTOCOL_ERROR, "binary frame outside STAGING")))
                    continue
                try:
                    receipt = stages.append(active_command, data)
                except (SessionError, Exception) as exc:
                    code = getattr(exc, "code", ErrorCode.AUDIO_INVALID)
                    if not isinstance(code, ErrorCode):
                        try:
                            code = ErrorCode(str(code))
                        except Exception:
                            code = ErrorCode.AUDIO_INVALID
                    try:
                        stages.cancel(active_command, "audio_error")
                    except Exception:
                        pass
                    await ws.send_text(_json(_error(
                        0, code, str(exc)[:128], UUID(active_command), fatal=True,
                    )))
                    active_command = None
                    continue
                pending_frames += 1
                now = time.time()
                if receipt.should_ack or now - last_ack >= 0.2:
                    last_ack = now
                    pending_frames = 0
                    await ws.send_text(_json(contracts.AppendAck(
                        command_id=UUID(active_command), next_seq=receipt.next_seq,
                        pcm_bytes=receipt.pcm_bytes,
                    )))
                continue
            text = message.get("text")
            if text is None:
                continue
            try:
                parsed = parse_client_message(text)
            except contracts.ContractError as exc:
                await ws.send_text(_json(_error(0, ErrorCode.PROTOCOL_ERROR, str(exc)[:128], fatal=False)))
                continue
            if isinstance(parsed, Ping):
                ready, code = state.ready()
                await ws.send_text(_json(Pong(msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                                              ready=ready, code=code)))
                continue
            if isinstance(parsed, Hello):
                if hello_done:
                    await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.PROTOCOL_ERROR, "duplicate hello")))
                    continue
                if cfg.server is None or str(parsed.server_id) != cfg.server.server_id:
                    await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.PAIRING_REQUIRED,
                                                    "server identity mismatch", fatal=True)))
                    await ws.close(code=1008)
                    return
                if str(parsed.device_id) != device_id:
                    await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.AUTH_FAILED, "device id mismatch", fatal=True)))
                    await ws.close(code=1008)
                    return
                ready, code = state.ready()
                db.record_session(str(parsed.boot_id), device_id, parsed.firmware_version)
                await ws.send_text(_json(HelloAck(
                    msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                    server_id=UUID(cfg.server.server_id), device_id=UUID(device_id),
                    ready=ready, code=code, capabilities=list(state.capabilities()),
                    limits=Limits(), capability_version=contracts.CAPABILITY_VERSION,
                )))
                hello_done = True
                continue
            if not hello_done:
                await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.PROTOCOL_ERROR, "hello required", fatal=True)))
                await ws.close(code=1008)
                return
            command_id = getattr(parsed, "command_id", None)
            if command_id:
                owned = db.get_command(str(command_id))
                if owned and owned.get("device_id") != device_id:
                    await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.AUTH_FAILED,
                                                   "command belongs to another device", fatal=True)))
                    await ws.close(code=1008)
                    return
            if isinstance(parsed, Feedback):
                try:
                    row = db.get_command(str(parsed.command_id))
                    if not row:
                        raise ValueError("command not found")
                    if row.get("device_id") and str(row["device_id"]) != device_id:
                        raise ValueError("feedback device mismatch")
                    saved = db.record_feedback(
                        str(parsed.command_id), device_id,
                        rating=parsed.rating, dismissed_by=parsed.dismissed_by,
                        touch_count=parsed.touch_count,
                        countdown_at_dismiss=parsed.countdown_at_dismiss,
                        page=parsed.page, source="device",
                    )
                except Exception as exc:
                    await ws.send_text(_json(_error(
                        parsed.msg_id, ErrorCode.PROTOCOL_ERROR, str(exc)[:128],
                        parsed.command_id,
                    )))
                    continue
                await ws.send_text(_json(FeedbackAck(
                    msg_id=0, in_reply_to=parsed.msg_id,
                    command_id=parsed.command_id, accepted=True,
                    source=str(saved.get("source", "device")),
                )))
                try:
                    from . import feedback_analysis
                    if feedback_analysis.due(db) and not getattr(state, "analysis_running", False):
                        state.analysis_running = True
                        async def _analyze_feedback() -> None:
                            try:
                                from .auto_training import train_if_ready
                                await asyncio.to_thread(feedback_analysis.analyze, cfg, db)
                                await asyncio.to_thread(train_if_ready, cfg, db)
                            except Exception as exc:
                                log.warning("feedback auto-analysis failed: %s", exc)
                            finally:
                                state.analysis_running = False
                        task = asyncio.create_task(_analyze_feedback())
                        state.tasks.add(task)
                        task.add_done_callback(state.tasks.discard)
                except Exception as exc:
                    log.warning("feedback auto-analysis scheduling failed: %s", exc)
                continue
            if isinstance(parsed, contracts.Begin):
                if active_command or db.active_command():
                    await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.BUSY, "another command is active")))
                    continue
                try:
                    stages.begin(str(parsed.command_id), device_id, None)
                except Exception as exc:
                    code = getattr(exc, "code", ErrorCode.BUSY)
                    try:
                        code = ErrorCode(str(code))
                    except Exception:
                        code = ErrorCode.BUSY
                    await ws.send_text(_json(_error(parsed.msg_id, code, str(exc)[:128])))
                    continue
                active_command = str(parsed.command_id)
                last_ack = time.time()
                await ws.send_text(_json(BeginAck(
                    msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                    command_id=parsed.command_id,
                )))
                continue
            if isinstance(parsed, contracts.ReplayPrepare):
                if active_command or db.active_command():
                    await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.BUSY, "another command is active")))
                    continue
                try:
                    ready = service.prepare_replay(
                        str(parsed.command_id), device_id, str(parsed.replay_of), boot_id=None,
                    )
                except Exception as exc:
                    code = getattr(exc, "code", ErrorCode.COMMAND_NOT_FOUND)
                    try:
                        code = ErrorCode(str(code))
                    except Exception:
                        code = ErrorCode.COMMAND_NOT_FOUND
                    await ws.send_text(_json(_error(parsed.msg_id, code, str(exc)[:128])))
                    continue
                active_command = str(parsed.command_id)
                await ws.send_text(_json(ReplayReady(
                    msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                    command_id=parsed.command_id, replay_of=parsed.replay_of,
                    sha256=ready["sha256"], expires_in_ms=ready["expires_in_ms"],
                    summary=ready["summary"],
                )))
                continue
            if isinstance(parsed, contracts.Seal):
                if not active_command or str(parsed.command_id) != active_command:
                    await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.PROTOCOL_ERROR, "no matching active command")))
                    continue
                try:
                    row = stages.seal(active_command, parsed.frame_count, parsed.pcm_bytes, parsed.sha256)
                except Exception as exc:
                    code = getattr(exc, "code", ErrorCode.AUDIO_INVALID)
                    try:
                        code = ErrorCode(str(code))
                    except Exception:
                        code = ErrorCode.AUDIO_INVALID
                    await ws.send_text(_json(_error(parsed.msg_id, code, str(exc)[:128],
                                                    UUID(active_command))))
                    active_command = None
                    continue
                await ws.send_text(_json(SealAck(
                    msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                    command_id=parsed.command_id, frame_count=row["frame_count"],
                    pcm_bytes=row["pcm_bytes"], sha256=row["pcm_sha256"],
                )))
                continue
            if isinstance(parsed, contracts.Commit):
                if not active_command or str(parsed.command_id) != active_command:
                    await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.PROTOCOL_ERROR, "no matching active command")))
                    continue
                try:
                    row = stages.commit(active_command, parsed.sha256)
                except Exception as exc:
                    code = getattr(exc, "code", ErrorCode.STORAGE_ERROR)
                    try:
                        code = ErrorCode(str(code))
                    except Exception:
                        code = ErrorCode.STORAGE_ERROR
                    await ws.send_text(_json(_error(parsed.msg_id, code, str(exc)[:128],
                                                    UUID(active_command))))
                    continue
                await ws.send_text(_json(CommitAck(
                    msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                    command_id=parsed.command_id, state=CommandState(row["state"]), code=None,
                )))
                active_command = None
                notify = await state.notify_for(device_id)
                task = asyncio.create_task(state.pipeline.process(active_command or str(parsed.command_id), device_id, notify))
                state.tasks.add(task)
                task.add_done_callback(state.tasks.discard)
                continue
            if isinstance(parsed, contracts.Cancel):
                try:
                    outcome, st = stages.cancel(str(parsed.command_id), parsed.reason.value)
                except Exception as exc:
                    code = getattr(exc, "code", ErrorCode.COMMAND_NOT_FOUND)
                    try:
                        code = ErrorCode(str(code))
                    except Exception:
                        code = ErrorCode.COMMAND_NOT_FOUND
                    await ws.send_text(_json(_error(parsed.msg_id, code, str(exc)[:128])))
                    continue
                if active_command == str(parsed.command_id):
                    active_command = None
                await ws.send_text(_json(CancelAck(
                    msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                    command_id=parsed.command_id, state=st, outcome=CancelOutcome(outcome),
                )))
                continue
            if isinstance(parsed, contracts.StatusQuery):
                row = db.get_command(str(parsed.command_id))
                if not row:
                    await ws.send_text(_json(StatusSnapshot(
                        msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                        command_id=parsed.command_id, known=False, state=None,
                        terminal=False, code=ErrorCode.COMMAND_NOT_FOUND, result=None,
                    )))
                    continue
                st = CommandState(row["state"])
                result = state.pipeline.result_message(str(parsed.command_id)) if st in TERMINAL_STATES else None
                await ws.send_text(_json(StatusSnapshot(
                    msg_id=parsed.msg_id, in_reply_to=parsed.msg_id,
                    command_id=parsed.command_id, known=True, state=st,
                    terminal=st in TERMINAL_STATES,
                    code=ErrorCode(row["error_code"]) if row["error_code"] else None,
                    result=result,
                )))
                continue
            if isinstance(parsed, contracts.Confirm):
                # R2 confirmation execution is implemented by the R2 adapter
                # work; R1 fails closed rather than executing an unconfirmed plan.
                await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.CAPABILITY_UNAVAILABLE,
                                                "confirmation is not enabled in R1")))
                continue
            await ws.send_text(_json(_error(parsed.msg_id, ErrorCode.PROTOCOL_ERROR, "unsupported message")))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("remote session error: %s", exc)
    finally:
        if active_command:
            row = db.get_command(active_command)
            if row and row["state"] in (CommandState.STAGING.value, CommandState.SEALED.value):
                try:
                    stages.cancel(active_command, "network_error")
                except Exception:
                    pass
        await state.manager.detach(device_id, ws)
