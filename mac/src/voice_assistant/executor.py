"""The single serialized action executor.

Every external effect is preceded by a durable STARTED journal row and followed
by a verified result. Nothing is retried automatically; an unverified effect is
reported as OUTCOME_UNKNOWN and stops later steps.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .adapters.base import (
    AdapterError,
    AdapterRegistry,
    InvocationReceipt,
    PreparedAction,
    ResolvedAction,
)
from . import contracts
from .contracts import (
    ActionResult,
    CommandState,
    ErrorCode,
    REPLAY_ACTIONS,
    StepState,
    TrustedContext,
    ValidatedAction,
    ValidatedPlan,
    canonical_json,
    parse_reference,
)
from .storage import Database

GLOBAL_STOP_CODES = {
    ErrorCode.AUTH_FAILED,
    ErrorCode.PERMISSION_DENIED,
    ErrorCode.CAPABILITY_UNAVAILABLE,
    ErrorCode.POLICY_DENIED if hasattr(ErrorCode, "POLICY_DENIED") else ErrorCode.INVALID_PLAN,
    ErrorCode.USER_CANCELED,
    ErrorCode.MAC_LOCKED,
    ErrorCode.MAC_OFFLINE,
}

ProgressCallback = Callable[[str, int | None], None]


@dataclass
class ExecutionOutcome:
    state: CommandState
    code: ErrorCode | None
    summary: str
    results: list[ActionResult]
    replayable: bool


class Executor:
    def __init__(self, db: Database, registry: AdapterRegistry, clock: Callable[[], float] = time.time):
        self.db = db
        self.registry = registry
        self.clock = clock

    def _resolve_params(self, action: ValidatedAction, prior: dict[int, ActionResult]) -> dict[str, Any]:
        params = dict(action.params)
        for key, value in list(params.items()):
            if (action.action, key) in contracts.REFERENCE_FIELDS and isinstance(value, str) and value.startswith("$"):
                # Only reference-capable fields reach this point; planner has
                # already verified the field/action pair and dependency shape.
                ref = parse_reference(value)
                if ref is None:
                    raise AdapterError(ErrorCode.INVALID_PLAN, f"invalid reference in {key}")
                source, field = ref
                if source == -1:
                    raise AdapterError(ErrorCode.INVALID_PLAN, "$prev requires a preceding step")
                result = prior.get(source)
                if not result or result.status not in (StepState.SUCCEEDED, StepState.NOOP_VERIFIED):
                    raise AdapterError(ErrorCode.TARGET_STALE, "referenced step did not succeed")
                if field not in result.output:
                    raise AdapterError(ErrorCode.TARGET_STALE, "referenced output is missing")
                resolved = result.output[field]
                expected = {
                    "search_result_id": str, "video_id": str,
                }.get(field)
                if expected and not isinstance(resolved, expected):
                    raise AdapterError(ErrorCode.INVALID_PLAN, "referenced output has the wrong type")
                params[key] = resolved
        return params

    def _binding_target(self, action: ValidatedAction, prior: dict[int, ActionResult]) -> dict[str, Any]:
        binding = dict(action.binding)
        kind = binding.get("kind")
        if kind == "play_output":
            src = int(binding["source_step"])
            result = prior.get(src)
            if not result or result.status not in (StepState.SUCCEEDED, StepState.NOOP_VERIFIED):
                raise AdapterError(ErrorCode.TARGET_STALE, "play dependency did not succeed")
            binding["video_id"] = result.output.get("video_id")
            binding["generation"] = result.output.get("player_generation")
            if not binding["video_id"]:
                raise AdapterError(ErrorCode.TARGET_STALE, "play output has no video id")
        elif kind == "search_step":
            src = int(binding["source_step"])
            result = prior.get(src)
            if not result or result.status not in (StepState.SUCCEEDED, StepState.NOOP_VERIFIED):
                raise AdapterError(ErrorCode.TARGET_STALE, "search dependency did not succeed")
            binding["search_result_id"] = result.output.get("search_result_id")
            if not binding["search_result_id"]:
                raise AdapterError(ErrorCode.TARGET_STALE, "search output has no snapshot id")
        elif kind == "player_snapshot":
            player = self.db.current_player(self.clock())
            if not player:
                raise AdapterError(ErrorCode.TARGET_STALE, "player state is no longer available")
            if binding.get("video_id") and player["video_id"] != binding["video_id"]:
                raise AdapterError(ErrorCode.TARGET_STALE, "current video changed")
            if binding.get("generation") and player["generation"] != binding["generation"]:
                raise AdapterError(ErrorCode.TARGET_STALE, "player generation changed")
        elif kind == "search_snapshot":
            snap = self.db.latest_snapshot("search", self.clock())
            if not snap or snap["id"] != binding.get("search_result_id"):
                raise AdapterError(ErrorCode.TARGET_STALE, "search snapshot expired or changed")
        return binding

    def _dependencies_ready(self, action: ValidatedAction, prior: dict[int, ActionResult]) -> tuple[bool, ErrorCode | None]:
        for dep in action.after:
            result = prior.get(dep)
            if not result:
                return False, ErrorCode.TARGET_STALE
            if result.status not in (StepState.SUCCEEDED, StepState.NOOP_VERIFIED):
                return False, ErrorCode.TARGET_STALE
        return True, None

    def execute(
        self,
        plan: ValidatedPlan,
        command_id: str,
        context: TrustedContext,
        deadline: float,
        progress: ProgressCallback | None = None,
    ) -> ExecutionOutcome:
        started_at = self.clock()
        prior: dict[int, ActionResult] = {}
        outcomes: list[ActionResult] = []
        unknown = False
        stop = False
        canceled = False
        last_code: ErrorCode | None = None
        for step_index, action in enumerate(plan.actions):
            if stop:
                row = ActionResult(step_index=step_index, status=StepState.SKIPPED,
                                   code=ErrorCode.INTERRUPTED, latency_ms=0)
                self.db.finish_step(command_id, step_index, StepState.SKIPPED, code=ErrorCode.INTERRUPTED)
                outcomes.append(row)
                prior[step_index] = row
                continue
            if self.clock() > deadline or self.clock() > plan.expires_at:
                stop = True
                last_code = ErrorCode.ACTION_FAILED
                row = ActionResult(step_index=step_index, status=StepState.SKIPPED,
                                   code=ErrorCode.ACTION_FAILED, latency_ms=0)
                self.db.finish_step(command_id, step_index, StepState.SKIPPED, code=ErrorCode.ACTION_FAILED)
                outcomes.append(row)
                prior[step_index] = row
                continue
            state = self.db.get_state(command_id)
            if state is CommandState.CANCELED:
                canceled = True
                stop = True
                continue
            if state in (CommandState.OUTCOME_UNKNOWN,):
                stop = True
                continue
            if progress:
                progress("executing", step_index)
            deps_ok, dep_code = self._dependencies_ready(action, prior)
            if not deps_ok:
                row = ActionResult(step_index=step_index, status=StepState.SKIPPED,
                                   code=dep_code, latency_ms=0)
                self.db.finish_step(command_id, step_index, StepState.SKIPPED, code=dep_code)
                outcomes.append(row)
                prior[step_index] = row
                last_code = dep_code
                continue
            try:
                params = self._resolve_params(action, prior)
                binding = self._binding_target(action, prior)
                resolved = ResolvedAction(step_index, action.action, params, binding)
                adapter = self.registry.adapter_for(action.action)
                prepared = adapter.prepare(resolved, context)
            except AdapterError as exc:
                row = ActionResult(step_index=step_index, status=StepState.FAILED,
                                   code=exc.code, latency_ms=0)
                self.db.finish_step(command_id, step_index, StepState.FAILED, code=exc.code)
                outcomes.append(row)
                prior[step_index] = row
                last_code = exc.code
                if exc.code in GLOBAL_STOP_CODES:
                    stop = True
                continue
            except Exception as exc:  # defensive: prepared action must not leak
                row = ActionResult(step_index=step_index, status=StepState.FAILED,
                                   code=ErrorCode.INVALID_PLAN, latency_ms=0)
                self.db.finish_step(command_id, step_index, StepState.FAILED, code=ErrorCode.INVALID_PLAN)
                outcomes.append(row)
                prior[step_index] = row
                last_code = ErrorCode.INVALID_PLAN
                continue
            # Durable intent before any external call.
            if not self.db.start_step(command_id, step_index):
                row = ActionResult(step_index=step_index, status=StepState.SKIPPED,
                                   code=ErrorCode.INTERRUPTED, latency_ms=0)
                self.db.finish_step(command_id, step_index, StepState.SKIPPED, code=ErrorCode.INTERRUPTED)
                outcomes.append(row)
                prior[step_index] = row
                last_code = ErrorCode.INTERRUPTED
                stop = True
                continue
            # Opening a video may need a cold network/player load before its
            # clock advances. Other controls retain their short action budget.
            step_budget = 25.0 if action.action == "bilibili.play" else 5.0
            step_deadline = min(deadline, plan.expires_at, self.clock() + step_budget)
            try:
                receipt = adapter.invoke(prepared, step_deadline)
            except AdapterError as exc:
                if exc.effect_possible:
                    unknown = True
                    stop = True
                    row = ActionResult(step_index=step_index, status=StepState.OUTCOME_UNKNOWN,
                                       code=ErrorCode.OUTCOME_UNKNOWN, latency_ms=0)
                    self.db.finish_step(command_id, step_index, StepState.OUTCOME_UNKNOWN,
                                        code=ErrorCode.OUTCOME_UNKNOWN)
                else:
                    row = ActionResult(step_index=step_index, status=StepState.FAILED,
                                       code=exc.code, latency_ms=0)
                    self.db.finish_step(command_id, step_index, StepState.FAILED, code=exc.code)
                    if exc.code in GLOBAL_STOP_CODES:
                        stop = True
                outcomes.append(row)
                prior[step_index] = row
                last_code = row.code
                continue
            except Exception:
                unknown = True
                stop = True
                row = ActionResult(step_index=step_index, status=StepState.OUTCOME_UNKNOWN,
                                   code=ErrorCode.OUTCOME_UNKNOWN, latency_ms=0)
                self.db.finish_step(command_id, step_index, StepState.OUTCOME_UNKNOWN,
                                    code=ErrorCode.OUTCOME_UNKNOWN)
                outcomes.append(row)
                prior[step_index] = row
                last_code = ErrorCode.OUTCOME_UNKNOWN
                continue
            try:
                result = adapter.verify(receipt, step_deadline)
            except Exception:
                result = ActionResult(step_index=step_index, status=StepState.OUTCOME_UNKNOWN,
                                      code=ErrorCode.OUTCOME_UNKNOWN, latency_ms=0)
            if result.status is StepState.FAILED and receipt.effect_possible:
                # The call reached the target but we could not prove its state.
                result = ActionResult(step_index=step_index, status=StepState.OUTCOME_UNKNOWN,
                                      code=ErrorCode.OUTCOME_UNKNOWN,
                                      output=result.output, evidence=result.evidence,
                                      latency_ms=result.latency_ms)
                unknown = True
                stop = True
            if result.status is StepState.OUTCOME_UNKNOWN:
                unknown = True
                stop = True
            self.db.finish_step(
                command_id, step_index, result.status,
                output=result.output, evidence=result.evidence, code=result.code,
            )
            outcomes.append(result)
            prior[step_index] = result
            last_code = result.code
            if result.code in GLOBAL_STOP_CODES:
                stop = True
            if result.status is StepState.FAILED and result.code in (ErrorCode.PERMISSION_DENIED,
                                                                     ErrorCode.MAC_LOCKED,
                                                                     ErrorCode.MAC_OFFLINE):
                stop = True
        # Aggregate.
        successes = [r for r in outcomes if r.status in (StepState.SUCCEEDED, StepState.NOOP_VERIFIED)]
        failures = [r for r in outcomes if r.status in (StepState.FAILED, StepState.SKIPPED)]
        if canceled and not successes:
            state, code, summary = CommandState.CANCELED, ErrorCode.USER_CANCELED, "已取消"
        elif unknown:
            state, code, summary = CommandState.OUTCOME_UNKNOWN, ErrorCode.OUTCOME_UNKNOWN, "执行结果未知，请检查 Mac"
        elif failures and successes:
            state, code, summary = CommandState.PARTIAL, failures[0].code or ErrorCode.ACTION_FAILED, "部分完成"
        elif failures and not successes:
            state, code, summary = CommandState.FAILED, failures[0].code or ErrorCode.ACTION_FAILED, "未完成"
        elif successes and len(successes) == len(outcomes):
            ui_summary = next((str(r.output.get("ui_summary")) for r in successes
                               if isinstance(r.output, dict) and r.output.get("ui_summary")), "")
            state, code, summary = CommandState.SUCCEEDED, None, ui_summary or "完成"
        else:
            state, code, summary = CommandState.FAILED, last_code or ErrorCode.ACTION_FAILED, "未完成"
        replayable = (
            state is CommandState.SUCCEEDED
            and len(plan.actions) == 1
            and plan.actions[0].action in REPLAY_ACTIONS
        )
        with self.db.transaction() as tx:
            tx.execute(
                "UPDATE commands SET total_latency_ms=? WHERE id=?",
                (max(0, int((self.clock() - started_at) * 1000)), command_id),
            )
        self.db.finalize(command_id, state, code, summary, replayable)
        return ExecutionOutcome(state, code, summary, outcomes, replayable)
