from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from voice_assistant.adapters.base import AdapterRegistry  # noqa: E402
from voice_assistant.adapters.fake import FakeBilibiliAdapter, FakeBilibiliState  # noqa: E402
from voice_assistant.config import load_config  # noqa: E402
from voice_assistant.contracts import (  # noqa: E402
    CommandState,
    DecisionStatus,
    ErrorCode,
    PlayerSnapshot,
    SearchResult,
    SearchSnapshot,
    StepState,
    TrustedContext,
    validate_decision_proposal,
)
from voice_assistant.executor import Executor  # noqa: E402
from voice_assistant.planner import PlanRejection, build_plan  # noqa: E402
from voice_assistant.storage import Database  # noqa: E402


def prop(actions, status="ok", reason="none"):
    return validate_decision_proposal({
        "schema_version": 1,
        "status": status,
        "reason_code": reason,
        "clarification": None,
        "model_confidence": None,
        "actions": actions,
    })


def st(i, action, params=None, after=None):
    return {"id": i, "action": action, "params": params or {}, "after": after or []}


def context(player=True, search=False, now=None):
    now = time.time() if now is None else now
    ps = None
    ss = None
    if player:
        ps = PlayerSnapshot(
            video_id="BV1LqJC6iEZV", generation=str(uuid.uuid4()), paused=False,
            position_ms=30000, observed_at=now - 1, expires_at=now + 60, account_id="acct",
        )
    if search:
        ss = SearchSnapshot(
            search_result_id=uuid.uuid4(),
            results=[SearchResult(video_id="BV1LqJC6iEZV", title="猫", index=1)],
            observed_at=now - 1, expires_at=now + 100, generation=uuid.uuid4(), query="猫",
        )
    return TrustedContext(now=now, search_snapshot=ss, player_snapshot=ps)


@pytest.fixture()
def env(tmp_path):
    cfg = load_config(tmp_path)
    db = Database(cfg.db_path)
    device_id, _ = db.register_device("test")
    fake = FakeBilibiliAdapter()
    registry = AdapterRegistry()
    registry.register(fake, (
        "bilibili.open", "bilibili.search", "bilibili.play", "bilibili.pause", "bilibili.resume",
        "bilibili.next", "bilibili.prev", "bilibili.favorite", "bilibili.like",
        "bilibili.danmaku_on", "bilibili.danmaku_off", "bilibili.send_danmaku",
    ))
    executor = Executor(db, registry)
    return cfg, db, device_id, fake, executor


def seed_player(db, ctx):
    p = ctx.player_snapshot
    db.save_snapshot(
        "player", "com.bilibili.bilibiliPC", p.generation,
        {"paused": p.paused, "position_ms": p.position_ms, "account_id": p.account_id},
        p.observed_at, p.expires_at, video_id=p.video_id,
    )


def seed_command(db, device_id, plan_json):
    cid = str(uuid.uuid4())
    db.create_audio_command(cid, device_id, "boot", "fp-" + cid, time.time() + 100)
    db.seal(cid, 1, 640, "0" * 64)
    db.commit(cid, "0" * 64, "audio/x.wav", {"size_bytes": 640, "duration_ms": 20, "quality_label": "good"})
    db.transition(cid, CommandState.PLANNING)
    db.journal_plan(cid, plan_json, "test")
    db.transition(cid, CommandState.EXECUTING)
    return cid


def test_build_plan_r1_actions(env):
    cfg, db, device_id, fake, executor = env
    ctx = context()
    for name, params in [
        ("bilibili.open", {}),
        ("bilibili.search", {"query": "猫"}),
        ("bilibili.play", {"bv": "BV1LqJC6iEZV"}),
        ("bilibili.pause", {}),
        ("bilibili.resume", {}),
    ]:
        plan = build_plan(prop([st(0, name, params)]), command_id=str(uuid.uuid4()),
                          device_id=device_id, enabled_actions=__import__(
                              "voice_assistant.contracts", fromlist=["R1_ACTIONS"]).R1_ACTIONS,
                          context=ctx)
        assert plan.actions[0].action == name


