"""Contract + planner coverage for the R2 player controls.

Execution itself is verified against the live app (``player_ui``); these tests
pin the wire contract: every spoken command turns into exactly one validated
action and the planner accepts or rejects it with the right error.
"""
from __future__ import annotations

import time
import uuid

import pytest

from voice_assistant import contracts
from voice_assistant.contracts import (
    ActionStep, DecisionProposal, DecisionStatus, ErrorCode, PlayerSnapshot,
    ReasonCode, TrustedContext,
)
from voice_assistant.intent import rule_based_proposal
from voice_assistant.planner import PlanRejection, build_plan


def _context() -> TrustedContext:
    now = time.time()
    player = PlayerSnapshot(
        video_id="BV1JKDbBzETH", generation=uuid.uuid4(), paused=False,
        position_ms=45_000, observed_at=now, expires_at=now + 120,
    )
    return TrustedContext(now=now, player_snapshot=player)


def _plan(text: str):
    proposal = rule_based_proposal(text, _context())
    assert proposal is not None, text
    return build_plan(
        proposal, command_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
        enabled_actions=contracts.ENABLED_ACTIONS, context=_context(),
    )


@pytest.mark.parametrize("text,action", [
    ("点赞", "bilibili.like"),
    ("投币", "bilibili.coin"),
    ("投两个币", "bilibili.coin"),
    ("收藏这个视频", "bilibili.favorite"),
    ("收藏到房车", "bilibili.favorite"),
    ("打开评论区", "bilibili.comments"),
    ("快进30秒", "bilibili.seek"),
    ("快进到第2分10秒", "bilibili.seek"),
    ("下一集", "bilibili.next"),
    ("上一集", "bilibili.prev"),
    ("音量调到百分之六十", "system.volume"),
    ("发送弹幕 好可爱", "bilibili.send_danmaku"),
])
def test_plan_accepts_player_controls(text: str, action: str):
    plan = _plan(text)
    assert [a.action for a in plan.actions] == [action]


def test_plan_rejects_double_seek_target():
    with pytest.raises(Exception):
        ActionStep(id=0, action="bilibili.seek", params={"delta_s": 10, "target_s": 30})


def test_coin_count_is_bounded():
    with pytest.raises(Exception):
        ActionStep(id=0, action="bilibili.coin", params={"count": 3})


def test_volume_target_is_bounded():
    with pytest.raises(Exception):
        ActionStep(id=0, action="system.volume", params={"target": 101})


def test_folder_name_is_bounded():
    with pytest.raises(Exception):
        ActionStep(id=0, action="bilibili.favorite", params={"folder": "x" * 33})


def test_player_control_requires_a_current_video():
    proposal = rule_based_proposal("点赞", None)
    assert proposal is not None
    with pytest.raises(PlanRejection) as exc:
        build_plan(proposal, command_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                   enabled_actions=contracts.ENABLED_ACTIONS, context=TrustedContext(now=time.time()))
    assert exc.value.code == ErrorCode.TARGET_STALE


def test_disabled_action_is_rejected():
    proposal = DecisionProposal(
        status=DecisionStatus.ok, reason_code=ReasonCode.none,
        actions=[ActionStep(id=0, action="bilibili.like", params={})],
    )
    with pytest.raises(contracts.ContractError) as exc:
        build_plan(proposal, command_id=str(uuid.uuid4()), device_id=str(uuid.uuid4()),
                   enabled_actions=contracts.R1_ACTIONS, context=_context())
    assert "not enabled" in str(exc.value)
