"""Device-facing result labels: truthful, ASCII, short (firmware font has no CJK)."""
from uuid import uuid4

import pytest

from voice_assistant.contracts import CommandState, ErrorCode, Result
from voice_assistant.pipeline import result_detail


def test_out_of_scope_is_not_reported_as_search_expired():
    # Regression: the device used to match the Chinese substring "搜索" inside
    # this summary and show SEARCH EXPIRED / SEARCH AGAIN.
    summary = "没有识别到 B 站指令，请说打开B站、搜索、播放、暂停或继续"
    assert result_detail(CommandState.FAILED, ErrorCode.OUT_OF_SCOPE, summary) == "NOT A BILIBILI COMMAND"


def test_needs_search_versus_multiple_actions():
    assert result_detail(CommandState.FAILED, ErrorCode.CLARIFY_NEEDED, "请先搜索一个视频，再说第几个") == "NEED VISIBLE TARGET"
    assert result_detail(CommandState.FAILED, ErrorCode.CLARIFY_NEEDED, "这句话包含多个动作，请分开说，避免误操作") == "SAY ONE COMMAND"


def test_no_speech_variants():
    assert result_detail(CommandState.FAILED, ErrorCode.NO_SPEECH, "没有检测到语音，请重新说") == "NO SPEECH / TRY AGAIN"
    assert result_detail(CommandState.FAILED, ErrorCode.NO_SPEECH, "没有听清，请重新说") == "NOT CLEAR / TRY AGAIN"


def test_successful_noop_keeps_adapter_label():
    assert result_detail(CommandState.SUCCEEDED, None, "完成") == "DONE"
    assert result_detail(CommandState.SUCCEEDED, None, "ALREADY PLAYING") == "ALREADY PLAYING"
    assert result_detail(CommandState.CANCELED, None, "已取消") == "CANCELED"
    assert result_detail(CommandState.OUTCOME_UNKNOWN, None, "") == "UNKNOWN / CHECK MAC"


def test_every_code_has_an_ascii_short_label():
    for code in ErrorCode:
        label = result_detail(CommandState.FAILED, code, "没有听清，请重新说")
        assert label.isascii(), (code, label)
        assert 0 < len(label) <= 24, (code, label)


def test_result_message_carries_detail():
    result = Result(
        msg_id=0, command_id=uuid4(), state=CommandState.FAILED,
        code=ErrorCode.OUT_OF_SCOPE, summary="没有识别到 B 站指令",
        detail="NOT A BILIBILI COMMAND", step_results=[], replayable=False,
    )
    assert '"detail":"NOT A BILIBILI COMMAND"' in result.model_dump_json()


def test_search_field_missing_is_actionable():
    label = result_detail(CommandState.FAILED, ErrorCode.ACTION_FAILED, "请先回到B站首页，再搜索")
    assert label == "OPEN THE BILIBILI HOME"


def test_action_detail_labels():
    from voice_assistant.contracts import CommandState
    from voice_assistant.pipeline import action_detail
    assert action_detail(CommandState.SUCCEEDED, "bilibili.like") == "LIKED"
    assert action_detail(CommandState.SUCCEEDED, "bilibili.coin") == "COIN SENT"
    assert action_detail(CommandState.SUCCEEDED, "bilibili.favorite") == "FAVORITED"
    assert action_detail(CommandState.SUCCEEDED, "bilibili.seek") == "SEEKED"
    assert action_detail(CommandState.SUCCEEDED, "bilibili.send_danmaku") == "DANMAKU SENT"
    assert action_detail(CommandState.SUCCEEDED, "bilibili.next") == "NEXT EPISODE"
    assert action_detail(CommandState.SUCCEEDED, "bilibili.prev") == "PREV EPISODE"
    assert action_detail(CommandState.SUCCEEDED, "system.volume") == "VOLUME SET"
    assert action_detail(CommandState.FAILED, "bilibili.like") is None
    assert action_detail(CommandState.SUCCEEDED, "bilibili.play") is None
