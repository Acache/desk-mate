# Global idle brightness implementation plan

**Goal:** Fade all pages to 50% after 10 seconds idle and restore immediately on input.
**Architecture:** Portable elapsed-time model; shared LVGL task controls hardware brightness, common touch and physical-button polling report activity under the existing mutex.
**Tech Stack:** C/C++, Arduino ESP32-C6, LVGL 8, SH8601.

## Constraints
10 seconds idle, 1 second fade, 50% target; preserve input actions, animations, page state and power behavior. No extra task or blocking delay.

## Tasks
- [x] Add tests/idle_brightness_test.c for timing boundaries, recovery, sustained activity and tick wrap; confirm failure before adding firmware/desk-mate-app/idle_brightness.h.
- [x] Implement independent state helper and rerun portable checks.
- [x] Integrate shared activity API and periodic hardware updates in bsp_lvgl_port.cpp/.h; report touch press/hold/release.
- [x] Report BOOT/KEY debounced press/hold/release and PWR events/hold in desk-mate-app.ino without consuming input.
- [x] Run native brightness, power and focus checks; compile firmware and review diff. Record outcomes. Firmware flashed at user request; user accepted hardware validation and authorized commit.

## Validation results

- Portable brightness checks passed, including 10-second boundary, 1-second fade, interruption, held input/release, and tick rollover.
- Simulator CTest: 12/12 passed, including LVGL 8/9 gesture/UI checks, power hold and timer regressions.
- ESP32-C6 Arduino compile passed: 2,075,576 / 2,097,152 bytes flash, 165,496 bytes static RAM. Output: build/firmware/.
- git diff --check passed. Firmware flashed to ESP32-C6 via /dev/cu.usbmodem101 on 2026-11-04; all written hashes verified and device reset. User confirmed hardware validation OK and authorized Git commit on 2026-11-04.
