"""Actual adapter route and CDP effect checks, without external playback."""
from types import SimpleNamespace
import json
import time
import uuid

import pytest

from voice_assistant.adapters import cdp_player as cdp
from voice_assistant.adapters.bilibili import BilibiliAdapter
from voice_assistant.adapters.base import AdapterRegistry, ResolvedAction
from voice_assistant.config import load_config
from voice_assistant.contracts import CommandState, ErrorCode, R1_ACTIONS, StepState, TrustedContext, validate_decision_proposal
from voice_assistant.executor import Executor
from voice_assistant.planner import build_plan
from voice_assistant.storage import Database

BV = "BV1jVJk6AEmn"
HOME = "https://bilipc.bilibili.com/index.html#/page/home/recommends"


def adapter_without_ax(monkeypatch):
    adapter = BilibiliAdapter.__new__(BilibiliAdapter)
    adapter.app_installed = lambda: True
    adapter.app_running = lambda: True
    adapter.accessibility_trusted = lambda: False
    adapter._helper = lambda *args, **kw: pytest.fail("AX must not run on the CDP route")
    adapter._search_ui = lambda *args: pytest.fail("Visible play must never search first")
    def save(bv, paused):
        adapter._last_player = {"generation": "verified-generation", "video_id": bv, "paused": paused}
        return adapter._last_player
    adapter._save_player = save
    monkeypatch.setattr(cdp, "control_available", lambda: True)
    return adapter


def test_main_page_cdp_exposes_play_without_ax_or_player(monkeypatch):
    adapter = adapter_without_ax(monkeypatch)
    caps = adapter.probe()
    assert "bilibili.play" in caps.available
    assert "bilibili.play" not in caps.reasons
    assert "bilibili.search" not in caps.available  # It still needs its own working implementation.


def test_visible_home_card_plays_through_the_real_adapter_without_search(monkeypatch):
    adapter = adapter_without_ax(monkeypatch)
    calls = []
    def play(bvid, **kwargs):
        calls.append((bvid, kwargs))
        return {"ok": True, "clicked": True, "bvid": bvid, "playing_verified": True,
                "time_before": 0.1, "time_after": 0.8, "via": "cdp-visible-card"}
    monkeypatch.setattr(cdp, "play_visible_video", play)
    context = SimpleNamespace(page_context={"url": HOME, "home_results": [{"bvid": BV, "title": "第一张卡片"}]})
    prepared = adapter.prepare(ResolvedAction(0, "bilibili.play", {"bv": BV}, {"kind": "bv", "video_id": BV}), context)
    receipt = adapter.invoke(prepared, time.time() + 10)
    result = adapter.verify(receipt, time.time() + 10)
    assert result.status is StepState.SUCCEEDED
    assert result.output["video_id"] == BV
    assert calls[0][1]["source_url"] == HOME


@pytest.mark.parametrize("raw,expected_status,expected_code", [
    ({"ok": False, "clicked": False, "error": "visible_target_missing", "via": "cdp-visible-card"}, StepState.FAILED, ErrorCode.TARGET_STALE),
    ({"ok": False, "clicked": True, "bvid": BV, "error": "playback_not_verified", "via": "cdp-visible-card"}, StepState.OUTCOME_UNKNOWN, ErrorCode.OUTCOME_UNKNOWN),
    ({"ok": True, "clicked": True, "bvid": "BV1xx411c7mD", "playing_verified": True,
      "time_before": 0, "time_after": 1, "via": "cdp-visible-card"}, StepState.OUTCOME_UNKNOWN, ErrorCode.OUTCOME_UNKNOWN),
])
def test_click_or_wrong_video_is_not_playback_success(monkeypatch, raw, expected_status, expected_code):
    adapter = adapter_without_ax(monkeypatch)
    monkeypatch.setattr(cdp, "play_visible_video", lambda *args, **kw: raw)
    prepared = adapter.prepare(ResolvedAction(0, "bilibili.play", {"bv": BV}, {}), None)
    result = adapter.verify(adapter.invoke(prepared, time.time() + 10), time.time() + 10)
    assert result.status is expected_status
    assert result.code is expected_code


def test_cdp_exact_visible_target_dispatches_mouse_and_requires_time_advancement(monkeypatch):
    calls = []
    states = iter([
        {"video": False},
        {"video": True, "bvid": BV, "paused": False, "time": 0.0},
        {"video": True, "bvid": BV, "paused": False, "time": 0.3},
    ])
    class Session:
        def __init__(self, ws, **kwargs): self.ws = ws
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def js(self, expression, **kwargs):
            assert BV in expression
            return {"x": 210, "y": 170, "title": "visible card"}
        def call(self, method, params): calls.append((method, params))
        def state(self): return next(states)
    monkeypatch.setattr(cdp, "_targets", lambda: [
        {"url": HOME, "webSocketDebuggerUrl": "home-ws"},
        {"url": "https://bilipc.bilibili.com/player.html", "webSocketDebuggerUrl": "player-ws"},
    ])
    monkeypatch.setattr(cdp, "CdpPlayer", Session)
    monkeypatch.setattr(cdp.time, "sleep", lambda _: None)
    result = cdp.play_visible_video(BV, source_url=HOME)
    assert result["playing_verified"]
    assert [x[1]["type"] for x in calls] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert result["time_after"] > result["time_before"]


def test_control_available_includes_home_without_player(monkeypatch):
    monkeypatch.setattr(cdp, "_targets", lambda: [{"url": HOME}])
    assert cdp.control_available()
    assert not cdp.available()


