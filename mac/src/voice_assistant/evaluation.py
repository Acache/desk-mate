"""Read-only intent evaluation: proposals and plans, never adapter execution."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import statistics
import time
from pathlib import Path
from uuid import UUID, uuid4

from .contracts import (ACTION_PARAM_TYPES, ENABLED_ACTIONS, PlayerSnapshot,
                        SearchResult, SearchSnapshot, TrustedContext)
from .decision import OllamaDecision
from .intent import apply_policy_guard, has_supported_intent, rule_based_proposal
from .planner import build_plan


def case_context(case: dict) -> TrustedContext:
    now = time.time()
    player = None
    search = None
    if case.get("player", True):
        player = PlayerSnapshot(video_id="BV1xx411c7mD", generation=uuid4(), paused=False,
                                position_ms=45000, observed_at=now, expires_at=now+120)
    if case.get("search"):
        search = SearchSnapshot(search_result_id=UUID(int=1), generation=uuid4(),
                                query="猫", observed_at=now, expires_at=now+120,
                                results=[SearchResult(video_id=f"BV{i:010d}", title=f"猫视频{i}",
                                                      index=i, row=1, column=i) for i in range(1,5)])
    return TrustedContext(now=now, player_snapshot=player, search_snapshot=search)


def normalized_actions(actions) -> list[dict]:
    return [{"action": a.action, "params": ACTION_PARAM_TYPES[a.action].model_validate(a.params).model_dump(exclude_none=True),
             "after": a.after} for a in actions]


def evaluate_case(case: dict, decision, mode: str = "routed", timeout: float = 30) -> dict:
    context = case_context(case)
    started = time.perf_counter()
    route = "rules"
    row = {"id": case["id"], "text": case["text"], "expected": case["expected"]}
    try:
        proposal = rule_based_proposal(case["text"], context) if mode == "routed" else None
        if proposal is None:
            route = "ollama"
            proposal = decision.propose(case["text"], context, ENABLED_ACTIONS, time.time()+timeout)
        original = proposal
        proposal = apply_policy_guard(case["text"], proposal)
        if original != proposal:
            row["before_policy_guard"] = original.model_dump(mode="json")
        actual = {"status": proposal.status.value, "actions": normalized_actions(proposal.actions)}
        plan_valid = True
        if proposal.status.value == "ok":
            try:
                build_plan(proposal, command_id=str(uuid4()), device_id=str(uuid4()),
                           enabled_actions=ENABLED_ACTIONS, context=context)
            except Exception as exc:
                plan_valid = False
                row["plan_error"] = str(exc)
        expected = case["expected"]
        expected_names = Counter(a["action"] for a in expected["actions"])
        actual_names = Counter(a["action"] for a in actual["actions"])
        row.update(actual=actual, plan_valid=plan_valid,
                   passed=actual == expected and plan_valid,
                   missing_actions=sum((expected_names-actual_names).values()),
                   extra_actions=sum((actual_names-expected_names).values()))
    except Exception as exc:
        row.update(passed=False, error=str(exc), missing_actions=len(case["expected"]["actions"]),extra_actions=0)
        if getattr(decision,"last_response",None):
            row["invalid_response"] = decision.last_response
    row.update(route=route, latency_ms=round((time.perf_counter()-started)*1000,2))
    return row


def report(rows: list[dict], model: str, mode: str) -> dict:
    latencies = sorted(r["latency_ms"] for r in rows)
    clarifications = [r for r in rows if r["expected"]["status"] == "clarify_needed"]
    by_route = {}
    for route in ("rules","ollama"):
        values = sorted(r["latency_ms"] for r in rows if r["route"] == route)
        by_route[route] = {"cases":len(values),"median_ms":statistics.median(values) if values else None,
                           "p95_ms":values[min(len(values)-1,int(len(values)*.95))] if values else None}
    return {"model":model,"mode":mode,"executed_actions":False,"personalizer_enabled":False,"cases":len(rows),
            "passed":sum(r["passed"] for r in rows),
            "exact_match_rate":sum(r["passed"] for r in rows)/max(1,len(rows)),
            "missing_actions":sum(r["missing_actions"] for r in rows),
            "extra_actions":sum(r.get("extra_actions",0) for r in rows),
            "false_clarifications":sum(r.get("actual",{}).get("status") == "clarify_needed" and r["expected"]["status"] != "clarify_needed" for r in rows),
            "by_route":by_route,
            "clarification_correct":sum(r["passed"] for r in clarifications),
            "clarification_cases":len(clarifications),
            "median_ms":statistics.median(latencies) if rows else 0,
            "p95_ms":latencies[min(len(latencies)-1,int(len(latencies)*.95))] if rows else 0,
            "results":rows}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--model",default="qwen2.5:1.5b")
    p.add_argument("--url",default="http://127.0.0.1:11434")
    p.add_argument("--mode",choices=("routed","model"),default="routed")
    p.add_argument("--timeout",type=float,default=30)
    args = p.parse_args(argv)
    model = OllamaDecision(args.model,args.url)
    model.prewarm()
    cases = json.loads(args.corpus.read_text())
    rows = []
    for case in cases:
        row = evaluate_case(case,model,args.mode,args.timeout)
        rows.append(row)
        print(f"{case['id']}: {'PASS' if row['passed'] else 'FAIL'} {row['route']} {row['latency_ms']} ms",flush=True)
    result = report(rows,args.model,args.mode)
    import hashlib
    result["corpus_sha256"] = hashlib.sha256(args.corpus.read_bytes()).hexdigest()
    result["prompt_sha256"] = hashlib.sha256(model.prompt_path.read_bytes()).hexdigest()
    policy_files = ("decision.py","intent.py","planner.py")
    result["policy_sha256"] = hashlib.sha256(b"".join(Path(__file__).with_name(name).read_bytes() for name in policy_files)).hexdigest()
    result["model_digest"] = model.model_digest
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k != "results"},ensure_ascii=False))
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
