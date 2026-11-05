# Animation follow-up from device feedback

User confirms power hold, waveform and cancel bar are smooth. Horizontal page-1 transitions remain choppy; group-opacity fade exhibits scan-like updates. User explicitly prefers immediate dismissal once the reverse countdown reaches zero and requests fixing the BLE management return flash.

## Changes
- [x] Replace the power rewind->fade->idle sequence with rewind->idle; preserve progress timing, short-press suppression, repeated hold and hardware shutdown.
- [x] Save pager position on BLE management entry, cancel underlying scroll animation, and restore position after media reconstruction/layout before rendering another frame. Keep low-memory delete/rebuild lifecycle.
- [x] Add native power boundary and per-frame manager-return regressions; run LVGL8/9 and 32KB tests.
- [x] Compile and flash for user comparison; no Git commit until accepted.

## Remaining horizontal performance
No change to pointer/refresh intervals, DMA buffering, QSPI frequency or gesture/animation design in this follow-up. Large circles/full-screen motion remain a separate B experiment requiring heap and rendering measurements. Do not claim this fixes page-1 scroll FPS.

## Results

12/12 tests passed; LVGL8 UI regression with 32KB pool passed. Firmware compiled (2,076,080 bytes flash, 165,504 bytes static RAM) and uploaded to /dev/cu.usbmodem101, hashes verified and device reset. Await user hardware validation of the two fixes.
