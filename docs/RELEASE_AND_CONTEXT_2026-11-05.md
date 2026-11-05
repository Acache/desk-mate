# Release identity, permission detection and context-first execution

## Canonical current behavior
User reaffirmed: exact current context + an explicit simple command executes directly; composite/unfamiliar requests use the current context with the model, followed by host validation. Search is optional, not a prerequisite for selecting a home/player video.

## Source map
- `spec/MASTER_SPEC.md`, `docs/ISSUES_2026-11-04.md`: historical requirements and observed issues; this note clarifies current behavior where older search-only assumptions conflict.
- `adapters/cdp_player.py`: visible card/episode/recommendation collection; home cards are first-class context.
- `adapters/bilibili.py`: select focused/front Bilibili page, not an old search window just because it has results. `ax_helper.swift` window-metadata query does not require AX and does not activate/reorder windows.
- `context_rules.py`, `intent.py`: exact selectors and complete simple grammar; ambiguous titles clarify, composite requests reach the model.
- `pipeline.py`, `decision.py`, `mac/prompts/intent-v1.txt`: preserve current page across refresh, send home/search/player data to the model, do not reject unfamiliar language merely for lacking a keyword.
- `context_rules.guard_visible_targets`, `planner.py`, `executor.py`: bind model ordinals to current stable IDs, reject fabricated IDs, preserve contracts/policy/confirmation/cancellation/effect checks.

## Stable local publication
Canonical app: `build/usability-release/Desk-Mate.app`. Version: `mac/app/version.json`, monotonically increasing bundle build. Update with `python3 mac/scripts/update_local_app.py`; build/sign staging as data only, quit own native instances gracefully, replace original app, then run native checks at the canonical path. Never execute staging. Application Support identity, credentials, database and models are retained. Root `AGENTS.md` records this convention for future agents.

Regular activation policy and LSUIElement=false restore Dock/Command-Tab. Show main window immediately and on reopen; close-window need not stop the background service, Quit does.

## Permission truth and test ergonomics
Permission status is observed by the running main App using `AXIsProcessTrusted()` and `CGPreflightScreenCaptureAccess()`. Explicit Recheck uses `AXIsProcessTrustedWithOptions(prompt:true)` and `CGRequestScreenCaptureAccess()` in that same process, matching the earlier native refresh implementation. Return from Settings refreshes preflight results. The UI and `permission-status.json` share that exact observation and record bundle path/name/PID/version. The previous short-lived CLI probe was incorrect because it has a different TCC responsibility context; it is now disabled. Rechecking never resets TCC or restarts STT/Ollama.

No Apple-issued signing identity is installed. User chose to improve local testing without one. Local updates now use a persistent private self-signed certificate, with the ordinary certificate-bound designated requirement verified across real builds. Fixed paths and main-process observations remove the staging/CLI attribution mistakes; the one-time, user-approved reassociation from the old ad-hoc authorization has now been completed. No identifier-only requirement or permission bypass is introduced. Apple's [code-signing tasks](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Procedures/Procedures.html) describes self-signed development identities; [TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements) explains the role of the designated requirement in retaining app identity across updates.

Apple reference: https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements

## Validation
Full unit+TLS/protocol baseline: 202 passed. Final unit+websocket suite: 203 passed; subsequent focused context/intent/planner/websocket regressions: 64 passed. Swift optimized compile, notification self-check, package signing and metadata checks passed. Native UI confirmed main window visible with service/STT/model ready. Permission recheck left service/Ollama PIDs unchanged and did not navigate to Settings. Live read-only CDP check found 12 visible home cards (and an existing player page), confirming home collection against the installed client without playing anything. End-to-end synthetic websocket tests cover exact home ordinal with no search/model and unfamiliar language with current home context retained through refresh.

## Earlier real-device failures (resolved in follow-up)

At the start of the follow-up repair, actual System Settings inspection showed only `.Desk-Mate-staging.app` enabled for both Accessibility and Screen Capture, while the canonical main App reported both false. Running the staging binary in earlier release self-checks had registered the wrong client name. LaunchServices cleanup cannot transfer a privacy grant to another code identity. Do not equate the old checked row with current-process authorization or change the UI to claim access it does not have.

During the release health check the saved peer also showed BLE_AUTH_TIMEOUT. A hot reconnect kept model/service PIDs unchanged but had not authenticated within the sampled window. Synthetic execution and live read-only target binding passed; real voice execution is not claimed as accepted.

