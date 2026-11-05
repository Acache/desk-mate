# Learnings

## [LRN-20261101-BLE] best_practice

**Logged**: 2026-11-01
**Priority**: high
**Status**: in_progress
**Area**: backend

### Summary
BLE advertising API success and a correct cached packet do not prove continuous over-the-air discovery.

### Details
Phone discovery failed on iOS, Android and Harmony despite a successful advertising start and Flags 0x06/HID UUID/name in the cached payload. Add controller active/peer/handle diagnostics and correlate an independent scanner with the pairing window. NimBLE-Arduino 2.5.0 setDiscoverableMode does not invalidate uploaded payloads; whiteListRemove also increments an invalidated vector iterator after erase/shrink, so use one complete ble_gap_wl_set replacement under exclusive manager ownership.

### Suggested Action
Validate real radio state and discovery after mode changes; preserve existing bonds during diagnosis. Do not mark phone compatibility verified until a phone pairs and media control works.

### Metadata
- Source: user_feedback
- Related Files: firmware/desk-mate-app/src/media_ble/ble_manager.cpp
- Tags: ble, discovery, nimble


## [LRN-20261102-ADV] best_practice

**Logged**: 2026-11-02
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
NimBLE-Arduino 2.5.0 setName appends AD fields; rebuilding whole packets avoids silent exhaustion on repeated reconnects.

### Details
A cached-flags workaround repeatedly called setName, filling scan-response and advertising packets after a few reconnects. Fresh bounded advertising/scan-response packets fix this. Old paired hosts can also consume each new advertising procedure by auto-reconnecting: parking only their transport during the explicit pairing window keeps discovery continuous, while media stays restricted to one encrypted, subscribed target. User confirmed discovery on iOS, HyperOS and HarmonyOS after this change.

### Suggested Action
Treat discovery, bonding, media readiness and persistence as separate checks. A debug serial pairing window must never be confused with a user-opened new 120-second window; remove bench commands from the final verification build.

### Metadata
- Source: error, user_feedback
- Related Files: firmware/desk-mate-app/src/media_ble/ble_manager.cpp


## [LRN-20261102-PWR] best_practice

**Logged**: 2026-11-02
**Priority**: medium
**Status**: resolved
**Area**: firmware

AXP2101 supports 4/6/8/10s hardware shutdown but only 128ms/512ms/1s/2s
power-on. Use PMIC negative/positive edge snapshots for cancellable hold feedback.
Read failures must not become fake 0xFF key events (the library caches -1 in bytes).
Acknowledge observed key bits only, rather than clearing all IRQs after a read.
LVGL animation tests should allow one refresh interval at visual boundaries;
keep exact timing assertions in the independent model tests.

## [LRN-20261102-CMAKE] best_practice

**Logged**: 2026-11-02
**Priority**: low
**Status**: resolved
**Area**: testing

When requesting multiple build targets including a newly added target, explicitly
regenerate CMake first: the outer Makefile may have expanded targets before its
automatic regeneration. A subsequent build uses the regenerated target list.

## [LRN-20261102-VOICE-PAGER] correction

**Logged**: 2026-11-02
**Priority**: medium
**Status**: resolved
**Area**: design

The voice-management mockup incorrectly introduced bottom page dots although the
firmware already has a global three-dot pager at y=24, temporarily visible after
page changes. User prefers the existing top indicator. Preserve existing chrome
when redesigning an individual page; never add duplicate navigation affordances.
User selected voice-first, English UI, and larger existing Montserrat sizes.
Chinese glyphs can be embedded in firmware and do not inherently require SD or
individual image assets; English primarily reduces font Flash and reuse costs.

## [LRN-20261102-VOICE-MANAGER] best_practice

**Logged**: 2026-11-02
**Priority**: high
**Status**: resolved
**Area**: firmware

