# BLE Voice Bridge Experiment Implementation Plan

**Goal:** Measure a verifiable PCM-rate stream over the existing single BLE/HID connection, keeping Wi-Fi voice usable.

**Architecture:** Optional firmware GATT laboratory, portable credit/framing model and isolated Python probe. Keep instruction execution above transports; document the future shared-session boundary.

**Tech Stack:** ESP32-C6, NimBLE-Arduino, portable C, Python asyncio/Bleak, CMake/CTest/pytest.

## Global Constraints

- 8 saved bonds, 1 connection; no second BLE server or connection owner.
- Production Wi-Fi voice retained; no application end-to-end tests.
- Lab uses synthetic data, no software actions or microphone capture.
- 32KB/s then 50KB/s, bounded 60s, receiver credit and integrity check required.
- Preserve pre-existing work; no automatic commits or global library edits.

## Tasks

- [x] Native failing tests then portable protocol/model (`ble_voice_protocol.h`, `test_ble_voice.c`, CMake target).
- [x] Lab service queued into existing BLE owner; generation checks, encrypted bond gating and credit-limited poll (`ble_voice_lab.*`, `ble_manager.cpp`, build config).
- [x] Host receiver/probe and isolated optional dependency; unit/fake-client tests (`mac/src/voice_assistant/ble_lab.py`, `mac/tests/unit/test_ble_lab.py`).
- [x] Full unit/native regressions, normal/lab compile and configuration/binary checks.
- [x] Flash validated lab and attempt actual data-only measurement; document prerequisites and measured versus unmeasured results.

## Results and remaining hardware qualification

- Host unit tests: 85 passed, including 15 data-only BLE probe tests.
- Native/UI CTest: 8/8 passed; actual 32768-byte LVGL8 and BLE protocol checks: 2/2 passed.
- Normal build: 2,057,422 bytes Flash, 152,024 static RAM; no lab service strings.
- Tuned lab: 2,062,614 bytes Flash, 152,176 static RAM; upload to /dev/cu.usbmodem101 hash verified and reset. Source-built C/C++ settings both verified 8 bonds / 1 connection / 32 CCCDs.
- Actual baseline 32,000 B/s target: 258,096 payload bytes received, no observed corruption/sequence gaps, incomplete and timed out. The diagnostic report is build/ble-lab-baseline-32k.json, NOT a successful throughput qualification.
- Actual baseline 16,000 B/s, 5s target: all 80,000 bytes acknowledged, effective 14,642.8 B/s including startup, max receive gap 143.46ms; below the strict 95% threshold. Report build/ble-lab-16k.json.
- Tuned firmware requests 15ms/2M PHY and the receiver now uses a 24-packet window; adds BLE-only connection/MTU/interval/generation diagnostics. No USB serial inspection during measurements.
- [x] Tuned same-connection suite completed: 32KB/s received 320,000 bytes at 31,598.9 B/s (PASS), 50KB/s received all 500,000 bytes at 38,619.1 B/s (throughput FAIL). Max receive gaps 82.89 / 132.37ms; MTU 247, no corruption/out-of-order/stale-run data. No client disconnect between runs.
- macOS cached the old GATT table without optional info. Probe now tolerates that additive-service cache, records unavailable firmware peer-count/generation diagnostics and verifies uninterrupted same-client lifetime. It does not assert negotiated PHY/interval.
- [x] Additional 60s 32KB/s sustained test PASS: all 1,920,000 bytes acknowledged in 60.1518s, 31,919.2 B/s, longest receive gap 152.14ms; no corruption/out-of-order/stale-run data. Report build/ble-lab-32k-60s.json. Data-path experiment completed; actual microphone/command integration remains a subsequent stage.
- No microphone, speech recognition, software actions or media-key end-to-end test was executed.
