# LVGL 桌面模拟器

> 更新：2026-11-01。当前模拟器复用三页 `focus_ui.h`，用于计时器、分页、反馈覆盖层的交互/渲染检查。旧六页、桌宠、Token/ACP 与 base64 语音说明已不适用。

## 范围

`sim_main.c` 直接包含固件 UI 头文件，并链接 `focus_font_120.c`；电池、媒体发送和语音状态由 stub 提供。模拟器不运行 Arduino 主循环、物理按键采样、真实 BLE、I2S 或 WSS，不能替代端到端控制验收。

| 目标 | 用途 |
|---|---|
| `deskmate` | LVGL 9：SDL 交互窗口、测试、截图 |
| `deskmate_v8` | 可选 LVGL 8.4：硬件 UI 渲染检查，仅支持测试和截图 |
| `timer_test` | 不依赖硬件的计时器状态模型测试 |

## 构建与测试

需要 CMake、C 编译器、SDL2 和 LVGL 源码。当前默认 LVGL 9 路径为仓库外部依赖目录 `_lv_sim/lvgl`；不存在时显式传入路径。不要将下载的依赖或构建产物提交到仓库。

从仓库根目录运行：

```bash
cmake -S tools/lv_sim -B build/sim -DLVGL_DIR=/path/to/lvgl-9.x
cmake --build build/sim -j
ctest --test-dir build/sim --output-on-failure
./build/sim/deskmate
```

需要检查硬件使用的 LVGL 8.4 时，配置时额外提供目录：

```bash
cmake -S tools/lv_sim -B build/sim \
  -DLVGL_DIR=/path/to/lvgl-9.x \
  -DLVGL8_DIR=/path/to/lvgl-8.4
cmake --build build/sim -j
ctest --test-dir build/sim --output-on-failure
```

CTest 默认包含 timer、swipe_and_ui；配置 LVGL8_DIR 后增加 hardware_lvgl8_swipe_and_ui。`--test` 还覆盖反馈覆盖层状态转换。构建目标名是 deskmate，不是 lv_sim。

## 截图

页码：0=Focus，1=Mac，2=Voice。命令行参数替代了旧 LVGL_PAGE/LVGL_SCREENSHOT 环境变量：

```bash
./build/sim/deskmate --screenshot 0 /tmp/deskmate-focus.bmp
./build/sim/deskmate --screenshot 1 /tmp/deskmate-mac.bmp connected
./build/sim/deskmate --screenshot 2 /tmp/deskmate-voice.bmp
# 配置 LVGL8_DIR 后可用同样参数检查硬件渲染
./build/sim/deskmate_v8 --screenshot 0 /tmp/deskmate-focus-v8.bmp
```

当前 connected 参数会设置模拟连接状态并推进计时器；不是实际连接 Mac。Voice 截图路径会展示反馈覆盖层，不代表麦克风采集或语音执行结果。

## 迭代方式

1. 修改共享 UI，构建并运行 CTest。
2. 检查所改页面截图；涉及硬件字体/布局时加 LVGL 8.4 渲染检查。
3. 涉及 Arduino、按键、BLE、音频或传输时，再编译固件并按改动范围做实机回归。

当前完成状态与后续任务见 [项目进展](../../docs/STATUS.md)。

## 蓝牙管理与真实内存限制测试

```bash
cmake -S tools/lv_sim -B build/sim-memory \
  -DLVGL8_DIR=/path/to/lvgl-8.4 -DLVGL8_POOL_BYTES=32768
cmake --build build/sim-memory --target deskmate_v8 ble_model_test -j
ctest --test-dir build/sim-memory -R 'hardware_lvgl8|ble_policy' --output-on-failure
```

截图仍用默认扩容池构建（快照需要整帧内存），例如：

```bash
./build/sim/deskmate_v8 --screenshot 1 /tmp/bluetooth.bmp bluetooth
./build/sim/deskmate_v8 --screenshot 1 /tmp/bluetooth-last.bmp bluetooth-last
./build/sim/deskmate_v8 --screenshot 1 /tmp/bluetooth-detail.bmp bluetooth-detail
./build/sim/deskmate_v8 --screenshot 1 /tmp/bluetooth-rename.bmp bluetooth-rename
./build/sim/deskmate_v8 --screenshot 1 /tmp/bluetooth-pair.bmp bluetooth-pair
```

这些截图使用虚构主机数据；跨主机无线连接需要实机验证。

蓝牙截图默认显示八台虚构设备，列表每页两台；`bluetooth-last` 为第四页。自动 UI 测试覆盖四页、奇数末页、删除后页码修正、触摸/物理翻页、稳定设备身份和八台容量提示。

## 全局电源浮窗测试

CTest 增加 `power_hold`（阈值、取消、重新按住、延迟短按和时钟溢出）；LVGL UI 测试覆盖浮窗触摸隔离、倒放、渐隐、蓝牙键盘叠层和 32KB 内存池。模拟器不执行真实断电。

```bash
./build/sim/deskmate_v8 --screenshot 0 /tmp/power.bmp power
./build/sim/deskmate_v8 --screenshot 1 /tmp/power-cancel.bmp power-cancel
```

截图会包含全局电源顶层。实机需验证长按约 4 秒断电、短按原动作、中途松开不关机，以及冷开机；不要用模拟器通过替代硬件验收。

## 语音电脑界面

`voice_hosts` / `voice_host_store` 是无硬件单元测试。UI 测试覆盖确认默认保留、稳定身份、名称键盘、RMS 波形、取消读秒、PWR 叠层；不连接真实 Mac。截图：

```bash
./build/sim/deskmate_v8 --screenshot 2 /tmp/voice.bmp voice-home
./build/sim/deskmate_v8 --screenshot 2 /tmp/recording.bmp voice-recording
./build/sim/deskmate_v8 --screenshot 2 /tmp/cancel.bmp voice-cancel
./build/sim/deskmate_v8 --screenshot 2 /tmp/computers.bmp voice-manager
./build/sim/deskmate_v8 --screenshot 2 /tmp/detail.bmp voice-detail
./build/sim/deskmate_v8 --screenshot 2 /tmp/rename.bmp voice-rename
./build/sim/deskmate_v8 --screenshot 2 /tmp/network.bmp voice-network
```
