"""Context-first resolution rules (no I/O; CDP context is injected)."""
from __future__ import annotations

from voice_assistant.context_rules import PageContext, cn_number, resolve


def _items(prefix: str, n: int = 5) -> list[dict]:
    return [{"bvid": f"BV{prefix}{i:09d}", "title": f"{prefix} 视频 {i}"} for i in range(1, n + 1)]


SEARCH = _items("S")
EPISODES = _items("E", 3)
RECS = _items("R", 4)


def test_chinese_number_parser():
    assert cn_number("三") == 3
    assert cn_number("十五") == 15
    assert cn_number("二十") == 20
    assert cn_number("两") == 2
    assert cn_number("12") == 12


def test_search_page_ordinal_uses_search_results():
    ctx = PageContext(page="search", search_results=SEARCH)
    r = resolve("播放第三个视频", ctx)
    assert r and r["action"] == "bilibili.play"
    assert r["params"]["bv"] == SEARCH[2]["bvid"]


def test_player_page_ordinal_defaults_to_episodes():
    ctx = PageContext(page="player", episodes=EPISODES, recommendations=RECS)
    r = resolve("播放第二个", ctx)
    assert r and r["params"]["bv"] == EPISODES[1]["bvid"], "合集优先是约定的默认规则"


def test_player_page_without_episodes_falls_back_to_recommendations():
    ctx = PageContext(page="player", recommendations=RECS)
    r = resolve("点开第4个", ctx)
    assert r and r["params"]["bv"] == RECS[3]["bvid"]


def test_explicit_recommendation_word_wins():
    ctx = PageContext(page="player", episodes=EPISODES, recommendations=RECS)
    r = resolve("推荐里第二个", ctx)
    assert r and r["params"]["bv"] == RECS[1]["bvid"]


def test_explicit_collection_word_wins_on_search_page():
    ctx = PageContext(page="search", search_results=SEARCH, episodes=EPISODES)
    r = resolve("合集第1个", ctx)
    assert r and r["params"]["bv"] == EPISODES[0]["bvid"]


def test_ordinal_out_of_range_clarifies():
    ctx = PageContext(page="player", episodes=EPISODES)
    r = resolve("播放第九个", ctx)
    assert r and r["action"] == "clarify"


def test_empty_list_clarifies():
    ctx = PageContext(page="search", search_results=[])
    r = resolve("播放第二个", ctx)
    assert r and r["action"] == "clarify"


def test_title_match_uses_search_results():
    ctx = PageContext(page="search", search_results=[
        {"bvid": "BV1xx411c7mD", "title": "一口气带你认识13种猫咪"},
        {"bvid": "BV1yy411c7mD", "title": "狗狗合集"},
    ])
    r = resolve("打开猫咪", ctx)
    assert r and r["params"]["bv"] == "BV1xx411c7mD"


def test_duplicates_and_invalid_bvids_are_dropped():
    ctx = PageContext(page="search", search_results=[
        {"bvid": "BV1xx411c7mD", "title": "a"},
        {"bvid": "BV1xx411c7mD", "title": "a again"},
        {"bvid": "not-a-bv", "title": "junk"},
        {"bvid": "BV1zz411c7mD", "title": "b"},
    ])
    r = resolve("播放第二个", ctx)
    assert r and r["params"]["bv"] == "BV1zz411c7mD"


def test_unrelated_text_is_left_to_the_normal_rules():
    assert resolve("暂停", PageContext(page="search", search_results=SEARCH)) is None