def test_cold_video_load_and_stale_player_shell_do_not_end_verification_early(monkeypatch):
    clock = [0.0]
    reads = []
    class Session:
        def __init__(self, ws, **kwargs): self.ws = ws
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def js(self, expression, **kwargs):
            return {"x": 210, "y": 170, "title": "visible card"}
        def call(self, *args): pass
        def state(self):
            reads.append(self.ws)
            if self.ws == "empty-shell":
                return {"video": False}
            return {"video": clock[0] >= 12, "bvid": BV, "paused": False,
                    "time": max(0, clock[0] - 12)}
    monkeypatch.setattr(cdp, "_targets", lambda: [
        {"url": HOME, "webSocketDebuggerUrl": "home-ws"},
        {"url": "https://bilipc.bilibili.com/player.html", "webSocketDebuggerUrl": "empty-shell"},
        {"url": "https://bilipc.bilibili.com/player.html?bvid=" + BV, "webSocketDebuggerUrl": "new-player"},
    ])
    monkeypatch.setattr(cdp, "CdpPlayer", Session)
    monkeypatch.setattr(cdp.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cdp.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = cdp.play_visible_video(BV, source_url=HOME)
    assert result["playing_verified"]
    assert 12 < clock[0] < 25
    assert "empty-shell" in reads and "new-player" in reads


def test_bound_page_disappearing_does_not_click_another_window(monkeypatch):
    monkeypatch.setattr(cdp, "_targets", lambda: [
        {"url": "https://bilipc.bilibili.com/player.html", "webSocketDebuggerUrl": "wrong-page"},
    ])
    monkeypatch.setattr(cdp, "CdpPlayer", lambda *_: pytest.fail("must not substitute another page"))
    result = cdp.play_visible_video(BV, source_url=HOME)
    assert not result["clicked"]
    assert result["error"] == "visible_target_missing"


def test_missing_bound_card_does_not_fall_back_to_ax_even_with_permission(monkeypatch):
    adapter = adapter_without_ax(monkeypatch)
    adapter.accessibility_trusted = lambda: True
    monkeypatch.setattr(cdp, "play_visible_video", lambda *args, **kwargs: {
        "ok": False, "clicked": False, "error": "visible_target_missing", "via": "cdp-visible-card",
    })
    context = SimpleNamespace(page_context={"url": HOME, "home_results": [{"bvid": BV, "title": "第一张卡片"}]})
    prepared = adapter.prepare(ResolvedAction(0, "bilibili.play", {"bv": BV}, {}), context)
    receipt = adapter.invoke(prepared, time.time() + 25)
    result = adapter.verify(receipt, time.time() + 25)
    assert not receipt.effect_possible
    assert result.status is StepState.FAILED
    assert result.code is ErrorCode.TARGET_STALE


def test_cdp_success_persists_player_evidence_and_terminal_result(tmp_path, monkeypatch):
    """Exercise the storage boundary: canonical evidence disallows float seconds."""
    cfg = load_config(tmp_path)
    db = Database(cfg.db_path)
    try:
        device, _ = db.register_device("CDP storage regression")
        cid = str(uuid.uuid4())
        db.create_audio_command(cid, device, "boot", "fp-" + cid, time.time() + 100)
        db.seal(cid, 1, 640, "0" * 64)
        db.commit(cid, "0" * 64, "audio/x.wav", {"size_bytes": 640, "duration_ms": 20, "quality_label": "good"})
        db.transition(cid, CommandState.PLANNING)
        action = {"id": 0, "action": "bilibili.play", "params": {"bv": BV}, "after": []}
        proposal = validate_decision_proposal({
            "schema_version": 1, "status": "ok", "reason_code": "none",
            "clarification": None, "model_confidence": None, "actions": [action],
        })
        context = TrustedContext(now=time.time(), page_context={
            "url": HOME, "home_results": [{"bvid": BV, "title": "第一张卡片"}],
        })
        plan = build_plan(proposal, command_id=cid, device_id=device, enabled_actions=R1_ACTIONS, context=context)
        db.journal_plan(cid, {"actions": [action]}, "storage-regression")
        db.transition(cid, CommandState.EXECUTING)
        adapter = BilibiliAdapter(cfg, db)
        monkeypatch.setattr(adapter, "app_installed", lambda: True)
        monkeypatch.setattr(adapter, "app_running", lambda: True)
        monkeypatch.setattr(adapter, "accessibility_trusted", lambda: False)
        monkeypatch.setattr(adapter, "_helper", lambda *args, **kw: pytest.fail("must use CDP"))
        monkeypatch.setattr(cdp, "control_available", lambda: True)
        monkeypatch.setattr(cdp, "play_visible_video", lambda *args, **kw: {
            "ok": True, "clicked": True, "bvid": BV, "playing_verified": True,
            "time_before": 0.113, "time_after": 0.874, "via": "cdp-visible-card",
        })
        registry = AdapterRegistry()
        registry.register(adapter, ("bilibili.play",))
        result = Executor(db, registry).execute(plan, cid, context, time.time() + 30)
        assert result.state is CommandState.SUCCEEDED
        assert db.get_state(cid) is CommandState.SUCCEEDED
        step = db.steps(cid)[0]
        assert step["state"] == "SUCCEEDED"
        evidence = json.loads(step["evidence_json"])
        assert evidence["position_before_ms"] == 113
        assert evidence["position_after_ms"] == 874
        assert db.current_player()["video_id"] == BV
    finally:
        db.close()
