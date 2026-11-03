"""Runtime pipeline: COMMITTED audio -> STT -> validated plan -> executor.

Only a durable COMMIT can enter this module. Every stage re-checks
cancellation, expiry and command state. Work runs in worker threads; the API
event loop is never blocked by a model or a GUI call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from . import contracts
from .config import AppConfig
from .intent import apply_policy_guard, has_supported_intent, personalization_candidate, rule_based_proposal, unsupported_intent_reason
from .contracts import (
    CommandState,
    ConfirmPreview,
    ConfirmRequired,
    DecisionStatus,
    ErrorCode,
    Progress,
    Result,
    SearchResult,
    SearchSnapshot,
    StepResult,
    StepState,
    TrustedContext,
    PlayerSnapshot,
    canonical_json,
    plan_digest,
)
from .decision import DecisionAdapter, DecisionError
from .executor import Executor
from .planner import PlanRejection, build_plan, requires_confirmation
from .sessions import CommandService
from .stt import STTAdapter, STTError, validate_audio
from .storage import Database, utc_now

Notify = Callable[[Any], Awaitable[None]]
log = logging.getLogger("voice_assistant.pipeline")
log.setLevel(logging.INFO)


def _is_ascii(text: str) -> bool:
    return bool(text) and all(ord(c) < 128 for c in text)


# Short labels for the 480x480 device: Montserrat has no CJK glyphs, and the
# device must never infer the reason from Chinese summary words. Keep these
# ASCII and <= 24 characters.
_DETAIL_BY_CODE = {
    ErrorCode.READY_FALSE: "MAC NOT READY",
    ErrorCode.BUSY: "MAC BUSY / RETRY",
    ErrorCode.AUTH_FAILED: "AUTH FAILED",
    ErrorCode.PAIRING_REQUIRED: "PAIR THIS MAC AGAIN",
    ErrorCode.PROTOCOL_ERROR: "PROTOCOL ERROR",
    ErrorCode.COMMAND_NOT_FOUND: "COMMAND EXPIRED",
    ErrorCode.AUDIO_INVALID: "AUDIO INVALID",
    ErrorCode.AUDIO_OVERRUN: "AUDIO TOO LONG",
    ErrorCode.HASH_MISMATCH: "AUDIO CHECK FAILED",
    ErrorCode.SHORT_PRESS: "HOLD LONGER",
    ErrorCode.USER_CANCELED: "CANCELED",
    ErrorCode.RECORDING_TIMEOUT: "RECORDING TOO LONG",
    ErrorCode.DISK_FULL: "MAC DISK FULL",
    ErrorCode.STORAGE_ERROR: "MAC STORAGE ERROR",
    ErrorCode.MODEL_UNAVAILABLE: "MAC MODEL OFFLINE",
    ErrorCode.MODEL_TIMEOUT: "MAC MODEL TIMEOUT",
    ErrorCode.OUT_OF_SCOPE: "NOT A BILIBILI COMMAND",
    ErrorCode.INVALID_PLAN: "CANNOT PLAN / RETRY",
    ErrorCode.CAPABILITY_UNAVAILABLE: "ACTION UNAVAILABLE",
    ErrorCode.TARGET_STALE: "CHECK THE APP",
    ErrorCode.PERMISSION_DENIED: "MAC PERMISSION",
    ErrorCode.RATE_LIMITED: "TOO MANY REQUESTS",
    ErrorCode.ACTION_FAILED: "ACTION FAILED",
    ErrorCode.INTERRUPTED: "INTERRUPTED / RETRY",
    ErrorCode.OUTCOME_UNKNOWN: "UNKNOWN / CHECK MAC",
}


_ACTION_DETAIL = {
    "bilibili.like": "LIKED",
    "bilibili.coin": "COIN SENT",
    "bilibili.favorite": "FAVORITED",
    "bilibili.comments": "COMMENTS",
    "bilibili.seek": "SEEKED",
    "bilibili.send_danmaku": "DANMAKU SENT",
    "bilibili.next": "NEXT EPISODE",
    "bilibili.prev": "PREV EPISODE",
    "bilibili.home": "HOME",
    "system.vol_up": "VOLUME UP",
    "system.vol_down": "VOLUME DOWN",
    "system.volume": "VOLUME SET",
}


PLAYER_RELATIVE_ACTIONS = {
    "bilibili.pause", "bilibili.resume", "bilibili.like", "bilibili.coin",
    "bilibili.favorite", "bilibili.comments", "bilibili.seek",
    "bilibili.send_danmaku", "bilibili.next", "bilibili.prev",
}


def action_detail(state: CommandState, action: str | None) -> str | None:
    """Short ASCII label for a single player-control action."""
    if state is CommandState.SUCCEEDED and action in _ACTION_DETAIL:
        return _ACTION_DETAIL[action]
    return None


def result_detail(state: CommandState, code: ErrorCode | None, summary: str) -> str:
    """Pick the short ASCII label shown on the device for a finished command."""
    if state is CommandState.SUCCEEDED:
        return summary if _is_ascii(summary) and len(summary) <= 24 else "DONE"
    if state is CommandState.PARTIAL:
        return "PARTIAL DONE"
    if state is CommandState.CANCELED:
        return "CANCELED"
    if state is CommandState.OUTCOME_UNKNOWN:
        return "UNKNOWN / CHECK MAC"
    if code is ErrorCode.NO_SPEECH:
        return "NOT CLEAR / TRY AGAIN" if "听清" in summary else "NO SPEECH / TRY AGAIN"
    if code is ErrorCode.CLARIFY_NEEDED:
        if "搜索" in summary:
            return "SEARCH FIRST"
        if "多个动作" in summary or "分开说" in summary:
            return "SAY ONE COMMAND"
        if "弹幕" in summary:
            return "OPEN THE VIDEO FIRST"
        return "NEED MORE INFO"
    if code is None:
        return "FAILED / SEE MAC"
    if code is ErrorCode.ACTION_FAILED and "首页" in summary:
        return "OPEN THE BILIBILI HOME"
    return _DETAIL_BY_CODE.get(code, "FAILED / SEE MAC")


@dataclass
class PipelineContext:
    cfg: AppConfig
    db: Database
    service: CommandService
    stt: STTAdapter
    decision: DecisionAdapter
    executor: Executor
    enabled_actions: tuple[str, ...]
    notify_error: Callable[[str], None] | None = None
    refresh_player: Callable[[], Any] | None = None
    personalizer: Any | None = None
    # Transcripts started at Seal (audio complete) so STT overlaps the device's
    # cancel window instead of starting only after Commit.
    early_stt: dict[str, Any] | None = None
    # Returns the visible page context (page type + result/episode lists) from
    # the local CDP session; None when it is not available.
    context_reader: Callable[[], Any] | None = None


class Pipeline:
    def __init__(self, ctx: PipelineContext):
        self.ctx = ctx
        self._run_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._feedback_scheduled: set[str] = set()

    # ------------------------------------------------------------------
    def _state(self, command_id: str) -> CommandState | None:
        return self.ctx.db.get_state(command_id)

    def _cancelled(self, command_id: str) -> bool:
        return self._state(command_id) is CommandState.CANCELED

    def _audio_path(self, command_id: str) -> Path:
        with self.ctx.db.transaction() as tx:
            row = tx.execute(
                "SELECT relative_path FROM audio_samples WHERE command_id=? AND deletion_state='active'",
                (command_id,),
            ).fetchone()
        if not row:
            raise STTError(ErrorCode.AUDIO_INVALID, "committed audio is missing")
        rel = row["relative_path"]
        path = (self.ctx.cfg.root / rel).resolve()
        audio_root = self.ctx.cfg.audio_dir.resolve()
        if audio_root not in path.parents or not path.is_file() or path.is_symlink():
            raise STTError(ErrorCode.STORAGE_ERROR, "audio path is not a trusted file")
        return path

    def _context(self, command_id: str) -> TrustedContext:
        now = utc_now()
        player_row = self.ctx.db.current_player(now)
        player = None
        if player_row:
            player = PlayerSnapshot(
                video_id=player_row["video_id"], generation=player_row["generation"],
                paused=player_row["paused"], position_ms=player_row["position_ms"],
                observed_at=player_row["observed_at"], expires_at=player_row["expires_at"],
                account_id=player_row["account_id"],
            )
        search_row = self.ctx.db.latest_snapshot("search", now)
        search = None
        if search_row:
            payload = json.loads(search_row["payload_json"])
            results = [
                SearchResult(
                    video_id=r["video_id"], title=r["title"], index=int(r["index"]),
                    row=int(r["row"]) if r.get("row") is not None else None,
                    column=int(r["column"]) if r.get("column") is not None else None,
                )
                for r in payload.get("results", [])[:50]
                if r.get("video_id") and r.get("title") and r.get("index")
            ]
            search = SearchSnapshot(
                search_result_id=UUID(search_row["id"]), results=results,
                observed_at=search_row["observed_at"], expires_at=search_row["expires_at"],
                generation=UUID(search_row["generation"]), query=str(payload.get("query", "")),
            )
        recent = []
        with self.ctx.db.transaction() as tx:
            rows = tx.execute(
                "SELECT id, state, summary FROM commands WHERE state IN "
                "('SUCCEEDED','PARTIAL','FAILED','OUTCOME_UNKNOWN') ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
        for row in rows:
            recent.append({"command_id": row["id"], "state": row["state"], "summary": row["summary"]})
        return TrustedContext(now=now, player_snapshot=player, search_snapshot=search, recent_commands=recent)

    def _finalize(self, command_id: str, state: CommandState, code: ErrorCode | None, summary: str) -> None:
        rows = self.ctx.db.steps(command_id)
        replayable = (
            state is CommandState.SUCCEEDED
            and len(rows) == 1
            and rows[0]["action"] in contracts.REPLAY_ACTIONS
        )
        self.ctx.db.finalize(command_id, state, code, summary, replayable)
        if state in (CommandState.FAILED, CommandState.PARTIAL, CommandState.OUTCOME_UNKNOWN):
            self.ctx.service.set_failure_retention(command_id)

    def result_message(self, command_id: str) -> Result | None:
        row = self.ctx.db.get_command(command_id)
        if not row:
            return None
        state = CommandState(row["state"])
        steps = []
        for step in row["steps"]:
            steps.append(StepResult(
                step_index=int(step["step_index"]),
                status=StepState(step["state"]),
                code=ErrorCode(step["error_code"]) if step["error_code"] else None,
            ))
        code = ErrorCode(row["error_code"]) if row["error_code"] else None
        summary = (row["summary"] or self._summary_for(state))[:128]
        detail = result_detail(state, code, summary)
        if len(row["steps"]) == 1:
            detail = action_detail(state, row["steps"][0]["action"]) or detail
        return Result(
            msg_id=0,
            command_id=UUID(command_id),
            state=state,
            code=code,
            summary=summary,
            detail=detail,
            step_results=steps,
            replayable=self._replayable(row),
        )

    @staticmethod
    def _summary_for(state: CommandState) -> str:
        return {
            CommandState.SUCCEEDED: "完成",
            CommandState.PARTIAL: "部分完成",
            CommandState.FAILED: "未完成",
            CommandState.CANCELED: "已取消",
            CommandState.OUTCOME_UNKNOWN: "执行结果未知，请检查 Mac",
        }.get(state, state.value)

    @staticmethod
    def _replayable(row: dict[str, Any]) -> bool:
        try:
            plan = json.loads(row.get("plan_json") or "{}")
            actions = plan.get("actions", [])
            return (
                row.get("state") == CommandState.SUCCEEDED.value
                and len(actions) == 1
                and actions[0].get("action") in contracts.REPLAY_ACTIONS
            )
        except Exception:
            return False

    # ------------------------------------------------------------------
    async def process(self, command_id: str, device_id: str, notify: Notify) -> None:
        async with self._run_lock:
            await self._process(command_id, device_id, notify)

    async def _process(self, command_id: str, device_id: str, notify: Notify) -> None:
        row = self.ctx.db.get_command(command_id)
        if not row:
            return
        if row["state"] != CommandState.COMMITTED.value:
            return
        deadline = float(row["committed_at"] or utc_now()) + 120.0
        if utc_now() > deadline:
            self._finalize(command_id, CommandState.FAILED, ErrorCode.ACTION_FAILED, "处理超时，未执行")
            await self._send_result(command_id, notify)
            return
        t_start = time.perf_counter()
        try:
            wav_path = self._audio_path(command_id)
            validation = await asyncio.to_thread(validate_audio, wav_path)
            t_validate = time.perf_counter()
            if not validation.is_speech_like():
                self._finalize(command_id, CommandState.FAILED, ErrorCode.NO_SPEECH, "没有检测到语音，请重新说")
                await self._send_result(command_id, notify)
                return
            if self._cancelled(command_id):
                return
            self.ctx.db.transition(command_id, CommandState.TRANSCRIBING)
            await notify(Progress(msg_id=0, command_id=UUID(command_id), stage="transcribing", step_index=None))
            transcript = None
            early = (self.ctx.early_stt or {}).pop(command_id, None)
            if early is not None:
                try:
                    transcript = await asyncio.wait_for(asyncio.shield(early), timeout=60)
                except Exception as exc:  # noqa: BLE001 - fall back to a fresh run
                    log.warning("early STT failed, retrying: %s", exc)
                    transcript = None
            if transcript is None:
                transcript = await asyncio.to_thread(self.ctx.stt.transcribe, wav_path, deadline)
            t_stt = time.perf_counter()
            if self._cancelled(command_id):
                return
            if not transcript.normalized_text:
                self._finalize(command_id, CommandState.FAILED, ErrorCode.NO_SPEECH, "没有听清，请重新说")
                await self._send_result(command_id, notify)
                return
            # Persist the transcript before any intent rejection so failures
            # are diagnosable (previously OUT_OF_SCOPE rows had raw_text=None).
            self.ctx.db.set_transcript(
                command_id, transcript.raw_model_text, transcript.normalized_text, transcript.model_digest
            )
            if not has_supported_intent(transcript.normalized_text):
                reason = unsupported_intent_reason(transcript.normalized_text)
                self._finalize(command_id, CommandState.FAILED, ErrorCode.OUT_OF_SCOPE,
                               reason or "没有识别到 B 站指令，请说打开B站、搜索、播放、暂停或继续")
                await self._send_result(command_id, notify)
                return
            self.ctx.db.transition(command_id, CommandState.PLANNING)
            await notify(Progress(msg_id=0, command_id=UUID(command_id), stage="planning", step_index=None))
            t_refresh0 = time.perf_counter()
            context = self._context(command_id)
            proposal = None
            route = "rules"
            # Context first: resolve "第 N 个 / 打开 <标题>" against the page the
            # user is actually looking at (search results, episodes, recommen-
            # dations) instead of the previous command's snapshot.
            if self.ctx.context_reader is not None:
                try:
                    page = await asyncio.to_thread(self.ctx.context_reader)
                except Exception:
                    page = None
                if page:
                    context.page_context = page
                    from .context_rules import PageContext, resolve as resolve_context

                    decision = resolve_context(transcript.normalized_text, PageContext(
                        page=str(page.get("page") or "other"),
                        search_results=list(page.get("search_results") or []),
                        episodes=list(page.get("episodes") or []),
                        recommendations=list(page.get("recommendations") or []),
                    ))
                    if decision and decision.get("action") == "bilibili.play":
                        proposal = contracts.DecisionProposal(
                            status=contracts.DecisionStatus.ok,
                            reason_code=contracts.ReasonCode.none,
                            actions=[contracts.ActionStep(id=0, action="bilibili.play",
                                                          params=dict(decision["params"]))],
                        )
                        route = "context"
            # Prefer the deterministic R1 grammar for clear commands. The LLM
            # must not add an unspoken search/play action to "打开B站".
            if proposal is None:
                proposal = rule_based_proposal(transcript.normalized_text, context)
            # Refresh the player snapshot by *action*, not by Chinese keywords:
            # "点个赞" never contains "点赞" and used to fail with TARGET_STALE.
            needs_player = proposal is None or any(
                a.action in PLAYER_RELATIVE_ACTIONS for a in proposal.actions
            )
            if self.ctx.refresh_player and needs_player:
                try:
                    await asyncio.to_thread(self.ctx.refresh_player)
                except Exception:
                    pass
                context = self._context(command_id)
            t_refresh = time.perf_counter()
            route = "rules"
            if proposal is None and self.ctx.personalizer is not None and personalization_candidate(transcript.normalized_text):
                route = "personalizer"
                proposal = self.ctx.personalizer.propose(transcript.normalized_text)
            if proposal is None:
                route = "ollama"
                proposal = await asyncio.to_thread(
                    self.ctx.decision.propose,
                    transcript.normalized_text, context, self.ctx.enabled_actions, deadline,
                )
            log.info("decision command=%s route=%s decision_ms=%d", command_id, route,
                     int((time.perf_counter() - t_refresh) * 1000))
            proposal = apply_policy_guard(transcript.normalized_text, proposal)
            if self._cancelled(command_id):
                return
            try:
                plan = build_plan(
                    proposal,
                    command_id=command_id,
                    device_id=device_id,
                    enabled_actions=self.ctx.enabled_actions,
                    context=context,
                    app_allowlist=self.ctx.cfg.app_allowlist,
                )
            except PlanRejection as exc:
                self._finalize(command_id, CommandState.FAILED, exc.code, exc.summary)
                await self._send_result(command_id, notify)
                return
            except contracts.ContractError as exc:
                self._finalize(command_id, CommandState.FAILED, ErrorCode.INVALID_PLAN, "无法形成可执行计划")
                await self._send_result(command_id, notify)
                return
            plan_json = {
                "actions": [
                    {"action": a.action, "params": a.params, "after": a.after, "binding": a.binding}
                    for a in plan.actions
                ]
            }
            t_plan = time.perf_counter()
            self.ctx.db.journal_plan(command_id, plan_json)
            if requires_confirmation(plan) and self.ctx.enabled_actions is contracts.R1_ACTIONS:
                # R1 never includes public/account actions; this is defensive.
                self._finalize(command_id, CommandState.FAILED, ErrorCode.CAPABILITY_UNAVAILABLE, "该动作需要 R2 确认支持")
                await self._send_result(command_id, notify)
                return
            if requires_confirmation(plan):
                confirmation_id = uuid4()
                plan_hash = plan_digest(plan)
                expires = min(utc_now() + 15.0, plan.expires_at)
                self.ctx.db.save_confirmation(
                    str(confirmation_id), command_id, device_id,
                    self._account_for(plan), plan_json, plan_hash, expires,
                )
                self.ctx.db.transition(command_id, CommandState.AWAITING_CONFIRMATION)
                preview = self._preview_for(plan)
                await notify(ConfirmRequired(
                    msg_id=0, command_id=UUID(command_id), confirmation_id=confirmation_id,
                    plan_hash=plan_hash, preview=preview,
                    expires_in_ms=max(1, int((expires - utc_now()) * 1000)),
                ))
                return
            if self._cancelled(command_id):
                return
            self.ctx.db.transition(command_id, CommandState.EXECUTING)
            await notify(Progress(msg_id=0, command_id=UUID(command_id), stage="executing", step_index=None))

            loop = asyncio.get_running_loop()
            def sync_progress(stage: str, step_index: int | None) -> None:
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(notify(Progress(
                        msg_id=0, command_id=UUID(command_id), stage="executing", step_index=step_index,
                    )))
                )

            outcome = await asyncio.to_thread(
                self.ctx.executor.execute, plan, command_id, context, deadline, sync_progress
            )
            t_exec = time.perf_counter()
            log.warning(
                "timing command=%s text=%r validate_ms=%d stt_ms=%d refresh_ms=%d plan_ms=%d execute_ms=%d total_ms=%d",
                command_id, transcript.normalized_text,
                int((t_validate - t_start) * 1000), int((t_stt - t_validate) * 1000),
                int((t_refresh - t_refresh0) * 1000), int((t_plan - t_refresh) * 1000),
                int((t_exec - t_plan) * 1000), int((t_exec - t_start) * 1000),
            )
            await self._send_result(command_id, notify)
        except STTError as exc:
            self._finalize(command_id, CommandState.FAILED, exc.code, exc.summary)
            await self._send_result(command_id, notify)
        except DecisionError as exc:
            self._finalize(command_id, CommandState.FAILED, exc.code, exc.summary)
            await self._send_result(command_id, notify)
        except Exception as exc:
            self._finalize(command_id, CommandState.FAILED, ErrorCode.STORAGE_ERROR, "处理失败，请重试")
            await self._send_result(command_id, notify)

    async def _send_result(self, command_id: str, notify: Notify) -> None:
        msg = self.result_message(command_id)
        if msg is not None:
            await notify(msg)
            if msg.state in (CommandState.SUCCEEDED, CommandState.PARTIAL,
                             CommandState.FAILED, CommandState.OUTCOME_UNKNOWN)                     and command_id not in self._feedback_scheduled:
                self._feedback_scheduled.add(command_id)
                self._tasks.add(asyncio.create_task(self._feedback_fallback(command_id)))

    async def _feedback_fallback(self, command_id: str) -> None:
        """Record a weak UNFEEDBACK if the device did not report one.

        Device feedback always wins in Database.record_feedback(); this row is
        only a sparse-data marker, never a positive training label.
        """
        await asyncio.sleep(8.0)
        try:
            if self.ctx.db.feedback_for_command(command_id):
                return
            row = self.ctx.db.get_command(command_id)
            if not row or row["state"] == CommandState.CANCELED.value:
                return
            self.ctx.db.record_feedback(
                command_id, str(row.get("device_id") or ""),
                rating="unfeedback", dismissed_by="server_inferred",
                touch_count=0, countdown_at_dismiss=0, source="server",
            )
        except Exception as exc:
            log.warning("feedback fallback failed command=%s error=%s", command_id, exc)
        finally:
            self._feedback_scheduled.discard(command_id)

    def _account_for(self, plan) -> str | None:
        for action in plan.actions:
            if action.binding.get("account_id"):
                return str(action.binding["account_id"])
        return None

    def _preview_for(self, plan) -> ConfirmPreview:
        action = next(a for a in plan.actions if a.action in contracts.ACCOUNT_ACTIONS)
        text = str(action.params.get("text", ""))[:30]
        video = str(action.binding.get("video_id") or "当前视频")
        account = str(action.binding.get("account_id") or "当前账号")
        return ConfirmPreview(text=text or "（非文本动作）", video_id=video, target_label=video, account_label=account)