Five padded VoiceConfig blobs with 4KB CA fields can exhaust a small shared NVS
partition before five hosts are saved. Persist versioned credentials plus actual
CA length, keep one active config in RAM, and write SSID/password as one blob.
The default LVGL theme is disabled on hardware: textarea and keyboard item text,
background and alignment must be styled explicitly or valid widgets are invisible.
Bind durable feedback and pending commands to server identity; capture feedback
origin in the fresh-result branch before updating the UI's last-command marker.
User explicitly excluded end-to-end tests; model/store/UI/memory/compile checks
are distinct from physical speech, host switching and phone provisioning QA.

## 2026-11-02 — SDK defaults can override build-wide NimBLE flags

- Category: best_practice
- A plain build_opt.h `-DCONFIG_BT_NIMBLE_MAX_BONDS=8` was overwritten when ESP32 sdkconfig.h was included, even in a fresh build directory. An incremental-cache explanation was ruled out by preprocessor output.
- Force-include a project-owned header that loads sdkconfig.h first, then undefines/redefines NimBLE source-build settings; apply to every C/C++ library unit, not only the sketch. Verify final macros in both application C++ and NimBLE C sources, and keep compile-time assertions.
- Freeze legacy persistent struct capacities during expansion. Changing the shared limit inside old v1/v2 layouts silently breaks migration.
- LVGL8 long-dot label text can temporarily be ellipsized before a layout/timer pass. UI assertions that inspect rendered text must advance layout first.

## 2026-11-02 — BLE transport experiment details

- NimBLEHIDDevice also creates a NOTIFY-capable battery service. Budget persisted CCCDs for HID input, battery, service-change and custom voice data across all eight bonds (32), not merely input + custom data.
- A connected BLE HID peripheral may stop advertising. Discover existing CoreBluetooth peripherals by both HID and the custom service, then pass a BLEDevice containing the existing CBPeripheral/delegate to Bleak; passing its UUID string triggers a fresh scan and may miss it. Keep the private Bleak shim isolated and version-pinned.
- Even serial inspection with DTR/RTS initially false can interrupt an ESP32-C6 USB connection. Finish firmware uploads before requesting a manual Bluetooth reconnection, and keep measurements on BLE; disabled auto-reconnect is a user preference, not permission to silently enable it.
- Raw link failure must retain received-byte, sequence and cumulative-ACK counters. A complete but slow run is distinct from corrupt audio or a transport disconnect. Do not report mock throughput as radio throughput.

## 2026-11-02 — Additive GATT diagnostics and cached macOS service tables

- A firmware update added a read-only INFO characteristic after existing control/data. macOS continued exposing the earlier cached table, so treating INFO as mandatory aborted a usable transfer.
- Keep diagnostic extensions optional when the OS lacks the characteristic. Distinguish unknown counters from invalid reported counters (multiple peers still fails). Track uninterrupted same-client lifetime separately and do not claim the firmware counter/negotiated PHY was read. Added regression coverage for cached service tables.
- Actual tuned results: 32KB/s short and 60s sustained tests passed; 50KB/s headroom target failed despite complete correct delivery. Report each criterion independently rather than equating integrity, target throughput and full speech-control readiness.

## 2026-11-02 BLE production verification

- CoreBluetooth `retrieveConnectedPeripheralsWithServices_` can return a macOS HID-owned peripheral whose `CBPeripheral.state()` is 0 for the new app central. Do not discard it by state; attach using the returned BLEDevice and delegate. HID UUID may be hidden; battery180f/device-info180a and current/legacy custom services help locate the same explicitly selected UUID. Regression fixture covers state=0.
- Inlined firmware auth/session loops can retain a 2KB RX buffer while nested serialization and BLE queue-copy functions add another 2KB each. A 10KB voice task reached READY, then faulted on a heartbeat with stack protection. Use single-owner persistent scratch buffers, preserve non-reentrant ownership, and verify task stack high-water mark on real auth/heartbeats rather than accepting compilation and protocol unit tests alone.
- NimBLE `getPeerInfo(uint8_t)` indexes peers; use `getPeerInfoByHandle(uint16_t)` for connection handles. Do not rely on the first connection having handle0.
- Track service-change notification generation separately from optional remote-name lookup; user-named hosts may skip the name generation update.


## 2026-11-02 深夜 — 真实语音测试暴露的带宽与门槛问题

