from voice_assistant.intent import has_supported_intent


def test_supported_r1_phrases():
    for text in ("打开B站", "打开哔哩哔哩", "搜索猫", "暂停", "继续播放", "恢复", "播放第二个"):
        assert has_supported_intent(text), text


def test_noise_and_short_text_rejected():
    for text in ("", ".", "I.", "嗯", "hello", "谢谢", "第2个视频"):
        assert not has_supported_intent(text), text


def test_compound_and_questions_reach_semantic_model():
    from voice_assistant.intent import rule_based_proposal, is_semantic_request
    for text in ("声音小一点，顺便把这个收藏到房车", "点赞然后收藏", "不要点赞，只收藏", "我刚才点赞了吗？", "点赞收藏"):
        assert rule_based_proposal(text) is None, text
    assert is_semantic_request("不要点赞，只收藏")
    assert has_supported_intent("小声一点")


def test_classifier_never_absorbs_r2_or_negation():
    from voice_assistant.intent import personalization_candidate, apply_policy_guard, _ok
    for text in ("声音稍微大一点", "不要暂停", "暂停然后收藏", "打开第三个视频"):
        assert not personalization_candidate(text)
    assert personalization_candidate("帮我暂停一会儿")
    for text in ("取消点赞", "我刚才点赞了吗？"):
        p = apply_policy_guard(text,_ok([("bilibili.like",{},[])]))
        assert not p.actions


def test_literal_payload_is_not_parsed_as_an_action_word():
    from voice_assistant.intent import rule_based_proposal
    p = rule_based_proposal("发送弹幕 Hello, 别忘了点赞！")
    assert p.actions[0].action == "bilibili.send_danmaku"
    # Trailing sentence punctuation belongs to the sentence, not the payload.
    assert p.actions[0].params["text"] == "Hello, 别忘了点赞"
    p = rule_based_proposal("搜索如何收藏视频")
    assert p.actions[0].action == "bilibili.search"
    assert p.actions[0].params["query"] == "如何收藏视频"
    assert rule_based_proposal("发送弹幕 " + "字"*31).status.value == "clarify_needed"
    assert rule_based_proposal("发送弹幕 Hello。").actions[0].params["text"] == "Hello"
    assert rule_based_proposal("收藏到 My Work 文件夹").actions[0].params["folder"] == "My Work"
    assert rule_based_proposal("收藏到"+"字"*33).status.value == "clarify_needed"


def _ctx():
    import time
    from voice_assistant.contracts import TrustedContext
    return TrustedContext(now=time.time())


def test_open_never_invents_extra_search():
    p = __import__("voice_assistant.intent", fromlist=["rule_based_proposal"]).rule_based_proposal("打开B站。", _ctx())
    assert p is not None and p.status.value == "ok"
    assert [(a.action, a.params) for a in p.actions] == [("bilibili.open", {})]


def test_search_and_play_is_deterministic():
    p = __import__("voice_assistant.intent", fromlist=["rule_based_proposal"]).rule_based_proposal("搜索猫，播放第二个", _ctx())
    assert p is not None and [a.action for a in p.actions] == ["bilibili.search", "bilibili.play"]
    assert p.actions[0].params["query"] == "猫"
    assert p.actions[1].params["index"] == 2
    assert p.actions[1].params["search_result_id"].startswith("$step_0")


def test_pause_and_resume_rules():
    from voice_assistant.intent import rule_based_proposal
    assert [a.action for a in rule_based_proposal("暂停", _ctx()).actions] == ["bilibili.pause"]
    assert [a.action for a in rule_based_proposal("继续播放", _ctx()).actions] == ["bilibili.resume"]
    assert rule_based_proposal("不要暂停", _ctx()).status.value == "out_of_scope"


