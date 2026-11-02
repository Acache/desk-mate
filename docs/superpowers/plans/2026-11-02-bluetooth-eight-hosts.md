# Bluetooth Eight Hosts Implementation Plan

**Goal:** 保存八台蓝牙配对信息，最多一条连接和一个控制目标。

**Architecture:** build_opt.h 为整个 sketch 与 NimBLE 统一配置；模型维持一个带代数的活动会话；元数据兼容固定旧布局；分页 UI 使用稳定身份。

**Tech Stack:** Arduino ESP32-C6、NimBLE-Arduino 2.5、NVS、LVGL8/9、CMake/CTest。

## Global Constraints

- Eight saved bonds, one simultaneous connection, one media target.
- Preserve existing bonds, aliases, last target, settings; no automatic eviction.
- English UI, existing global top pager unchanged, actual 32768-byte LVGL pool.
- No end-to-end tests; user performs real wireless acceptance.
- Preserve pre-existing workspace changes; no bulk staging or automatic commits.

## Tasks

- [x] Add failing native tests for eight hosts, old v2 three-host migration and metadata v3 roundtrip (`firmware/test/test_ble_model.c`).
- [x] Implement capacity, build-wide settings, versioned metadata and single-link admission (`src/media_ble/{ble_contracts,ble_metadata,ble_manager}`, `build_opt.h`). Run native policy tests.
- [x] Extend simulator tests for four pages, physical/touch navigation, stable identities, last-page removal and full capacity (`tools/lv_sim/sim_main.c`).
- [x] Implement two-device pages with count and Prev/Next (`ble_ui.h`). Run LVGL8/9 and 32KB pool suites, inspect screenshots.
- [x] Compile firmware, inspect NimBLE compiler configuration and binary capacity. Update Bluetooth guide, spec, status and validation records.

## Validation and delivery

- Native policy and legacy v1/v2 metadata migration tests passed; new v3 retains all eight slots.
- CTest build/sim: 7/7; build/sim-memory BLE and hardware LVGL8: 2/2, actual 32768-byte pool.
- Inspected LVGL8 first/last-page screenshots in build/previews/ble-eight-{first,last}.png.
- Arduino complete build in build/ble-eight passed with 2,057,420 bytes Flash and 151,768 bytes static RAM. Preprocessor checks for application C++ and NimBLE C confirmed 8 bonds / 1 connection / 16 CCCDs.
- USB upload to /dev/cu.usbmodem101 succeeded, hash verified and reset; log /tmp/ble-eight-upload.log.
- No end-to-end or eight-real-device pairing test was run. User acceptance remains outstanding.
