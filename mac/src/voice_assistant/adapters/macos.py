"""macOS system actions (R2). R1 never enables these capabilities."""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import AppConfig, utc_now
from ..contracts import ActionResult, ErrorCode, StepState
from .base import (
    ActionAdapter,
    AdapterError,
    CapabilitySet,
    InvocationReceipt,
    PreparedAction,
    ResolvedAction,
)


class MacSystemAdapter(ActionAdapter):
    name = "macos-system"

    def __init__(self, cfg: AppConfig, db: Any = None):
        self.cfg = cfg
        self.db = db

    def probe(self) -> CapabilitySet:
        caps = CapabilitySet()
        caps.available |= {"system.open_app", "system.screenshot"}
        # Volume/mute use Standard Additions; still verify at invoke time.
        caps.available |= {"system.vol_up", "system.vol_down", "system.volume", "system.mute"}
        return caps

    def _app_path(self, alias: str) -> str | None:
        bundle = self.cfg.app_allowlist.get(alias)
        if not bundle:
            return None
        try:
            out = subprocess.run(
                ["mdfind", f"kMDItemCFBundleIdentifier == '{bundle}'"],
                capture_output=True, text=True, timeout=5,
            )
            paths = [p for p in out.stdout.splitlines() if p.endswith(".app")]
            return paths[0] if paths else None
        except Exception:
            return None

    def prepare(self, resolved: ResolvedAction, context: Any) -> PreparedAction:
        action = resolved.action
        if action == "system.open_app":
            alias = resolved.params.get("app_id")
            path = self._app_path(alias)
            if not path:
                raise AdapterError(ErrorCode.CAPABILITY_UNAVAILABLE, "app alias is not installed")
            return PreparedAction(resolved, {"path": path}, time.time())
        if action in {"system.vol_up", "system.vol_down"}:
            return PreparedAction(resolved, {"delta": int(resolved.params.get("delta", 5))}, time.time())
        if action == "system.volume":
            return PreparedAction(resolved, {"target": max(0, min(100, int(resolved.params.get("target", 50))))}, time.time())
        if action == "system.mute":
            return PreparedAction(resolved, {}, time.time())
        if action == "system.screenshot":
            return PreparedAction(resolved, {}, time.time())
        raise AdapterError(ErrorCode.CAPABILITY_UNAVAILABLE, "unsupported system action")

    def invoke(self, prepared: PreparedAction, deadline: float) -> InvocationReceipt:
        started = time.time()
        action = prepared.resolved.action
        p = prepared.prepared or {}
        try:
            if action == "system.open_app":
                subprocess.run(["open", p["path"]], check=True, timeout=10)
            elif action == "system.volume":
                subprocess.run(["osascript", "-e", f"set volume output volume {p['target']}"],
                               check=True, timeout=10, capture_output=True, text=True)
                out = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                                     capture_output=True, text=True, timeout=5)
                p["observed"] = int(out.stdout.strip()) if out.stdout.strip().isdigit() else None
                if p["observed"] is not None and abs(p["observed"] - p["target"]) > 2:
                    return InvocationReceipt(prepared, started, time.time(), False,
                                             {"error": "volume_not_applied", **p})
            elif action in {"system.vol_up", "system.vol_down", "system.mute"}:
                script = self._volume_script(action, p.get("delta", 5))
                subprocess.run(["osascript", "-e", script], check=True, timeout=10,
                               capture_output=True, text=True)
            elif action == "system.screenshot":
                dest = self.cfg.screenshots_dir / f"{uuid.uuid4()}.png"
                subprocess.run(["screencapture", "-x", str(dest)], check=True, timeout=15)
                p["path"] = str(dest)
                p["size"] = dest.stat().st_size if dest.exists() else 0
            return InvocationReceipt(prepared, started, time.time(), True, p)
        except Exception as exc:
            return InvocationReceipt(prepared, started, time.time(), False, {"error": str(exc)[:200]})

    @staticmethod
    def _volume_script(action: str, delta: int) -> str:
        if action == "system.mute":
            return 'set volume output muted true'
        op = "+" if action == "system.vol_up" else "-"
        return (
            "set cur to output volume of (get volume settings)\n"
            f"set next to cur {op} {int(delta)}\n"
            "if next > 100 then set next to 100\n"
            "if next < 0 then set next to 0\n"
            "set volume output volume next"
        )

    def verify(self, receipt: InvocationReceipt, deadline: float) -> ActionResult:
        step = receipt.prepared.resolved.step_index
        action = receipt.prepared.resolved.action
        latency = max(0, int((receipt.finished_at - receipt.started_at) * 1000))
        if not receipt.effect_possible:
            return ActionResult(step_index=step, status=StepState.FAILED,
                                code=ErrorCode.ACTION_FAILED,
                                evidence={"error": str((receipt.raw or {}).get("error", ""))[:120]},
                                latency_ms=latency)
        if action == "system.screenshot":
            path = Path((receipt.prepared.prepared or {}).get("path", ""))
            if not path.is_file() or path.stat().st_size == 0:
                return ActionResult(step_index=step, status=StepState.FAILED,
                                    code=ErrorCode.ACTION_FAILED, latency_ms=latency)
            return ActionResult(step_index=step, status=StepState.SUCCEEDED,
                                output={}, evidence={"size": path.stat().st_size}, latency_ms=latency)
        if action == "system.open_app":
            return ActionResult(step_index=step, status=StepState.SUCCEEDED,
                                output={"app_id": receipt.prepared.resolved.params.get("app_id")},
                                evidence={"launched": True}, latency_ms=latency)
        return ActionResult(step_index=step, status=StepState.SUCCEEDED,
                            evidence={"volume_set": True}, latency_ms=latency)