def test_spatial_search_target_maps_to_result_index():
    import time, uuid
    from voice_assistant.contracts import SearchSnapshot, SearchResult, TrustedContext
    from voice_assistant.intent import rule_based_proposal
    now = time.time()
    results = [SearchResult(video_id=f"BV{i:010d}", title=f"v{i}", index=i,
                            row=(i - 1) // 4 + 1, column=(i - 1) % 4 + 1)
               for i in range(1, 9)]
    snap = SearchSnapshot(search_result_id=uuid.uuid4(), results=results,
                          observed_at=now, expires_at=now + 60,
                          generation=uuid.uuid4(), query="猫")
    ctx = TrustedContext(now=now, search_snapshot=snap)
    p = rule_based_proposal("播放第二排第一个视频。", ctx)
    assert p is not None and p.actions[0].action == "bilibili.play"
    assert p.actions[0].params["index"] == 5


def test_bare_play_uses_resume_when_player_exists():
    import time, uuid
    from voice_assistant.contracts import PlayerSnapshot, TrustedContext
    from voice_assistant.intent import rule_based_proposal
    now = time.time()
    player = PlayerSnapshot(video_id="BV1xx411c7mD", generation=uuid.uuid4(),
                            paused=True, position_ms=0, observed_at=now,
                            expires_at=now + 60)
    ctx = TrustedContext(now=now, player_snapshot=player)
    p = rule_based_proposal("播放。", ctx)
    assert p is not None and p.actions[0].action == "bilibili.resume"


def test_player_controls_are_supported():
    from voice_assistant.intent import rule_based_proposal, unsupported_intent_reason
    assert unsupported_intent_reason("收藏这个视频") is None
    cases = {
        "点赞": ("bilibili.like", {}),
        "给视频点赞": ("bilibili.like", {}),
        "投币": ("bilibili.coin", {"count": 1}),
        "投两个币": ("bilibili.coin", {"count": 2}),
        "收藏这个视频": ("bilibili.favorite", {}),
        "收藏到房车": ("bilibili.favorite", {"folder": "房车"}),
        "收藏到干活白噪音文件夹": ("bilibili.favorite", {"folder": "干活白噪音"}),
        "打开评论区": ("bilibili.comments", {"open": True}),
        "关闭评论区": ("bilibili.comments", {"open": False}),
        "快进30秒": ("bilibili.seek", {"delta_s": 30}),
        "后退10秒": ("bilibili.seek", {"delta_s": -10}),
        "快进到第1分30秒": ("bilibili.seek", {"target_s": 90}),
        "快进到30秒": ("bilibili.seek", {"target_s": 30}),
        "下一集": ("bilibili.next", {}),
        "上一集": ("bilibili.prev", {}),
        "音量调到百分之六十": ("system.volume", {"target": 60}),
        "音量调到60%": ("system.volume", {"target": 60}),
        "音量调大": ("system.vol_up", {"delta": 5}),
        "音量调小": ("system.vol_down", {"delta": 5}),
        "发送弹幕 好可爱": ("bilibili.send_danmaku", {"text": "好可爱"}),
    }
    for text, (action, params) in cases.items():
        proposal = rule_based_proposal(text)
        assert proposal is not None, text
        assert [a.action for a in proposal.actions] == [action], (text, proposal)
        assert proposal.actions[0].params == params, (text, proposal.actions[0].params)


def test_follow_is_still_unsupported():
    from voice_assistant.intent import unsupported_intent_reason
    assert unsupported_intent_reason("关注这个UP主")


def test_click_ordinal_uses_search_snapshot():
    import time, uuid
    from voice_assistant.contracts import SearchSnapshot, SearchResult, TrustedContext
    from voice_assistant.intent import rule_based_proposal
    now = time.time()
    snap = SearchSnapshot(search_result_id=uuid.uuid4(),
                          results=[SearchResult(video_id=f"BV{i:010d}", title=f"v{i}", index=i,
                                                row=(i - 1) // 4 + 1, column=(i - 1) % 4 + 1)
                                   for i in range(1, 9)],
                          observed_at=now, expires_at=now + 60,
                          generation=uuid.uuid4(), query="猫")
    ctx = TrustedContext(now=now, search_snapshot=snap)
    p = rule_based_proposal("点击第一个视频。", ctx)
    assert p is not None and p.actions[0].params["index"] == 1
    assert rule_based_proposal("点击第二排第一个视频。", ctx).actions[0].params["index"] == 5


def test_pause_play_variant_is_rule_based():
    from voice_assistant.intent import rule_based_proposal
    p = rule_based_proposal("暂停播放。", _ctx())
    assert p is not None and p.actions[0].action == "bilibili.pause"


def test_stop_variant_is_deterministic():
    from voice_assistant.intent import rule_based_proposal
    p = rule_based_proposal("停一下。", _ctx())
    assert p is not None and p.actions[0].action == "bilibili.pause"


def test_voice_alias_listen_to_stop():
    from voice_assistant.intent import normalize_intent_text, rule_based_proposal
    assert normalize_intent_text("听一下。") == "停一下"
    p = rule_based_proposal("听一下。", _ctx())
    assert p is not None and p.actions[0].action == "bilibili.pause"


def test_spatial_without_di_and_filler():
    import time, uuid
    from voice_assistant.contracts import SearchSnapshot, SearchResult, TrustedContext
    from voice_assistant.intent import rule_based_proposal
    now = time.time()
    snap = SearchSnapshot(search_result_id=uuid.uuid4(),
                          results=[SearchResult(video_id="BV1mxNueyEZ7", title="v", index=1,
                                                row=1, column=1)],
                          observed_at=now, expires_at=now + 60,
                          generation=uuid.uuid4(), query="猫")
    ctx = TrustedContext(now=now, search_snapshot=snap)
    for text in ("播放一排第一个视频。", "麻烦播放第一排第一个视频一下。"):
        p = rule_based_proposal(text, ctx)
        assert p is not None and p.actions[0].action == "bilibili.play"
        assert p.actions[0].params["index"] == 1


def test_model_search_without_search_verb_is_blocked():
    from voice_assistant.contracts import ActionStep, DecisionProposal, DecisionStatus, ReasonCode
    from voice_assistant.intent import apply_policy_guard
    proposal = DecisionProposal(status=DecisionStatus.ok, reason_code=ReasonCode.none,
                                actions=[ActionStep(id=0, action="bilibili.search",
                                                    params={"query": "一排第一个视频"})])
    guarded = apply_policy_guard("播放一排第一个视频。", proposal)
    assert guarded.status is DecisionStatus.clarify_needed
    assert not guarded.actions


def test_unresolved_target_does_not_fall_to_llm():
    from voice_assistant.intent import rule_based_proposal
    p = rule_based_proposal("打开第几个视频。", _ctx())
    assert p is not None
    assert not p.actions
    assert "搜索" in (p.clarification or "")


def test_reverse_spatial_word_order():
    import time, uuid
    from voice_assistant.contracts import SearchSnapshot, SearchResult, TrustedContext
    from voice_assistant.intent import rule_based_proposal
    now = time.time()
    snap = SearchSnapshot(search_result_id=uuid.uuid4(),
                          results=[SearchResult(video_id="BV1mxNueyEZ7", title="v", index=7,
                                                row=2, column=3)],
                          observed_at=now, expires_at=now + 60,
                          generation=uuid.uuid4(), query="q")
    ctx = TrustedContext(now=now, search_snapshot=snap)
    for text in ("点击第三个视频第二排。", "点击第2排第3个视频。"):
        p = rule_based_proposal(text, ctx)
        assert p is not None and p.actions[0].action == "bilibili.play"
        assert p.actions[0].params["index"] == 7


def test_title_direct_play_uses_visible_result():
    import time, uuid
    from voice_assistant.contracts import SearchSnapshot, SearchResult, TrustedContext
    from voice_assistant.intent import rule_based_proposal
    now = time.time()
    snap = SearchSnapshot(search_result_id=uuid.uuid4(),
                          results=[SearchResult(video_id="BV19u8BzREWm",
                                                title="【麻省理工】2025深度学习导论 MIT 6.S191",
                                                index=1, row=1, column=1)],
                          observed_at=now, expires_at=now + 60,
                          generation=uuid.uuid4(), query="深度学习")
    ctx = TrustedContext(now=now, search_snapshot=snap)
    p = rule_based_proposal("播放麻省理工深度学习导论", ctx)
    assert p is not None and p.actions[0].action == "bilibili.play"
    assert p.actions[0].params["index"] == 1


def test_title_without_snapshot_searches_then_plays():
    from voice_assistant.intent import rule_based_proposal
    p = rule_based_proposal("点击麻省理工深度学习导论", _ctx())
    assert p is not None
    assert [a.action for a in p.actions] == ["bilibili.search", "bilibili.play"]


def test_row_synonym_does_not_fall_back_to_ordinal():
    from voice_assistant.intent import rule_based_proposal
    p = rule_based_proposal("点击第三行第一个视频", _ctx())
    assert p is not None and not p.actions
    assert "搜索" in p.clarification


def test_close_home_mute_and_p_aliases():
    from voice_assistant.intent import rule_based_proposal
    for text in ("关闭。", "关闭这个视频。", "关掉这个视频。", "回到主界面。", "回主页。"):
        p = rule_based_proposal(text)
        assert p is not None and p.actions[0].action == "bilibili.home", text
    p = rule_based_proposal("静音。")
    assert p is not None and p.actions[0].action == "system.mute"
    # ASR hears "上一P" as 上一批/上一排/上一个视频; all normalise to prev.
    for text in ("上一批。", "上一排。", "上一个视频。", "上P。"):
        p = rule_based_proposal(text)
        assert p is not None and p.actions[0].action == "bilibili.prev", text
    for text in ("下一批。", "下一个视频。"):
        p = rule_based_proposal(text)
        assert p is not None and p.actions[0].action == "bilibili.next", text


def test_search_and_danmaku_strip_trailing_punctuation():
    from voice_assistant.intent import rule_based_proposal
    assert rule_based_proposal("搜索猫咪。").actions[0].params["query"] == "猫咪"
    assert rule_based_proposal("搜索猫咪.").actions[0].params["query"] == "猫咪"
    assert rule_based_proposal("发个弹幕说的好。").actions[0].params["text"] == "说的好"
