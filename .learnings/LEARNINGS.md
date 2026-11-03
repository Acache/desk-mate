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
