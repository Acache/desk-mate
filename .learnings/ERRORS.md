# Errors

## [ERR-20261103-CUA] native app inspection stalled desktop chat

**Logged**: 2026-11-03
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
CUA getApp on the newly built menu-bar app remained pending for about 405 seconds. The user reported losing chat input/stop controls and had to restart the desktop client. The app had compiled and its isolated service was healthy; this is not evidence of an application menu deadlock.

### Suggested Fix
Avoid retrying the affected UI integration in this session. Use bounded CLI checks, native --self-check, private health/state files and explicit process cleanup; leave visual interaction and permission acceptance as unverified.

### Metadata
- Reproducible: unknown
- Source: user_feedback
- Related Files: mac/app/DeskMate.swift, mac/src/voice_assistant/app_runtime.py

---

## [ERR-20261104-DISPLAY] display experiment and C/C++ UI checks

**Logged**: 2026-11-04
**Status**: resolved
**Area**: frontend

- Increasing LVGL circle-cache slots from 2 to 4 exhausted the actual 32KB pool in circ_calc_aa4. Rejected that variant; retained two slots.
- The C simulator accepted BleSnapshot initialization with {0}, while Arduino C++ rejected conversion of integer zero to its enum field. Use explicit memset for the shared C/C++ snapshot, and require the firmware compile in addition to native tests.
- An added peer-name assertion initially used a stale simulator peer with bt_read unset. Bind the fake source explicitly before asserting current-device rendering; the revised regression now passes.