- 真实语音失败时先量“设备读秒 vs 主机收到的音频时长”。本次 5 秒按键只有 2.4–3.8 秒到达，直接指向发送侧丢帧，而不是麦克风或模型。`pcm_ring` 有 `dropped` 计数器但从未上报，诊断时只能靠时长反推；静默丢弃的缓冲应至少可通过日志或状态接口观察。
- 实验室峰值吞吐不是可用带宽。lab 的 31.9KB/s 没有余量，生产实测仅 21–23KB/s；设计必须按“持续速率 + 缓冲溢出语义”而不是最好成绩。原始 32KB/s 实时语音在 1.28 秒环形缓冲上必然失败。
- 微调 ACK（逐消息→每 4 包）只把 64 包/秒提到 100 包/秒，仍不够；真正的解法是把需求降到链路能力以内（IMA ADPCM 4:1，640B→164B）。优先改数据量，再谈调度优化。
- 逐消息 ACK 每个 GATT 写会占连接事件；批量 ACK + 控制消息立即 ACK 是正确组合。尾部必须确认，否则设备 2 秒无 ACK 会重置。
- 有损压缩下 SHA 校验要对齐“解码后 PCM”：编码器内部本来就有重建样本，固件对重建样本做哈希即可与主机落盘文件一致，无需改 seal/commit 语义。
- 用户纠正：不要用“按下就立刻说话”教用户迁就程序。用户会先按住再思考，程序要按最坏等待时间设计。任何“让用户改变操作习惯”的临时方案都应在下一轮被结构性修复替代。
- 设备文案之前靠中文 summary 关键词匹配，`OUT_OF_SCOPE` 因含“搜索”被显示成 `SEARCH EXPIRED`。应让服务端下发机器可读 `code` + ASCII `detail`，设备只负责显示。

## [LRN-20261104-ANIM] correction

**Logged**: 2026-11-04
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Animation optimization must cover all dynamic effects, not only power and page transitions.

### Details
User clarified scope includes finger drag, voice cancellation countdown, waveform and all other UI animation. Previous discussion identified power/page issues without completing that inventory.

### Suggested Action
Inventory every timer, animation and dynamic setter before claiming complete analysis; distinguish reduced redraw demand from measured hardware FPS.

### Metadata
- Source: user_feedback
- Related Files: firmware/desk-mate-app/focus_ui.h, firmware/desk-mate-app/power_ui.h
- Tags: scope, animation, profiling

## [LRN-20261105-UX] correction

**Status**: resolved
**Area**: frontend

Device drawer should have flat drawn chevrons, no redundant Close/instruction label, and finger-tracked motion with halfway commit/cancel. Passive connection status must not look like an action button. Preserve these distinctions when simplifying future screens. Routine startup waiting and automatic reconnect must never trigger modal error dialogs; report the actual failure stage and notify only on meaningful transitions.

## [LRN-20261105-RELEASE] correction

**Status**: resolved workflow; signing limitation documented
**Area**: infra

Never relocate the local app during iterative testing: preserve build/usability-release/Desk-Mate.app and replace it transactionally. Changing both path and ad-hoc code identity invalidates the user's verification baseline and can leave privacy toggles associated with old code. Keep version/build visible, regular app activation, automatic main window and lightweight main-process permission recheck. No automatic TCC reset; a stable certificate is needed to promise cross-build grants.

Context-first fixes must reach the whole collector→route→refresh→model→binding→execution chain, including home pages and complete compound instructions, rather than just adding a parser branch or hiding SEARCH FIRST.

## [LRN-20261105-REAL-ACCEPTANCE] correction

**Status**: resolved; real playback and cross-build native permission acceptance passed
**Area**: backend / macOS

Do not run any native checks from a staging `.app`. Initializing NSApp/privacy APIs there can register the staging filename as the authorization client. Native permission status must come from the running desktop App, not a CLI child executing the same binary. Restore explicit AX/Screen Capture request APIs for an explicit recheck and keep ordinary refresh prompt-free. A checked stale code identity in System Settings is not evidence that the current process has permission.

