# Global PWR Hold Implementation Plan

**Goal:** 4s hardware shutdown with global hold/cancel feedback.
**Architecture:** portable state model + PMIC IRQ snapshot + lazy top-layer LVGL card.
**Tech Stack:** C/C++, XPowersLib, Arduino ESP32-C6, LVGL 8/9, CMake simulator.

## Global Constraints

4s total hold; popup after 1s; displayed progress 0.0–3.0; reverse <=2s; fade 1s.
Retain page taps and current cold power-on. Preserve existing local modifications.

### Task 1: model
- [x] Add tests/power_hold_test.c for timing, cancellation, restart, tap suppression, rollover.
- [x] Add firmware/desk-mate-app/power_hold.h with explicit idle/hold/rewind/fade states.
- [x] Compile and run standalone tests, register with simulator CTest.

### Task 2: PMIC and routing
- [x] Configure 4s hardware power-off; enable press/release IRQs.
- [x] Replace short-only polling with an acknowledged snapshot (avoid lost-edge clearing).
- [x] Feed events through model/overlay and route only allowed short taps.

### Task 3: overlay and verification
- [x] Add power_ui.h, lazy card on top layer and 20ms refresh.
- [x] Add shared simulator assertions and hold/cancel screenshot scenarios.
- [x] Build LVGL 8/9 and hardware memory suite, inspect screenshots.
- [x] Compile firmware, update button documentation, record physical QA limits.

## Validation results

- Model timing/cancel/re-press/delayed IRQ/rollover tests pass.
- LVGL 9 + LVGL 8 suite: 5/5 pass.
- Actual 32KB LVGL 8 pool suite: 2/2 pass (including BLE keyboard + power overlay).
- Long-press and cancel snapshots visually checked; previews in build/previews/.
- ESP32-C6 Arduino compile succeeds; firmware in build/firmware/.
- git diff --check passes. Existing unrelated local edits preserved.
- Not flashed: physical PEK edge timing, 4s power cut, USB/battery behavior and
  cold power-on still require device verification.

## Integer display refinement

48px integer seconds (0s–3s), with the arc still updated every 20ms.
Tests additionally verify arc movement while the integer label stays unchanged.
LVGL suite 5/5 and 32KB pool suite 2/2 passed again; firmware compiled.

Uploaded integer-display firmware successfully to ESP32-C6 at /dev/cu.usbmodem101;
flash hashes verified and device reset. Serial shows live STATUS page=0 and ble=1;
serial monitor closed. Physical hold/cancel/power-cut verification is left to user.
