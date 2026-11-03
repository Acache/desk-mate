from voice_assistant.evaluation import evaluate_case, report


def test_rules_evaluation_never_needs_model_or_executor():
    case = {"id":"pause", "text":"暂停", "expected":{"status":"ok","actions":[
        {"action":"bilibili.pause","params":{},"after":[]}]}}
    row = evaluate_case(case,None)
    assert row["passed"] and row["route"] == "rules"
    assert report([row],"unused","routed")["executed_actions"] is False


def test_missing_actions_are_reported():
    case = {"id":"omission", "text":"暂停", "expected":{"status":"ok","actions":[
        {"action":"bilibili.pause","params":{},"after":[]},
        {"action":"bilibili.like","params":{},"after":[0]}]}}
    row = evaluate_case(case,None)
    assert not row["passed"] and row["missing_actions"] == 1
