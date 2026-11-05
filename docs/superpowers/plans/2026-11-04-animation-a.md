# Animation A implementation plan

**Goal:** Reduce redundant rendering work across every UI animation while preserving appearance, cadence and behavior.
**Architecture:** Equality-guarded label writes plus specific runtime style guards. Compare redraw pixels in identical LVGL8 scenarios using a simulator profile mode.
**Tech Stack:** C headers, LVGL8 hardware/LVGL9 simulator, Arduino ESP32-C6.

## Tasks
- [x] Add simulator profiling scenarios and collect baseline before UI changes.
- [x] Add shared label guard; apply in Focus, BLE, voice management and power UI; guard power opacity, page-dot styles and voice font.
- [x] Compare redraw counts and check stable updates do not dirty the screen; verify waveform and cancellation behavior.
- [x] Run LVGL8/9 CTest and 32KB-pool checks, compile firmware and flash; hashes verified.
- [ ] User hardware acceptance; no Git commit yet.

## Constraints
Preserve 30ms refresh/input periods, 50ms voice tick, waveform filter, 840ms cancel deadline, all animation timings, double 10-line buffers, SPI clock, input dispatch and idle dimming. Profile counts are software redraw demand, not device FPS.

## Results

See docs/ANIMATION_A_VALIDATION.md for identical-scenario baseline/after counts, including the wheel-snap regression and the limitation that redraw counts are not device FPS. Eight screenshot pairs are pixel-identical.
