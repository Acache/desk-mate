# Animation A: eliminate redundant UI work

User approved trying A across all dynamic UI effects before deciding on B.

## Inventory and scope
- Horizontal finger drag/page snap: LVGL pointer period 30ms, refresh 30ms, configured snap 280ms. Keep gesture semantics, timing, easing and page lifecycle.
- Focus wheels: 110ms LVGL position animations; focus timer seconds/arc and preset selection. Preserve their update semantics.
- Voice cancellation: 840ms window, width and tenths label updated by 50ms focus tick. Preserve the actual controller deadline and visual cadence; avoid reapplying static labels/font alongside each width update.
- Recording waveform: 21 bars, history shift at least 45ms apart inside 50ms focus tick; RMS gain, attack/release smoothing, geometry and allocation lifecycle unchanged.
- Voice recording clock/stages/result, device connection labels, management/pair countdowns: change text only when rendered text changes.
- Feedback: 5s response/2s confirmation, badge expiration; avoid resetting unchanged second label.
- Global power: 20ms update, hold/rewind/fade state machine. Do not set fullscreen opacity again when unchanged. Preserve fade and foreground ordering.
- Idle dimming: separate hardware brightness path; preserve verified 10s/60s thresholds.

## Design
Use a shared small header for an equality-guarded label update without persistent text caches. Apply it to the four UI headers. Guard runtime opacity/color/font changes using existing object values. Guard repeated hidden-flag writes too: LVGL 8 clear_flag(HIDDEN) invalidates even an already-visible object. Recenter media labels only when their text changes. Leave geometry and arc setters to LVGL unchanged-value guards. Do not skip data/state updates for hidden pages or change input, audio, cancel or feedback ownership.

No refresh-period, sampling-frequency, SPI clock, DMA buffering, task priority or animation redesign changes (B/C). Add simulator flush-pixel accounting, explicitly an estimate of redraw work, not hardware FPS. Compare identical scenarios before/after; run UI regressions in LVGL 8/9 and actual 32KB pool, compile and flash for user acceptance. No Git commit until accepted.

## User device-feedback override

After testing A, the user specifically requested removing the power overlay dismissal fade. Retain rewind timing, dismiss at zero, and fix BLE manager return-page restoration. Horizontal performance is still a separate B experiment. See docs/superpowers/plans/2026-11-04-animation-followup.md.
