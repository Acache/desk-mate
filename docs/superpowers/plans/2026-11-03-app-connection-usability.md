# App connection usability implementation plan

User approved the design: one Bilibili launch/control entry; switch ESP32 inputs without restarting models or the service; actionable progress and errors.

## Design

The backend owns a replaceable BLE connector. A user-only Unix socket accepts narrowly scoped select/disconnect commands from the bundled CLI. Selection is refused while a voice command is active, and the previous connector must fully stop before a new one starts. Successful selections are persisted by the backend; CLI settings remain possible while the service is stopped. Acceptance is separate from authentication success.

Bilibili state distinguishes absent installation, not running, running without CDP, CDP ready without a video, and player ready. Cold launch needs no restart approval. An already running client is never restarted without an explicit confirmation. Errors return stable codes, short Chinese explanations and recovery steps.

The native menu displays translated connection phases, current device, in-progress operations and meaningful failures. Background retries generate one alert per changed failure; dismissing an alert must not produce repeated identical dialogs. CLI commands and IPC have bounded waits.

## Tasks

- [x] Add tests for switching, active-command rejection, disconnect cleanup and error mapping; implement backend BLE controller and private IPC.
- [x] Add tests for cold Bilibili launch, running-client confirmation, no-player CDP readiness and failures; implement structured control results.
- [x] Connect native menu selection/connection actions to private IPC, improve status/progress/errors, and compile menu self-check.
- [x] Validate private IPC and unchanged service/model PIDs through bundle smoke tests without Bluetooth hardware or player mutations.
- [x] Rebuild/sign/archive an updated development app and update handoff and validation boundaries.

Physical Bluetooth pairing, Bilibili cold launch/restart and native UI interactions remain distinct manual acceptance items. Do not repeat the desktop UI automation call that hung the host application in this session.

## Evidence

172 unit tests passed; bundled private IPC preserved service/Ollama/STT process identities. Physical two-board handover, actual Bilibili launch/restart and native interaction remain manual acceptance items. No UI automation was retried.
