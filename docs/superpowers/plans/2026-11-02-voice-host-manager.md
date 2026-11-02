# Voice Host Manager Implementation Plan

**Goal:** Ship the approved English voice-first page and five trusted voice-computer profiles.
**Architecture:** Portable identity-bound model and requests, NVS profile store, transport-owned connection switching, lazy LVGL manager. Keep one active credential loaded; reuse existing RMS/audio FSM.
**Tech Stack:** ESP32-C6 Arduino, XPowersLib, LVGL 8/9, Preferences, WSS, Python USB provisioning.

## Global Constraints

- Preserve existing local work, pages 1/2, top pager, global 4s PWR.
- Five saved computers, one voice target. No implicit fallback to other hosts.
- English, existing 16/20/24/28/48 fonts, real RMS waveform retained.
- Keep 530ms qualification and earliest 840ms commit, block management during unsettled commands.
- USB adds profiles; current config migrates without losing credentials. Shared Wi-Fi config.
- User explicitly excludes end-to-end tests; run model/UI/memory/compile checks only.

### Task 1: catalog, store and protocol
Files: new voice_hosts.h, voice_host_model.h, voice_hosts.cpp; provisioning.cpp/h; mac provisioning.py.
- [x] Add native identity/switch/default/removal tests, confirm fail, implement model.
- [x] Implement profile snapshots, bounded NVS persistence, legacy migration, append/update by server identity.
- [x] Add human-readable host name to USB bundle without changing schema v1.

### Task 2: transport and input isolation
Files: transport.cpp/h, voice_controller.cpp/h, input_adapter.cpp/h, voice_contracts.h.
- [x] Add transport-owned request queue, candidate/rollback switching and reconnect pause.
- [x] Clear replay on host change, bind pending/feedback to origin server.
- [x] Reserve management input and wait for release; reject busy operations.
- [x] Add bounded SoftAP mobile Wi-Fi form, validate saved settings and exit/retry.

### Task 3: English UI
Files: new voice_hosts_ui.h, focus_ui.h, desk-mate-app.ino.
- [x] Add lazy manager list/detail/confirm/add/rename/default/forget/network/diagnose.
- [x] Bind touch/physical input to immutable server identity, suppress stale releases.
- [x] Refresh voice-first layout, retain RMS wave and release countdown, target header and feedback.

### Task 4: verification and delivery
Files: simulator/tests, CMake, docs.
- [x] Run native model/audio/FSM tests, LVGL 8/9 and real 32KB pool checks.
- [x] Inspect screenshots for key states and global power overlay.
- [x] Compile ESP32-C6 firmware; no real connection or voice execution testing.
- [x] Document user manual verification and updated provisioning commands.

## Verification

- CTest 7/7 pass: identity model, actual store with in-memory NVS substitute,
  power, BLE, timer, LVGL 9 and LVGL 8 shared UI.
- LVGL 8 full UI suite passes using actual 32768-byte pool; keyboard + power
  overlay is exercised, peak observed approximately 26KB in expanded-pool run.
- Native voice FSM and PCM/ring tests pass; host Python unit suite 70/70 pass.
- Visual snapshots checked: home, recording, release, list, detail, keyboard, network.
- ESP32-C6 compile passes, approximately 98% of 2MB app partition.
- No end-to-end, live speech, real Mac switching or real network setup tests run,
  per user instruction. User verification guide: docs/VOICE_COMPUTERS.md.
- Existing unrelated local modifications preserved. No Git commit created.

Final firmware uploaded to ESP32-C6 on /dev/cu.usbmodem101; all written hashes
verified and device reset. No serial monitoring, live voice command, network
setup or multi-Mac end-to-end test was performed. Physical verification remains
with the user as requested.
