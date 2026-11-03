"""Small deterministic guard for the R1 Bilibili command vocabulary.

The LLM is deliberately not allowed to turn arbitrary noise ("I.") into a
Bilibili action.  This guard is intentionally conservative: R1 only supports
open/search/play/pause/resume.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# An action verb is required. "点击第2个视频" is not an R1 action and must
# not reach the LLM/executor as a vague play/open request.
_KEYWORDS = ("打开", "搜索", "搜", "播放", "暂停", "继续", "恢复", "点击", "停",
             "点赞", "投币", "收藏", "弹幕", "评论", "快进", "后退", "回退", "倒退",
             "音量", "声音", "下一集", "下集", "上一集", "上集", "币")
_PUNCT = re.compile(r"[\s，。！？、,.!?;；:：\-—_（）()\[\]【】\"'“”‘’]+")


_VOICE_ALIASES = {
    "听一下": "停一下",
    "听下": "停下",
}


def normalize_intent_text(text: str) -> str:
    value = _PUNCT.sub("", text or "").lower()
    for spoken, canonical in _VOICE_ALIASES.items():
        if value == spoken:
            return canonical
    return value


def has_supported_intent(text: str) -> bool:
    t = normalize_intent_text(text)
    if len(t) < 2:
        return False
    return any(k in t for k in _KEYWORDS)


# ---------------------------------------------------------------------------
# Deterministic R1 parser
# ---------------------------------------------------------------------------
_CHINESE_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _index_value(token: str) -> int | None:
    if token.isdigit():
        value = int(token)
        return value if 1 <= value <= 50 else None
    return _CHINESE_NUM.get(token)


def _cn_number(token: str) -> int | None:
    if not token:
        return None
    if token.isdigit():
        return int(token)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total = 0
    current = 0
    for ch in token:
        if ch in digits:
            current = digits[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


def _ok(actions: list[tuple[str, dict]]) -> "DecisionProposal":
    from .contracts import ActionStep, DecisionProposal, DecisionStatus, ReasonCode
    return DecisionProposal(
        status=DecisionStatus.ok,
        reason_code=ReasonCode.none,
        actions=[ActionStep(id=i, action=action, params=params, after=after)
                 for i, (action, params, after) in enumerate(actions)],
    )


def _clarify(summary: str) -> "DecisionProposal":
    from .contracts import DecisionProposal, DecisionStatus, ReasonCode
    return DecisionProposal(status=DecisionStatus.clarify_needed,
                            reason_code=ReasonCode.missing_target,
                            clarification=summary, actions=[])


def _out_of_scope(summary: str) -> "DecisionProposal":
    from .contracts import DecisionProposal, DecisionStatus, ReasonCode
    return DecisionProposal(status=DecisionStatus.out_of_scope,
                            reason_code=ReasonCode.unsupported,
                            clarification=summary, actions=[])


_UNSUPPORTED_HINTS = {
    "关注": "关注属于 R2 账号动作，当前未启用",
}


def unsupported_intent_reason(text: str) -> str | None:
    t = normalize_intent_text(text)
    for key, reason in _UNSUPPORTED_HINTS.items():
        if key in t:
            return reason
    return None


def _search_result_by_grid(context, row: int, column: int):
    snap = getattr(context, "search_snapshot", None) if context is not None else None
    if snap is None or snap.expires_at < getattr(context, "now", 0):
        return None, None
    for item in snap.results:
        if getattr(item, "row", None) == row and getattr(item, "column", None) == column:
            return item, snap
    return None, snap


def clarify_proposal(summary: str) -> "DecisionProposal":
    return _clarify(summary)


def apply_policy_guard(text: str, proposal: "DecisionProposal | None") -> "DecisionProposal | None":
    """Constrain model fallback so it cannot invent side effects.

    A search is allowed only when the user explicitly said 搜索/搜. This is the
    concrete fix for "播放一排第一个视频" becoming search("一排第一个视频").
    """
    if proposal is None:
        return None
    t = normalize_intent_text(text)
    actions = [a.action for a in proposal.actions]
    if "bilibili.search" in actions and not any(k in t for k in ("搜索", "搜")):
        # A title direct-play rule intentionally searches the title first.
        if "bilibili.play" in actions and any(
            k in t for k in ("播放", "打开", "点击", "点开", "观看", "看", "放")
        ):
            pass
        else:
            return _clarify("这句话没有明确说搜索；请先明确说“搜索…”或用搜索结果说第几个")
    if len(actions) > 1 and not any(k in t for k in ("然后", "并", "再", "搜索", "搜")):
        return _clarify("这句话包含多个动作，请分开说，避免误操作")
    return proposal


def rule_based_proposal(text: str, context=None) -> "DecisionProposal | None":
    """Handle the small R1 command grammar without the LLM.

    The LLM is useful for fuzzy wording, but must not be allowed to invent an
    extra search/play step for "打开B站". These deterministic rules cover the
    common commands and leave genuinely ambiguous wording to the model.
    """
    import re
    t = normalize_intent_text(text)
    if not t:
        return None
    # Light filler/punctuation tolerance without changing stored transcripts.
    t = re.sub(r"^(?:请|麻烦|帮我|帮忙|给我|现在|那个|这个)+", "", t)
    t = re.sub(r"(?:一下|吧|啊|呀|呢|谢谢)$", "", t)
    if ("不要" in t or "别" in t) and any(k in t for k in ("打开", "搜索", "播放", "暂停", "继续", "恢复")):
        return _out_of_scope("已取消，不会执行该动作")

    # --- R2 player controls (deterministic; execution is verified by UI read-back) ---
    if "点赞" in t or "赞一下" in t or "点个赞" in t or t == "赞":
        return _ok([("bilibili.like", {}, [])])
    if "投币" in t or "投个币" in t or ("投" in t and "币" in t):
        count = 2 if re.search(r"(?:两|2)(?:个|枚)?币?|两个币|两枚", t) else 1
        return _ok([("bilibili.coin", {"count": count}, [])])
    if "收藏" in t and "取消收藏" not in t:
        folder = None
        fm = re.search(r"收藏(?:到|至|进|在)([^，。,.!！?？]{1,20}?)(?:收藏夹|文件夹|里|中)?$", t)
        if fm:
            folder = fm.group(1).strip()
        return _ok([("bilibili.favorite", {"folder": folder} if folder else {}, [])])
    if "评论" in t and any(k in t for k in ("关闭", "收起", "关掉", "关")):
        return _ok([("bilibili.comments", {"open": False}, [])])
    if "评论" in t and any(k in t for k in ("打开", "开", "看", "展开", "去", "进入")):
        return _ok([("bilibili.comments", {"open": True}, [])])
    if "弹幕" in t and any(k in t for k in ("发送", "发", "发个", "发一条", "发条", "写")):
        text = ""
        dm = re.search(r"弹幕(?:说|写|内容)?[：:，,]?(.+)$", t)
        if dm:
            text = dm.group(1).strip()
        if not text:
            dm = re.search(r"(?:发送|发)(?:一条|个|条)?(.+)$", t)
            if dm:
                text = dm.group(1).replace("弹幕", "").strip()
        if text:
            return _ok([("bilibili.send_danmaku", {"text": text[:30]}, [])])
        return _clarify("请说要发送的弹幕内容，例如“发送弹幕 好可爱”")
    if any(k in t for k in ("快进", "后退", "回退", "倒退", "倒回")):
        mm = re.search(r"到第?([0-9零一二三四五六七八九十百两]+)分([0-9零一二三四五六七八九十百两]+)秒", t)
        if mm:
            minutes, seconds = _cn_number(mm.group(1)), _cn_number(mm.group(2))
            if minutes is not None and seconds is not None:
                return _ok([("bilibili.seek", {"target_s": minutes * 60 + seconds}, [])])
        mm = re.search(r"到第?([0-9零一二三四五六七八九十百两]+)(?:分钟|分)", t)
        if mm and _cn_number(mm.group(1)) is not None:
            return _ok([("bilibili.seek", {"target_s": _cn_number(mm.group(1)) * 60}, [])])
        mm = re.search(r"到第?([0-9零一二三四五六七八九十百两]+)秒", t)
        if mm and _cn_number(mm.group(1)) is not None:
            return _ok([("bilibili.seek", {"target_s": _cn_number(mm.group(1))}, [])])
        mm = re.search(r"(?:快进|前进)([0-9零一二三四五六七八九十百两]+)秒", t)
        if mm and _cn_number(mm.group(1)) is not None:
            return _ok([("bilibili.seek", {"delta_s": _cn_number(mm.group(1))}, [])])
        mm = re.search(r"(?:后退|回退|倒退|倒回)([0-9零一二三四五六七八九十百两]+)秒", t)
        if mm and _cn_number(mm.group(1)) is not None:
            return _ok([("bilibili.seek", {"delta_s": -_cn_number(mm.group(1))}, [])])
        return _clarify("请说快进多少秒，或快进到几分几秒")
    if "下一集" in t or "下集" in t:
        return _ok([("bilibili.next", {}, [])])
    if "上一集" in t or "上集" in t:
        return _ok([("bilibili.prev", {}, [])])
    if any(k in t for k in ("音量", "声音")):
        vm = re.search(r"百分之([0-9零一二三四五六七八九十百两]+)", t)
        if vm and _cn_number(vm.group(1)) is not None:
            return _ok([("system.volume", {"target": max(0, min(100, _cn_number(vm.group(1))))}, [])])
        vm = re.search(r"([0-9]+)%", t)
        if vm:
            return _ok([("system.volume", {"target": max(0, min(100, int(vm.group(1))))}, [])])
        if any(k in t for k in ("大", "高", "响")):
            return _ok([("system.vol_up", {"delta": 5}, [])])
        if any(k in t for k in ("小", "低", "轻")):
            return _ok([("system.vol_down", {"delta": 5}, [])])
        return _clarify("请说调大、调小，或调到百分之多少")

    # Accept both natural orders produced by ASR:
    #   第2排第3个视频 / 点击第2排第3个
    #   点击第3个视频第2排
    spatial = re.search(r"第?([0-9一二三四五六七八九十]+)[排行]第?([0-9一二三四五六七八九十]+)个(?:视频)?", t)
    spatial_rev = None
    if spatial is None:
        spatial_rev = re.search(r"第?([0-9一二三四五六七八九十]+)个(?:视频)?第?([0-9一二三四五六七八九十]+)[排行]", t)
        if spatial_rev:
            spatial = spatial_rev
    if spatial:
        if spatial_rev is not None:
            column = _index_value(spatial.group(1))
            row = _index_value(spatial.group(2))
        else:
            row = _index_value(spatial.group(1))
            column = _index_value(spatial.group(2))
        item, snap = _search_result_by_grid(context, row or 0, column or 0)
        if item is None:
            if snap is None:
                return _clarify("请先搜索一个视频，再说第几排第几个")
            return _clarify("当前搜索结果没有第%s排第%s个，请重新搜索" % (spatial.group(1), spatial.group(2)))
        return _ok([("bilibili.play",
                     {"index": item.index, "search_result_id": str(snap.search_result_id)}, [])])

    ordinal = re.search(r"第?([0-9一二三四五六七八九十]+)个(?:视频)?", t)
    if ordinal and "排" not in t and "行" not in t and any(k in t for k in ("播放", "打开", "点击", "点开", "观看", "看", "放")) and not any(k in t for k in ("搜索", "搜")):
        index = _index_value(ordinal.group(1))
        snap = getattr(context, "search_snapshot", None) if context is not None else None
        if snap is None or snap.expires_at < getattr(context, "now", 0):
            return _clarify("请先搜索一个视频，再说第几个")
        match = next((r for r in snap.results if r.index == index), None)
        if match is None:
            return _clarify("当前搜索结果没有第%s个视频，请重新搜索" % ordinal.group(1))
        return _ok([("bilibili.play",
                     {"index": match.index, "search_result_id": str(snap.search_result_id)}, [])])

    open_intent = ("打开" in t or "启动" in t) and ("b站" in t or "哔哩" in t)
    other_verb = any(k in t for k in ("搜索", "搜", "播放", "暂停", "继续", "恢复", "第"))
    if open_intent and not other_verb:
        return _ok([("bilibili.open", {}, [])])

    search_match = re.search(r"(?:搜索|搜)(.+?)(?:(?:然后|并|再)?播放第|播放第|$)", t)
    index_match = re.search(r"播放第([0-9一二三四五六七八九十]+)个", t)
    index = _index_value(index_match.group(1)) if index_match else None

    if search_match and search_match.group(1):
        query = search_match.group(1).strip("，。,.!！?？、 ")
        if query:
            if index is not None:
                return _ok([
                    ("bilibili.search", {"query": query}, []),
                    ("bilibili.play", {"index": index,
                                       "search_result_id": "$step_0.output.search_result_id"}, [0]),
                ])
            return _ok([("bilibili.search", {"query": query}, [])])

    if index is not None and "搜索" not in t and "搜" not in t:
        snap = getattr(context, "search_snapshot", None) if context is not None else None
        if snap is None or snap.expires_at < getattr(context, "now", 0):
            return _clarify("请先搜索一个视频，再说播放第几个")
        return _ok([("bilibili.play",
                     {"index": index, "search_result_id": str(snap.search_result_id)}, [])])

    bv = re.search(r"(BV[0-9A-Za-z]{10})", text or "")
    if bv and ("播放" in t or "看" in t):
        return _ok([("bilibili.play", {"bv": bv.group(1)}, [])])

    if t in ("播放", "播放视频", "开始播放"):
        player = getattr(context, "player_snapshot", None) if context is not None else None
        if player is not None and player.expires_at >= getattr(context, "now", 0):
            return _ok([("bilibili.resume", {}, [])])
        return _clarify("没有可继续播放的视频，请先搜索并播放")
    if t in ("暂停播放", "暂停视频", "停止播放", "停", "停止"):
        return _ok([("bilibili.pause", {}, [])])
    if any(k in t for k in ("停一下", "停住", "停下")) and not any(k in t for k in ("继续", "恢复", "播放")):
        return _ok([("bilibili.pause", {}, [])])
    if "暂停" in t and not any(k in t for k in ("继续", "恢复", "播放")):
        return _ok([("bilibili.pause", {}, [])])
    if any(k in t for k in ("继续", "恢复", "接着播")):
        return _ok([("bilibili.resume", {}, [])])
    # Direct title: "播放/打开/点击 <视频标题>".  Prefer a visible search
    # snapshot; otherwise search the title and play its first result.
    media_verbs = ("播放", "打开", "点击", "点开", "观看", "看", "放")
    if any(t.startswith(v) for v in media_verbs) and not any(
        k in t for k in ("搜索", "搜", "第", "排", "行", "B站", "b站", "哔哩")
    ):
        query = t
        for verb in media_verbs:
            if query.startswith(verb):
                query = query[len(verb):]
                break
        query = re.sub(r"^(?:一下|下|这个|那个|视频)+", "", query)
        query = re.sub(r"(?:视频|一下|吧)+$", "", query).strip()
        if len(query) >= 2:
            snap = getattr(context, "search_snapshot", None) if context is not None else None
            best = None
            best_score = 0.0
            if snap is not None and snap.expires_at >= getattr(context, "now", 0):
                qkey = "".join(ch for ch in query.lower() if ch.isalnum())
                for item in snap.results:
                    tkey = "".join(ch for ch in str(item.title).lower() if ch.isalnum())
                    if not qkey or not tkey:
                        continue
                    if qkey in tkey or tkey in qkey:
                        score = 1.0
                    else:
                        score = SequenceMatcher(None, qkey, tkey).ratio()
                    if score > best_score:
                        best_score, best = score, item
            if best is not None and best_score >= 0.45:
                return _ok([("bilibili.play",
                             {"index": best.index,
                              "search_result_id": str(snap.search_result_id)}, [])])
            # No visible match: search the spoken title, then play first result.
            return _ok([
                ("bilibili.search", {"query": query}, []),
                ("bilibili.play", {"index": 1,
                                   "search_result_id": "$step_0.output.search_result_id"}, [0]),
            ])

    # A spatial/ordinal target we could not resolve must never fall through to
    # the LLM, where it can become an invented search. Ask for context instead.
    if any(k in t for k in ("第", "排", "行")) and ("视频" in t or "个" in t):
        return _clarify("请先搜索一个视频，再说第几排第几个；没有搜索上下文时不会猜测")
    return None