Parser success and mocked execution did not prove “播放第一个视频” worked. The real failed recording had the correct home BV plan but was rejected by an AX-only capability gate. A home-only CDP page must expose play capability before a player exists. Verify the entire real recording→STT→context→planner→adapter→correct BV→advancing media clock chain. Cold player loading also needs a realistic, bounded action budget; do not weaken success to “click dispatched”.

Run success evidence through the actual storage path too: canonical JSON prohibits floats, so CDP float seconds caused `STORAGE_ERROR` only after playback succeeded. Persist integer milliseconds and test real Adapter→Executor→SQLite→terminal result, not just adapter.verify(). The original failed recording now produces actual `SUCCEEDED / DONE` on published build 9.

A persistent self-signed certificate can provide normal certificate-bound local code identity without an Apple developer account or system trust changes. Two real published builds with different code hashes were verified against the same certificate DR. This does not migrate old ad-hoc TCC grants: ask for explicit permission before replacing the two stale system privacy entries, and never silently reset or fake their state.

After the user explicitly approved those two operations, select the canonical app through System Settings and complete both grants before restarting the App once. System Settings can keep the old staging display label in its own process cache: quitting and reopening Settings refreshed both rows to Desk-Mate without touching grants again. Build 9 → 10 then retained both real native preflight grants automatically across multiple refresh cycles, and backend AX capabilities were available. Test the next signed build after consent rather than only proving two signatures share a DR.

## [LRN-20261105-USB-ATOMIC] root cause and validation

**Status**: resolved; complete single-board hardware and native UI acceptance passed on 0.3.2 build 14
**Area**: macOS / ESP32 USB

Setting DTR and RTS false before pySerial.open does not avoid this ESP32-C6's reset: POSIX open still performs two separate modem ioctls. A controlled test produced a fresh USB_UART_HPSYS boot before any authorization payload. Pure os.open/close stayed authenticated; atomic TIOCMGET/masked TIOCMSET of both bits then passed real open/VA_SETUP/close with no boot banner, no link loss and stable service/model PIDs. Use usb_serial.open_provisioning_port for this identified native USB adapter. Do not disable the chip USB reset bit globally or change Bluetooth auto-reconnect preference as a workaround.

USB registration history must commit with credential insertion/revocation in SQLite. An independently reproduced JSON-after-DB-commit failure left stale history and caused repeated rotations plus two active credentials. The final implementation uses schema-3 usb_registrations and one transaction; a failed optional summary write is a warning, not a false authorization failure. After a board ACK but failed database commit, distinguish that partial outcome and explicit repair from a cable error. Roll back failed COMMIT while the connection is still in_transaction so later operations can recover.

The first complete grant test exposed separate BLE session and legacy-binding bugs after the USB reset was fixed. A fresh notification subscription must establish a new wire epoch/counter state, while same-generation subscription rearming must preserve it. The Mac must observe changed firmware epochs while idle even if the old database credential remains active, without extra STATUS reads during active audio.

An old peer-to-server binding needs scoped migration, not clearing all bindings or weakening proof checks. A successful USB profile write records a one-use migration grant. Only a complete proof matching the current device/server/token may consume that profile's grant and update the valid peer binding; stale proofs, invalid peers and unrelated conflicts remain rejected. Native tests cover failed NVS writes, grant consumption, and other-profile/Wi-Fi preservation.

After the user reconnected the board, final hardware acceptance covered real USB replacement, repeated adds with zero serial opens/no credential generation, a new authenticated voice epoch on reconnect, stable physical BLE generation and service/model/STT PIDs, and fresh authenticated snapshots after serial close. No ROM/reset/boot banners occurred; five Bluetooth hosts and automatic reconnect settings remained. Native UI Add also reused the record and retained both real macOS permissions. Evidence: build/usb-authorization-acceptance.json and build/usb-native-ui-acceptance.json. Coverage is one board/this Mac; other computer rows were unchanged and native regression protects other profiles/Wi-Fi, but physical second-Mac switching and full Wi-Fi provisioning were not tested.
