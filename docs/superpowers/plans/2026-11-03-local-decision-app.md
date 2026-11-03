# Local Decision and macOS App Implementation Plan

**Goal:** Improve local semantic decisions and produce a self-contained macOS service app.

**Architecture:** Conservative rule routing and a prewarmed Ollama fallback share the existing validated planner. A native menu app supervises bundled Python and Ollama with private application data.

**Tech Stack:** Python/FastAPI, Ollama, FunASR, Swift/AppKit, macOS signing tools.

## Global Constraints

- Bilibili commands use local interfaces only.
- No Bilibili restart without an explicit UI confirmation.
- Keep current developer services and user data untouched during validation.
- Distinguish fixtures, model inference and physical-device acceptance.
- Target machines require no Python, Swift, Homebrew or separately installed Ollama.

## Tasks

- [x] 1. Add regression cases for compound, negative and literal-content commands; correct rule and personalizer routing (`intent.py`, `pipeline.py`, `test_intent.py`).
- [x] 2. Add prewarm/residency, bounded context, R2 parameter guidance and timing (`decision.py`, `api.py`, `mac/prompts/intent-v1.txt`, `test_decision.py`).
- [x] 3. Add read-only decision evaluation corpus/CLI and inspect real local inference (`evaluation.py`, `mac/eval/`, CLI, tests).
- [x] 4. Add bundle resource resolution, a managed service supervisor and native menu shell (`runtime.py`, `app_runtime.py`, `mac/app/`).
- [x] 5. Add self-contained bundle builder, resource/link audit, relocation smoke validation, build instructions (`mac/scripts/build_app.py`, packaging tests, docs).
- [x] 6. Run unit checks, model evaluation, Swift compilation and bundle smoke checks; update handoff with evidence and remaining physical-device checks.

Each task starts with behavior-focused tests when applicable; existing tests must remain passing. No automatic commits or changes to developer-installed services.

## 验收边界

自动化、真实只读推理、签名审计和独立后台生命周期已完成；系统权限、实际 UI、USB/BLE+B站及另一台干净 Mac 验收仍待用户安装后进行。详见 docs/LOCAL_DECISION_APP_VALIDATION.md。
