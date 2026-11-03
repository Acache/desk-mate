"""Context-first resolution of deictic Bilibili commands.

The user's rule (2026-11-04): resolve "第 N 个 / 这个 / 下一个" against the
screen that is actually in front of the user, not against the previous
command's database snapshot.

This module is pure: it receives the transcript plus the page context that the
CDP collector read (page type, search results, episode list, recommendations)
and returns the action to run. It has no I/O so every rule is unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_PUNCT = re.compile(r"[\s，。！？、,.!?;；:：\-—_（）()\[\]【】\"'“”‘’]+")


def cn_number(token: str) -> int | None:
    if not token:
        return None
    if token.isdigit():
        return int(token)
    total = 0
    current = 0
    for ch in token:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


def normalize(text: str) -> str:
    return _PUNCT.sub("", text or "").lower()


@dataclass
class PageContext:
    """What the CDP collector saw right before the command."""

    page: str = "other"          # search | player | home | other
    search_results: list[dict] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    player: dict = field(default_factory=dict)

    @staticmethod
    def _item(entry: Any) -> dict | None:
        if not isinstance(entry, dict):
            return None
        bvid = str(entry.get("bvid") or "")
        if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            return None
        return {"bvid": bvid, "title": str(entry.get("title") or "")[:80]}


def _clean(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for entry in items or []:
        item = PageContext._item(entry)
        if item and item["bvid"] not in seen:
            seen.add(item["bvid"])
            out.append(item)
    return out


ORDINAL_RE = re.compile(r"第?([0-9零一二三四五六七八九十百两]+)个(?:视频|结果)?")
ORDINAL_TITLE_RE = re.compile(r"第?([0-9零一二三四五六七八九十百两]+)个")


def resolve(text: str, context: PageContext) -> dict | None:
    """Return {"action","params","why"} or None when this module cannot decide.

    ``None`` means "leave it to the normal intent rules / the model".
    """
    t = normalize(text)
    if not t:
        return None

    lists = {
        "search": _clean(context.search_results),
        "episodes": _clean(context.episodes),
        "recommendations": _clean(context.recommendations),
    }

    # --- explicit list words win over the page default -------------------
    explicit: str | None = None
    if any(k in t for k in ("推荐", "相关", "右边", "右侧")):
        explicit = "recommendations"
    elif any(k in t for k in ("合集", "选集", "分p", "第几p", "这一p", "这一集", "这集")):
        explicit = "episodes"

    ordinal = ORDINAL_RE.search(t) or ORDINAL_TITLE_RE.search(t)
    if ordinal:
        index = cn_number(ordinal.group(1))
        if index is None or index < 1:
            return None
        chosen = explicit
        if chosen is None:
            if context.page == "search":
                chosen = "search"
            elif context.page == "player":
                # Default rule agreed with the user: collection first.
                chosen = "episodes" if lists["episodes"] else "recommendations"
        if chosen is None:
            return {"action": "clarify", "params": {},
                    "why": "当前页面没有可点的列表，请先说搜索或打开一个视频"}
        items = lists[chosen]
        if not items:
            label = {"search": "搜索结果", "episodes": "合集", "recommendations": "推荐"}[chosen]
            return {"action": "clarify", "params": {},
                    "why": f"当前没有{label}列表，请先搜索或打开视频"}
        if index > len(items):
            return {"action": "clarify", "params": {},
                    "why": f"当前{chosen}只有{len(items)}个"}
        return {"action": "bilibili.play", "params": {"bv": items[index - 1]["bvid"]},
                "why": f"{chosen}[{index}]"}

    # --- title match: "打开 某某视频" -------------------------------------
    plain = re.sub(r"^(?:请|麻烦|帮我|给我)?(?:播放|打开|点开|观看|看)", "", t)
    plain = re.sub(r"(?:这个|那个|视频|吧|一下)+$", "", plain).strip()
    if len(plain) >= 2 and plain not in ("视频", "这个", "那个", "一下"):
        pool = []
        for name in (explicit,) if explicit else ("episodes", "search", "recommendations"):
            pool.extend(lists[name])
        for item in pool:
            hay = normalize(item["title"])
            if plain and (plain in hay or hay in plain):
                return {"action": "bilibili.play", "params": {"bv": item["bvid"]},
                        "why": "title"}
    return None