def test_build_plan_rejects_r2(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    with pytest.raises(Exception) as exc:
        build_plan(prop([st(0, "system.mute")]), command_id=str(uuid.uuid4()),
                   device_id=device_id, enabled_actions=R1_ACTIONS, context=context())
    # validate_plan_structure raises ContractError; either is a rejection with 0 effects.
    assert str(exc.value)


def test_build_plan_needs_player_for_pause(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    with pytest.raises(PlanRejection) as exc:
        build_plan(prop([st(0, "bilibili.pause")]), command_id=str(uuid.uuid4()),
                   device_id=device_id, enabled_actions=R1_ACTIONS, context=context(player=False))
    assert exc.value.code is ErrorCode.TARGET_STALE


def test_build_plan_search_play_reference(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    proposal = prop([
        st(0, "bilibili.search", {"query": "猫"}),
        st(1, "bilibili.play", {"index": 1, "search_result_id": "$step_0.output.search_result_id"}, [0]),
    ])
    plan = build_plan(proposal, command_id=str(uuid.uuid4()), device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=context(search=True))
    assert [a.action for a in plan.actions] == ["bilibili.search", "bilibili.play"]
    assert plan.actions[1].binding["kind"] == "search_step"


def test_play_query_requires_search(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    with pytest.raises(PlanRejection) as exc:
        build_plan(prop([st(0, "bilibili.play", {"query": "猫"})]), command_id=str(uuid.uuid4()),
                   device_id=device_id, enabled_actions=R1_ACTIONS, context=context())
    assert exc.value.code is ErrorCode.CLARIFY_NEEDED


def test_executor_search_then_play(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    fake.state.search_snapshot_id = str(uuid.uuid4())
    proposal = prop([
        st(0, "bilibili.search", {"query": "猫"}),
        st(1, "bilibili.play", {"index": 2, "search_result_id": "$step_0.output.search_result_id"}, [0]),
    ])
    ctx = context()
    plan = build_plan(proposal, command_id=str(uuid.uuid4()), device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=ctx)
    cid = seed_command(db, device_id, {"actions": [
        {"action": a.action, "params": a.params, "after": a.after} for a in plan.actions
    ]})
    # Planner's command id differs from the seeded one; rebuild against it.
    plan = build_plan(proposal, command_id=cid, device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=ctx)
    outcome = executor.execute(plan, cid, ctx, deadline=time.time() + 30)
    assert outcome.state is CommandState.SUCCEEDED, outcome
    assert fake.state.current_video == "BV1xx411c7mD"
    assert [a.step_index for a in outcome.results] == [0, 1]
    assert all(a.status is StepState.SUCCEEDED for a in outcome.results)
    assert db.get_state(cid) is CommandState.SUCCEEDED


def test_executor_pause_does_not_blind_toggle(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    ctx = context()
    seed_player(db, ctx)
    fake.state.player_generation = str(ctx.player_snapshot.generation)
    plan = build_plan(prop([st(0, "bilibili.pause")]), command_id=str(uuid.uuid4()),
                      device_id=device_id, enabled_actions=R1_ACTIONS, context=ctx)
    cid = seed_command(db, device_id, {"actions": [{"action": "bilibili.pause", "params": {}, "after": []}]})
    plan = build_plan(prop([st(0, "bilibili.pause")]), command_id=cid, device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=ctx)
    assert executor.execute(plan, cid, ctx, deadline=time.time() + 30).state is CommandState.SUCCEEDED
    assert fake.state.paused
    # Already paused: resume must set-state, not toggle back to paused.
    fake.state.paused = True
    cid2 = seed_command(db, device_id, {"actions": [{"action": "bilibili.resume", "params": {}, "after": []}]})
    ctx2 = context()
    seed_player(db, ctx2)
    fake.state.player_generation = str(ctx2.player_snapshot.generation)
    plan2 = build_plan(prop([st(0, "bilibili.resume")]), command_id=cid2, device_id=device_id,
                       enabled_actions=R1_ACTIONS, context=ctx2)
    assert executor.execute(plan2, cid2, ctx2, deadline=time.time() + 30).state is CommandState.SUCCEEDED
    assert not fake.state.paused


def test_executor_target_change_is_stale_and_no_effect(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    ctx = context()
    seed_player(db, ctx)
    fake.state.player_generation = str(ctx.player_snapshot.generation)
    plan = build_plan(prop([st(0, "bilibili.pause")]), command_id=str(uuid.uuid4()),
                      device_id=device_id, enabled_actions=R1_ACTIONS, context=ctx)
    cid = seed_command(db, device_id, {"actions": [{"action": "bilibili.pause", "params": {}, "after": []}]})
    plan = build_plan(prop([st(0, "bilibili.pause")]), command_id=cid, device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=ctx)
    fake.state.current_video = "BV1xx411c7mD"
    outcome = executor.execute(plan, cid, ctx, deadline=time.time() + 30)
    assert outcome.state is CommandState.FAILED
    assert outcome.code is ErrorCode.TARGET_STALE
    assert fake.state.invocations == []


def test_executor_unknown_stops_later_steps(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS, R2_ACTIONS
    fake.state.verify_failure_actions.add("bilibili.search")
    ctx = context()
    proposal = prop([
        st(0, "bilibili.search", {"query": "猫"}),
        st(1, "bilibili.pause"),
    ])
    # R1 only: pause is allowed; search verify failure causes unknown.
    plan = build_plan(proposal, command_id=str(uuid.uuid4()), device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=ctx)
    cid = seed_command(db, device_id, {"actions": [{"action": "bilibili.search", "params": {"query": "猫"}, "after": []},
                                                   {"action": "bilibili.pause", "params": {}, "after": []}]})
    plan = build_plan(proposal, command_id=cid, device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=ctx)
    outcome = executor.execute(plan, cid, ctx, deadline=time.time() + 30)
    assert outcome.state is CommandState.OUTCOME_UNKNOWN
    assert outcome.results[1].status is StepState.SKIPPED


def test_plan_rejects_forward_reference(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    with pytest.raises(Exception):
        build_plan(prop([st(0, "bilibili.pause", after=[1]), st(1, "bilibili.resume")]),
                   command_id=str(uuid.uuid4()), device_id=device_id,
                   enabled_actions=R1_ACTIONS, context=context())


def test_executor_journals_started_before_effect(env):
    cfg, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    ctx = context()
    seed_player(db, ctx)
    fake.state.player_generation = str(ctx.player_snapshot.generation)
    plan = build_plan(prop([st(0, "bilibili.pause")]), command_id=str(uuid.uuid4()),
                      device_id=device_id, enabled_actions=R1_ACTIONS, context=ctx)
    cid = seed_command(db, device_id, {"actions": [{"action": "bilibili.pause", "params": {}, "after": []}]})
    plan = build_plan(prop([st(0, "bilibili.pause")]), command_id=cid, device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=ctx)
    executor.execute(plan, cid, ctx, deadline=time.time() + 30)
    steps = db.steps(cid)
    assert steps[0]["started_at"] is not None
    assert steps[0]["completed_at"] is not None


@pytest.mark.parametrize("remaining,expected", [(90.0, 25.0), (7.0, 7.0)])
def test_video_load_budget_is_longer_but_respects_command_deadline(env, remaining, expected):
    _, db, device_id, fake, executor = env
    from voice_assistant.contracts import R1_ACTIONS
    now = time.time()
    executor.clock = lambda: now
    ctx = context(now=now)
    proposal = prop([st(0, "bilibili.play", {"bv": "BV1LqJC6iEZV"})])
    cid = seed_command(db, device_id, {"actions": [{"action": "bilibili.play", "params": {"bv": "BV1LqJC6iEZV"}, "after": []}]})
    plan = build_plan(proposal, command_id=cid, device_id=device_id,
                      enabled_actions=R1_ACTIONS, context=ctx)
    deadlines = []
    original_invoke = fake.invoke
    def invoke(prepared, deadline):
        deadlines.append(deadline)
        # The externally committed action still has the existing too_late
        # cancellation contract during media loading, without blocking its ACK.
        assert db.cancel(cid, "user")[0] == "too_late"
        return original_invoke(prepared, deadline)
    fake.invoke = invoke
    outcome = executor.execute(plan, cid, ctx, deadline=now + remaining)
    assert deadlines == [now + expected]
    assert outcome.state is CommandState.SUCCEEDED
