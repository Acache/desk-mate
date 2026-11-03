import json
import time
from uuid import uuid4

import httpx
import pytest

from voice_assistant.contracts import ENABLED_ACTIONS, PlayerSnapshot, TrustedContext
from voice_assistant.decision import OllamaDecision, _context_payload


def test_fresh_context_and_bounded_recent():
    now = time.time()
    p = PlayerSnapshot(video_id="BV1xx411c7mD", generation=uuid4(), paused=True,
                       position_ms=0, observed_at=now-60, expires_at=now-1)
    ctx = TrustedContext(now=now, player_snapshot=p,
                         recent_commands=[{"state":"SUCCEEDED", "summary":"x"*1000, "secret":"omit"}]*10)
    data = _context_payload(ctx)
    assert "player" not in data and "now" not in data
    assert len(data["recent"]) == 3
    assert len(data["recent"][0]["summary"]) == 160
    assert "secret" not in data["recent"][0]


def test_prewarm_and_proposal_use_residency(monkeypatch):
    requests = []
    proposal = {"status":"ok", "reason_code":"none", "actions":[
        {"id":0,"action":"system.vol_down","params":{"delta":5},"after":[]}]}
    def respond(request):
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200,json={"models":[{"name":"qwen2.5:1.5b","digest":"test"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200,json={"models":[{"name":"qwen2.5:1.5b"}]})
        return httpx.Response(200,json={"message":{"content":json.dumps(proposal)}})
    client = httpx.Client
    monkeypatch.setattr(httpx,"Client",lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    model = OllamaDecision()
    assert model.prewarm()["status"] == "ready"
    result = model.propose("小声一点",TrustedContext(now=time.time()),ENABLED_ACTIONS,time.time()+5)
    assert result.actions[0].action == "system.vol_down"
    payload = json.loads(next(r for r in requests if r.url.path == "/api/chat").content)
    assert payload["keep_alive"] == "30m"
    assert "delta" in payload["messages"][0]["content"]
    assert model.health()["model_loaded"]


def test_expired_deadline_never_calls_network(monkeypatch):
    from voice_assistant.decision import DecisionError
    monkeypatch.setattr(httpx,"Client", lambda **kw: pytest.fail("network after deadline"))
    with pytest.raises(DecisionError):
        OllamaDecision().propose("暂停",TrustedContext(now=time.time()),ENABLED_ACTIONS,time.time()-1)


def test_explicit_slots_preserve_folder_and_convert_time():
    from voice_assistant.decision import explicit_slots, decision_schema
    assert explicit_slots("声音小一点，顺便把这个收藏到旅行文件夹") == {"bilibili.favorite":{"folder":"旅行"}}
    assert explicit_slots("跳到两分零五秒") == {"bilibili.seek":{"target_s":125}}
    assert explicit_slots("把评论关掉") == {"bilibili.comments":{"open":False}}
    assert explicit_slots("发送弹幕 收藏到房车") == {}
    assert explicit_slots("跳到一分，然后跳到两分") == {}
    variants = decision_schema(("system.vol_down",))["properties"]["actions"]["items"]["anyOf"]
    assert len(variants) == 1
    assert variants[0]["properties"]["action"]["const"] == "system.vol_down"


def test_decoder_rejects_duplicate_keys_and_schema_blocks_negative_actions():
    from voice_assistant.decision import decision_schema
    from voice_assistant.intent import negated_actions
    assert OllamaDecision._parse('{"status":"ok","status":"out_of_scope","reason_code":"none","actions":[]}') is None
    forbidden = negated_actions("不要播放第三个，只暂停")
    assert {"bilibili.play","bilibili.resume"} <= forbidden
    schema = decision_schema(ENABLED_ACTIONS,{"bilibili.favorite":{"folder":"旅行"}},forbidden)
    variants = schema["properties"]["actions"]["items"]["anyOf"]
    assert all(v["properties"]["action"]["const"] not in forbidden for v in variants)
    favorite = next(v for v in variants if v["properties"]["action"]["const"] == "bilibili.favorite")
    assert "folder" in favorite["properties"]["params"]["required"]


def test_format_retry_explains_invalid_dependency(monkeypatch):
    calls = []
    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        proposal = {"status":"ok","reason_code":"none","actions":[
            {"id":0,"action":"bilibili.like","params":{},"after":[0] if len(calls) == 1 else []}]}
        return httpx.Response(200,json={"message":{"content":json.dumps(proposal)}})
    client = httpx.Client
    monkeypatch.setattr(httpx,"Client",lambda **kw:client(transport=httpx.MockTransport(respond),**kw))
    decision = OllamaDecision()
    proposal = decision.propose("给它点个赞",TrustedContext(now=time.time()),ENABLED_ACTIONS,time.time()+5)
    assert len(calls) == 2
    assert proposal.actions[0].after == []
    assert "after=[]" in calls[1]["messages"][-1]["content"]


def test_candidates_remove_unmentioned_action_families():
    from voice_assistant.decision import candidate_actions
    assert candidate_actions("别收藏，先暂停",ENABLED_ACTIONS) == ("bilibili.pause","bilibili.favorite")
    assert candidate_actions("发送弹幕 请点赞",ENABLED_ACTIONS) == ("bilibili.send_danmaku",)
    assert candidate_actions("无法识别的表达",ENABLED_ACTIONS) == ENABLED_ACTIONS


def test_full_search_context_keeps_indices_with_a_total_title_budget():
    from voice_assistant.contracts import SearchSnapshot, SearchResult
    now=time.time()
    search=SearchSnapshot(search_result_id=uuid4(),generation=uuid4(),query="猫",observed_at=now,expires_at=now+60,
                          results=[SearchResult(video_id=f"BV{i:010d}",title="很长的标题"*30,index=i,row=1,column=i) for i in range(1,51)])
    data=_context_payload(TrustedContext(now=now,search_snapshot=search),"播放第二个")
    cards=data["search"]["results"]
    assert len(cards)==50
    assert sum(len(card["title"]) for card in cards)<=2400
    assert cards[-1]["index"]==50 and cards[-1]["title_truncated"]