## Earlier published result (superseded)

Published 0.3.0 (build 6) in the canonical original path; date-stamped app removed. Seven modified runtime modules match published resources byte-for-byte. Source and packaged Info.plist use regular activation (LSUIElement=false); actual native UI verified default main window and service/model readiness. Final health sample: service ready, STT available, decision loaded; BLE resolving, not authenticated yet. No real voice execution acceptance claimed.

## Follow-up repair: 0.3.1 builds 9–10

The user's real failed first-video command (`d84018a7-4a06-40e8-885c-2c9a867c96eb`) already had the correct home-card BV plan but failed with `CAPABILITY_UNAVAILABLE`. The remaining execution gates incorrectly required AX permission and/or an existing player. `control_available()` now recognizes the actual home/search page; CDP selects the observed visible card on the bound page. Only play gains this independent capability; unrelated AX actions are not falsely advertised.

The full real-recording check then exposed two more success-path issues. The executor's blanket 5-second budget expired during cold media loading; play now has a 25-second maximum within the command/plan deadlines. A confirmed playback result also carried floating-point seconds in evidence, while the storage canonical serializer prohibits floats. Persisted evidence now uses integer milliseconds. A real Adapter → Executor → SQLite regression covers snapshots, step completion and final command success. Unexpected pipeline exceptions are now logged with their stack trace rather than silently losing the cause.

`mac/scripts/verify_recording_play.py` reuses the active saved WAV, the published build's real SenseVoice STT, current page collector, deterministic ordinal planner, production executor and real Bilibili adapter. No fake adapter is used; the exact command must not invoke an LLM. It runs an isolated protocol/database session, leaving production credentials/database unchanged. This does not claim a new hardware microphone/BLE recording test.

Actual build-9 acceptance: transcription `播放第一个视频。`, page `home`, first visible target `BV1jVJk6AEmn`, observed playback advanced from 906659 to 906871 ms. Both durable step state and protocol terminal result were `SUCCEEDED`, with `DONE`. Evidence: `build/real-first-video-acceptance.json`. Before that, read-only sampling independently observed the same player's time advancing. The full pre-final suite passed 224 tests; the final adapter/context/executor/protocol run passed 34 tests, and the broader targeted playback regressions passed 50.

Updater now defaults to a persistent local certificate in the private `~/Library/Application Support/desk-mate/signing` directory. It changes neither trust settings nor the keychain search list, and locks the dedicated keychain after signing. The real published build 8 and build 9 have different CDHashes but the exact same normal certificate-based designated requirement, verified against the same certificate and bundle ID (`build/local-signature-cross-build.json`). No weakened custom requirement is used. Local self-signing is for this Mac's testing, separate from Developer ID distribution.

After explicit user approval, only the two old staging grants were removed and the canonical `Desk-Mate.app` was selected in System Settings for Accessibility (displayed as “设备控制和数据访问” on this Mac) and Screen Capture (“录屏与系统录音”, not the separate audio-only section). Both grants were completed before a single App restart. The main window then showed both authorized, and native preflight values remained true across four fresh timer samples; backend AX capabilities also became available. An explicit recheck preserved the display. Evidence: `build/permission-reassociation-acceptance.json`.

System Settings initially retained the old display label in its process cache even after selecting the correct canonical file. Actually quitting and reopening System Settings refreshed both rows to `Desk-Mate = on`; no additional grant/reset was needed.

A subsequent in-place update to build 10 reused the same certificate and required no new authorization. Its new main process again reported both native preflight values true over four samples/10.5 seconds. Service/STT/model readiness and backend AX capabilities all passed, with stable process IDs during sampling. Evidence: `build/permission-cross-build-acceptance.json`. The published build-10 runtime matches all 36 source Python files byte-for-byte, preserving the already accepted real-recording playback fix.


## Current baseline: 0.3.2 build 14

The permission and real-recording playback repairs above remain included. USB control-line reset, live credential refresh and legacy peer binding migration are now fixed and accepted on one board/this Mac; normal reconnect preserves credentials and service/model processes. The canonical build retains both real native permissions, and its 37 Python modules / 3 SQL migrations match current source. See [USB acceptance and remaining hardware coverage](USB_AUTHORIZATION_VALIDATION_2026-11-05.md). Earlier timeout and permission-failure observations above are retained as diagnosis history, not the current delivery state.
