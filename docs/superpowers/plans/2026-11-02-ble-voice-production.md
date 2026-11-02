# BLE Voice R1 Implementation Plan

**Goal:** Make uncompressed voice usable over the same single BLE connection as HID, retaining selectable WSS.

**Architecture:** Bounded GATT message adapter owned by the existing BLE manager; transport-independent command session on Mac; firmware routes existing protocol through explicit BLE/Wi-Fi mode and mutual credential proofs.

**Tech Stack:** ESP32-C6/NimBLE, LVGL8/9, ArduinoJson/mbedTLS, Python asyncio/Bleak/FastAPI/SQLite.

## Global Constraints

- Eight saved BLE bonds, one physical connection, same selected peer for pages2/3.
- No compression; preserve 16kHz s16 mono and streaming/cancel/commit semantics.
- Wi-Fi explicit fallback; no implicit replay/target failover.
- Preserve existing credentials/bonds/settings and all prior workspace changes.
- Real speech/application acceptance is user-only; no automatic commits.

## Tasks

- [x] Extract shared authenticated session dispatcher from WSS; fix and test device ownership across status/cancel/commit.
- [x] Portable C/Python framing, credit and mutual-auth tests and implementations.
- [x] Production BLE service + manager ownership/lifecycle integration, preserving HID and persisted peer binding.
- [x] Firmware explicit transport mode and shared protocol routing, credential handshake, pending recovery and input/target guards.
- [x] Mac BLE connector in assistant lifecycle/CLI, shared session/authentication and revocation checks; fake transport/session tests.
- [x] Mode/capability UI, simulator/32KB regression, docs and user verification guide.
- [x] Full host/native checks, firmware compile and synthetic protocol validation, flash for user speech acceptance.

## Verification record

- 103 Mac unit tests pass (synthetic framing/authentication/credit/overflow/ownership/staging; no real STT or application actions).
- Native golden wire vectors, original voice FSM timing and audio conversion/ring checks pass.
- CTest 9/9, plus LVGL8 actual 32768-byte pool checks 2/2 pass. Network UI rendered and inspected.
- Normal production image: 2,072,860 / 2,097,152 bytes flash; globals 164,744 bytes.
- Firmware flashed with upload hashes verified. Boot: 81,200 bytes free heap, 5 existing bonds preserved, auto-reconnect remains off.
- Mac BLE dependency installed; runtime bridge started. Physical mutual authentication and READY/heartbeat verification passed for >2minutes, MTU247, generation1 and one physical peer. Runtime free heap ~76KB, minimum heap ~65KB, voice stack margin4940B. Captured and fixed a voice-task stack-protection panic by moving nested2KB buffers into single-owner persistent scratch. Fixed per-central CoreBluetooth state filtering, handle-vs-index lookup, one service-change indication/generation, and late ATT MTU negotiation.
