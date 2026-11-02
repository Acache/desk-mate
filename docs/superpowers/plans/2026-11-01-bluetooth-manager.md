# Bluetooth Manager Implementation Plan

> Design approved by the user on 2026-11-01. Execute sequentially in this checkout; preserve the existing uncommitted documentation work.

**Goal:** Manage three bonded media hosts with one active host, pairing windows, switching, persistence and a touchscreen manager.

**Architecture:** A hardware-independent C policy model produces a fixed-size snapshot. A NimBLE adapter owns callbacks, bounded queues, per-session HID and Preferences. Shared LVGL UI consumes snapshots and submits identity-bound requests; it never calls NimBLE.

**Tech stack:** Arduino ESP32-C6 3.3.8-cn, NimBLE-Arduino 2.5.0, LVGL 8.4/9, C99 native tests.

## Constraints

- Three remembered hosts, one active media session. Preserve existing bonds and all three pages.
- Pairing 120s, explicit connect progress 30s, encrypted + subscribed before READY.
- Stop unrestricted automatic advertising; unknown peers only admitted during pairing.
- No flash erase or changes to Wi-Fi/voice credentials. BLE callbacks never call LVGL.
- Keep the hardware LVGL pool at 32KiB. Target-specific HID; never replay old presses after switching.

## Task 1: policy and contracts

Files: src/media_ble/ble_model.h, ble_contracts.h, firmware/test/test_ble_model.c.

- [x] Write and run failing tests for import, target admission, pairing capacity/expiry, manual disconnect, auto reconnect, pause and stale sessions.
- [x] Implement the bounded policy model and request/snapshot types.
- [x] Run native tests with warnings enabled.

## Task 2: radio adapter

Files: src/media_ble/ble_manager.h/.cpp, media_ble.h, desk-mate-app.ino.

- [x] Implement bond reconciliation and versioned Preferences metadata.
- [x] Bound callback/request queues; track connection generation and target identity.
- [x] Restrict advertising, reject extra peers, preserve bonds, prevent overflow eviction.
- [x] Route HID by handle, release keys and reject stale queued reports.
- [x] Compile complete C6 firmware before UI integration.

## Task 3: UI and routing

Files: ble_ui.h, focus_ui.h, desk-mate-app.ino, tools/lv_sim/sim_main.c.

- [x] Add failing simulator tests for management actions, confirmation and input isolation.
- [x] Implement lazy panel/list/detail/pair/rename/confirmation views and media status.
- [x] Add physical focus navigation and page/layer generation ownership.
- [x] Run LVGL 8/9 tests, measure actual 32KiB pool usage and inspect screenshots.

## Task 4: integration and delivery

- [x] Run native BLE/timer/voice/audio tests, simulator suites and firmware compilation.
- [x] Flash preserving NVS, inspect boot, memory and original-host reconnect.
- [ ] Real second-host pairing/switching/forget validation (requires another phone/computer).
- [x] Record what real devices were available; do not claim unperformed multi-host tests.
- [x] Update progress, user instructions and remaining hardware checks.

Use focused file staging if a commit is needed; do not sweep unrelated documentation changes into implementation commits.

## 联测反馈修复（2026-11-01）

用户要求统一修复后再验证：iOS 配对后列表缺失、默认 Host 名称、第二次重连失败、配对倒计时不一致。

- [x] 配对记录按 NimBLE 实际 bond 与解析身份核实，增加身份事件处理和保存状态，避免临时地址进入列表；启动对账与重连使用同一身份。
- [x] 对端允许时异步读取 GAP Device Name，结果按会话归属校验；无法读取时配对完成直接提供常用设备名与自定义命名入口。
- [x] 每次用户主动开启新增配对均创建清楚的新窗口，界面区分启动广播、可发现倒计时与加密连接；最终固件关闭串口调试入口并烧录。
- [x] 原生/模拟器回归列表刷新、命名、元数据迁移、新窗口与真实内存池；实机 Mac 连续三轮断开再连、三次正常固件重启保留配对通过，调试版约 90 分钟无堆检查失败。
- [x] 用户统一联测后确认 iOS 和 Android 测试均 OK；串口恢复后导入 3 条实际 bond 并恢复最后目标，已更新进展记录。
- [ ] 媒体键结果逐项确认、鸿蒙完整配对/控制及 Windows 实测。
