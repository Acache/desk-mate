# Animation A validation (2026-11-04)

## Method

Same deterministic LVGL 8.4 simulator scenario and 10-line RGB565 buffer; baseline UI headers from commit 61230d3, compared against Animation A. Counts sum submitted rectangle pixels, including repeated pixels. No SPI, real audio, BLE load or DMA timing is simulated. These numbers measure redraw demand, not device FPS or latency. Each scene uses the same duration before/after; durations vary by scene.

| Scenario | Before pixels | After pixels | Change |
|---|---:|---:|---:|
| focus-idle | 17,340 | 0 | -100.0% |
| page-snap | 3,673,419 | 3,456,578 | -5.9% |
| media-idle | 1,449,460 | 0 | -100.0% |
| finger-drag | 8,517,321 | 6,221,378 | -27.0% |
| voice-ready | 2,607,180 | 0 | -100.0% |
| voice-cancel | 1,920,847 | 429,123 | -77.7% |
| voice-wave | 4,483,472 | 1,053,542 | -76.5% |
| feedback | 2,707,140 | 4,998 | -99.8% |
| wheel-snap | 232,804 | 286,452 | +23.0% |
| power-hold | 7,603,200 | 39,249 | -99.5% |
| power-release | 12,465,876 | 7,612,310 | -38.9% |
| ble-manager | 1,009,500 | 0 | -100.0% |

## Interpretation

The largest reductions are stable views, feedback and power-hold. Cancellation and recording waveform also submit substantially fewer pixels. Page snap improves only modestly; full-screen movement remains expensive. The wheel-snap scenario submits 23% more pixels with the same 61 flushes; no performance improvement is claimed there. Its layout/timing is unchanged. Geometry remains managed by LVGL; dirty-area merging can change when unrelated invalidations are removed. The cause and hardware impact of the wheel result are not established.

## Checks

- CTest: 12/12 passed, including LVGL 8/9 UI and new stable-page invalidation assertions.
- Actual LVGL 8 32,768-byte pool UI regression passed.
- Eight deterministic before/after screenshot pairs are pixel-identical: focus, media, voice ready, recording waveform, cancellation, power hold, power cancellation and feedback.
- Existing waveform decay, cancel state labels, gesture, input ownership, manager lifecycle, power hold/rewind/fade and feedback tests pass.
- Refresh/input periods, waveform cadence/filter, cancel deadline, animation timing/easing, QSPI clock and double 10-line buffers unchanged.
- ESP32-C6 compile passed: 2,076,026 bytes flash, 165,496 bytes static RAM (unchanged).
- Flashed to /dev/cu.usbmodem101; all hashes verified and device reset. User reports power hold, waveform and cancel bar are smooth; page 1 horizontal motion remains choppy. Feedback prompted the follow-up below.

## Device-feedback follow-up

- User requests dropping the group-opacity dismissal fade: rewind remains fully opaque and the overlay disappears at zero. In the same profile, power-release changes from 7,612,310 pixels / 1,605 flushes to 239,150 pixels / 68 flushes. This measures redraw demand, not actual FPS.
- BLE management now saves pager coordinates at entry, cancels pending underlying scroll, and restores coordinates after rebuilding/layout before the next rendered frame. A regression displaces the hidden pager and checks the restored page for 80 successive 5ms steps. The previously observed device flash awaits hardware revalidation.
- 12/12 CTest and actual 32KB LVGL8 UI checks passed again. ESP32-C6 compile: 2,076,080 bytes flash, 165,504 bytes static RAM.
- Horizontal page-1 motion remains unresolved. The 416px timer circle and 462px media circle are large software-rendered elements; LVGL circle cache has two slots and multiple radii may compete. Cache/buffer changes require measurement, not an assumed FPS gain. No changes to these settings in this follow-up.
