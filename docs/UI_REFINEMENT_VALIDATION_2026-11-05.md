# UI refinement validation — 2026-11-05

## Delivered behavior
- Media's large duplicate Focus ring is removed; running Focus time is compact text. Media/Voice device and transport status have no clickable frame/action.
- Drawn flat down/up chevrons replace connection entry text, Close button and swipe instruction. Opaque drawer tracks vertical finger displacement; release beyond halfway commits, earlier release reverses. A reversed opening drag cancels. Final settling uses a 160ms ease-out with no opacity blending.
- Desktop uses persistent dark Connection, Service/Voice and Permissions cards. Advanced actions and diagnostics expand inline; refresh updates existing fields. Destructive confirmations and explicit selection dialogs remain interactive dialogs; automatic result/error dialogs are removed.
- Startup waiting for an offline saved peer stays quiet. An observed connection loss produces one system notification; automatic retries stay quiet. Explicit-attempt failures and service errors are deduplicated. Notification permission denial does not fall back to a modal dialog. Successful manual operations update the status area.
- BLE timeout codes distinguish discovery, connection, channel setup and authentication. Diagnostic details remain available without exposing technical IDs as routine main-window content.

## Checks
- LVGL8/9 CTest: 12/12 passed, including partial/full/reversed opening and partial/full closing drags, status non-interactivity and media timer layout.
- Actual LVGL8 32KB pool UI regression passed.
- ESP32-C6 compile: 2,080,910 bytes flash, 165,584 static RAM. Uploaded to /dev/cu.usbmodem101; hashes verified and reset.
- Boot sample: READY heap 64168; steady heap 60468; min_heap 59032; no crash observed during 12-second sample.
- Backend control/runtime/error tests: 11/11 passed. The Unix-socket test required sandbox-external execution.
- Swift optimized compile and precondition-based notification self-check passed; packaged executable self-checks and deep/strict signature verification passed.
- Device root/media screenshots and desktop offscreen rendered layout inspected. Native desktop UI automation timed out; full interactive desktop and notification delivery acceptance is for the user.

## Artifacts
New app: build/2026-11-05/Desk-Mate.app. Existing app preserved. Quit the old instance before opening the new bundle. Preview: build/2026-11-05/preview.png. No Git commit pending user acceptance.
