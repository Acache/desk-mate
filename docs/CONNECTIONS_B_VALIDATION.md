# B and global Connections validation

## B experiment

Five deterministic LVGL8 simulator runs per buffer size; median CPU time. Host CPU time excludes ESP32 scheduling, SPI/DMA and real BLE/audio/TLS workloads. It is not hardware FPS.

| Buffer lines | Page-snap batches | CPU ms | Finger-drag batches | CPU ms |
|---|---:|---:|---:|---:|
| 10-lines | 722 | 21.89 | 1298 | 26.442 |
| 12-lines | 602 | 19.747 | 1082 | 23.548 |
| 20-lines | 362 | 16.272 | 650 | 19.168 |

Selected 12 lines per hardware DMA buffer: +3,840 bytes versus 10 lines. Keep 30ms refresh/input periods and two circle-cache slots. 20 lines uses +19,200 bytes; not selected due voice/TLS memory margin. Four circle-cache slots aborted with Out of memory in circ_calc_aa4 during the 32KB-pool regression, so this variant was rejected. Actual device baseline before this build: READY heap 68072, steady heap 64372 and min_heap 62936; no real recording/TLS stress acceptance is claimed.

## Connections

User approved one shared top-edge/tap entry with Bluetooth, Wi-Fi and Voice connection rows. Root and children reuse existing contracts/registries; Wi-Fi connectivity and voice readiness are distinct. Connection controls have moved into this hierarchy; Media retains playback/volume and Voice retains recording/cancel. Direct page status taps also open the shared root. Back returns a level; close/upward dismissal returns to the saved page. Power hold overlays have priority.

The current controller management gate prevents entry during recording/sending or while a record key is held; VOICE BUSY explains this. Read-only browsing during a command remains deferred. Existing commands are not interrupted to enter settings.

Media reconstruction now immediately runs focus_refresh, so its first return frame contains the current snapshot rather than the Your devices placeholder.

## Validation

- LVGL8/9 CTest 12/12 passed.
- Actual 32KB LVGL8 pool regression passed, including all Connections branches, exact page returns and power overlay.
- Added tests for top-edge drag entry, busy gate, branch root Back, and immediate peer-name restoration.
- Root screenshot visually inspected.
- Firmware compile passed: 2,079,566 bytes flash, 165,576 bytes static RAM.
- Uploaded to /dev/cu.usbmodem101; all hashes verified and device reset.
- Boot health sample: READY heap 64168; steady heap 60468; min_heap 59032; BLE READY with saved peer/current slot 4. No crash observed in the 14-second sample. Real gesture smoothness, voice recording and full user acceptance remain pending.
