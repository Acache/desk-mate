"""Deterministic plan validation and target binding.

The LLM proposal is untrusted data. This module is the only place that turns
it into a ValidatedPlan; prepared actions are created later by the executor
immediately before invocation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from . import contracts
from .contracts import (
    DecisionProposal,
    DecisionStatus,
    ErrorCode,
    PlayerSnapshot,
    ReasonCode,
    SearchSnapshot,
    TrustedContext,
    ValidatedAction,
    ValidatedPlan,
    parse_reference,
)
from .storage import ConflictError


@dataclass
class PlanRejection(Exception):
    code: ErrorCode
    summary: str
    clarification: str | None = None


OUTPUT_FIELD_SOURCE = {
    "search_result_id": {"bilibili.search"},
    "video_id": {"bilibili.play"},
}


def _fresh_player(context: TrustedContext) -> PlayerSnapshot | None:
    player = context.player_snapshot
    if not player:
        return None
    if player.expires_at < context.now:
        return None
    return player


def _fresh_search(context: TrustedContext) -> SearchSnapshot | None:
    snap = context.search_snapshot
    if not snap:
        return None
    if snap.expires_at < context.now:
        return None
    return snap


def _resolve_reference(
    step_index: int, field: str, value: str, proposal: DecisionProposal
) -> int:
    ref = parse_reference(value)
    if ref is None:
        raise PlanRejection(ErrorCode.INVALID_PLAN, f"invalid reference in {field}")
    source, ref_field = ref
    if source == -1:
        source = step_index - 1
    if source < 0 or source >= step_index:
        raise PlanRejection(ErrorCode.INVALID_PLAN, "reference must point to an earlier step")
    if ref_field != field:
        raise PlanRejection(ErrorCode.INVALID_PLAN, "reference field does not match parameter")
    source_action = proposal.actions[source].action
    if source_action not in OUTPUT_FIELD_SOURCE[field]:
        raise PlanRejection(ErrorCode.INVALID_PLAN, f"{source_action} does not produce {field}")
    return source


def _step_binding(
    step_index: int,
    step: contracts.ActionStep,
    proposal: DecisionProposal,
    player: PlayerSnapshot | None,
    search: SearchSnapshot | None,
) -> dict[str, Any]:
    action = step.action
    params = step.params
    if action in {"bilibili.pause", "bilibili.resume"}:
        # A pause/resume after a play step binds to that step's verified output;
        # otherwise it binds the plan-created current player snapshot.
        prior_play = [s for s in proposal.actions[:step_index] if s.action == "bilibili.play"]
        if step.after and prior_play:
            src = step.after[0]
            if proposal.actions[src].action == "bilibili.play":
                return {"kind": "play_output", "source_step": src}
        if not player:
            raise PlanRejection(ErrorCode.TARGET_STALE, "没有可用的播放器状态，请先播放视频")
        return {
            "kind": "player_snapshot",
            "video_id": str(player.video_id) if player.video_id else None,
            "generation": str(player.generation),
            "account_id": str(player.account_id) if player.account_id else None,
            "expires_at": int(player.expires_at),
        }
    if action == "bilibili.play":
        if params.get("bv"):
            return {"kind": "bv", "video_id": params["bv"]}
        if params.get("index") is not None:
            sid = params.get("search_result_id")
            if isinstance(sid, str) and sid.startswith("$"):
                src = _resolve_reference(step_index, "search_result_id", sid, proposal)
                return {"kind": "search_step", "source_step": src}
            if not search:
                raise PlanRejection(ErrorCode.TARGET_STALE, "搜索结果已过期，请重新搜索")
            if sid and str(search.search_result_id) != str(sid):
                raise PlanRejection(ErrorCode.TARGET_STALE, "搜索结果已变化，请重新搜索")
            return {
                "kind": "search_snapshot",
                "search_result_id": str(search.search_result_id),
                "generation": str(search.generation),
                "expires_at": int(search.expires_at),
            }
        if params.get("query"):
            raise PlanRejection(ErrorCode.CLARIFY_NEEDED, "当前无法确认播放目标，请补充可见标题或序号")
    if action in {"bilibili.danmaku_on", "bilibili.danmaku_off"}:
        if not player:
            raise PlanRejection(ErrorCode.TARGET_STALE, "没有可用的播放器")
        return {"kind": "player_snapshot", "video_id": str(player.video_id) if player.video_id else None,
                "generation": str(player.generation), "account_id": str(player.account_id) if player.account_id else None,
                "expires_at": int(player.expires_at)}
    if action in {"bilibili.favorite", "bilibili.like"}:
        vid = params.get("video_id")
        if isinstance(vid, str) and vid.startswith("$"):
            src = _resolve_reference(step_index, "video_id", vid, proposal)
            return {"kind": "play_output", "source_step": src}
        if vid:
            return {"kind": "literal", "video_id": vid}
        if not player:
            raise PlanRejection(ErrorCode.TARGET_STALE, "没有可用的当前视频")
        return {"kind": "player_snapshot", "video_id": str(player.video_id) if player.video_id else None,
                "generation": str(player.generation), "account_id": str(player.account_id) if player.account_id else None,
                "expires_at": int(player.expires_at)}
    if action == "bilibili.send_danmaku":
        if not player:
            raise PlanRejection(ErrorCode.TARGET_STALE, "没有可用的当前视频")
        vid = params.get("video_id")
        if isinstance(vid, str) and vid.startswith("$"):
            src = _resolve_reference(step_index, "video_id", vid, proposal)
            return {"kind": "play_output", "source_step": src, "text": params["text"],
                    "account_id": player.account_id}
        if vid and player.video_id and vid != player.video_id:
            raise PlanRejection(ErrorCode.TARGET_STALE, "弹幕目标与当前视频不一致")
        return {"kind": "player_snapshot", "video_id": str(player.video_id) if player.video_id else None,
                "generation": str(player.generation), "account_id": str(player.account_id) if player.account_id else None,
                "expires_at": int(player.expires_at), "text": params["text"]}
    if action in {"bilibili.next", "bilibili.prev"}:
        if not player:
            raise PlanRejection(ErrorCode.TARGET_STALE, "没有已知播放列表上下文")
        return {"kind": "player_snapshot", "video_id": str(player.video_id) if player.video_id else None,
                "generation": str(player.generation), "account_id": str(player.account_id) if player.account_id else None,
                "expires_at": int(player.expires_at)}
    return {}


def build_plan(
    proposal: DecisionProposal,
    *,
    command_id: str,
    device_id: str,
    enabled_actions: tuple[str, ...],
    context: TrustedContext,
    app_allowlist: dict[str, str] | None = None,
    now: float | None = None,
) -> ValidatedPlan:
    now = context.now if now is None else now
    if proposal.status is not DecisionStatus.ok:
        code = {
            ReasonCode.ambiguous: ErrorCode.CLARIFY_NEEDED,
            ReasonCode.missing_target: ErrorCode.CLARIFY_NEEDED,
            ReasonCode.unsupported: ErrorCode.OUT_OF_SCOPE,
            ReasonCode.too_many_steps: ErrorCode.CLARIFY_NEEDED,
            ReasonCode.policy_denied: ErrorCode.OUT_OF_SCOPE,
        }.get(proposal.reason_code, ErrorCode.INVALID_PLAN)
        summary = proposal.clarification or {
            ErrorCode.CLARIFY_NEEDED: "需要补充目标",
            ErrorCode.OUT_OF_SCOPE: "这个请求不在 B 站控制范围内",
        }.get(code, "计划被拒绝")
        raise PlanRejection(code, summary, proposal.clarification)
    contracts.validate_plan_structure(proposal, enabled_actions)
    if len(proposal.actions) > contracts.MAX_STEPS:
        raise PlanRejection(ErrorCode.CLARIFY_NEEDED, "too_many_steps")

    player = _fresh_player(context)
    search = _fresh_search(context)
    actions: list[ValidatedAction] = []
    plan_expiry = now + 120.0
    for index, step in enumerate(proposal.actions):
        # Validate references and infer dependencies before binding.
        deps = set(step.after)
        for key, value in step.params.items():
            if isinstance(value, str) and value.startswith("$") and (step.action, key) in contracts.REFERENCE_FIELDS:
                src = _resolve_reference(index, key, value, proposal)
                deps.add(src)
        if len(deps) > contracts.MAX_STEPS - 1:
            raise PlanRejection(ErrorCode.INVALID_PLAN, "too many dependencies")
        for dep in deps:
            if dep < 0 or dep >= index:
                raise PlanRejection(ErrorCode.INVALID_PLAN, "dependency must be an earlier step")
        if step.action == "system.open_app":
            alias = step.params.get("app_id")
            if not app_allowlist or alias not in app_allowlist:
                raise PlanRejection(ErrorCode.CAPABILITY_UNAVAILABLE, "应用不在允许列表中")
        binding = _step_binding(index, step, proposal, player, search)
        if binding.get("expires_at"):
            plan_expiry = min(plan_expiry, float(binding["expires_at"]))
        # Navigation after a pending public message is refused (R2 policy).
        actions.append(
            ValidatedAction(
                action=step.action,
                params=step.params,
                after=sorted(deps),
                binding=binding,
            )
        )
    if any(a.action == "bilibili.send_danmaku" for a in actions):
        if any(a.action in {
            "bilibili.search", "bilibili.play", "bilibili.next", "bilibili.prev", "bilibili.history"
        } for a in actions):
            raise PlanRejection(ErrorCode.CLARIFY_NEEDED, "请先导航到目标视频，再单独发送弹幕")
        if sum(1 for a in actions if a.action == "bilibili.send_danmaku") > 1:
            raise PlanRejection(ErrorCode.INVALID_PLAN, "每个计划最多一条弹幕")
    return ValidatedPlan(
        command_id=UUID(command_id),
        device_id=UUID(device_id),
        capability_version=contracts.CAPABILITY_VERSION,
        actions=actions,
        expires_at=plan_expiry,
    )


def requires_confirmation(plan: ValidatedPlan) -> bool:
    return any(a.action in contracts.PUBLIC_ACTIONS or a.action in contracts.ACCOUNT_ACTIONS for a in plan.actions)
