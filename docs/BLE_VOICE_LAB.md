# BLE 语音链路实验与对端桥接方案

本页记录迁移前的 **PCM 等效数据率 BLE 实验通道**。现已加入正式 BLE 语音，配置见 [BLE_VOICE.md](BLE_VOICE.md)；以下实验测量作为迁移依据保留。实验不读取麦克风、不启动识别、不执行软件操作。Wi-Fi 主机授权和蓝牙媒体配对均保留。

## 共用连接

实验服务与 HID 都挂在同一个 NimBLE server，连接上限仍为 1，保存配对上限仍为 8。只有选定、已加密、已配对且 HID 就绪的主机可以测试。实验开始时暂时占用语音输入，完成/停止/断开后释放；媒体快捷键不被改写，也不自动发送按键。

长期产品中，第 2 屏为同一目标的快捷键，第 3 屏为通用语音入口。语音能力需要该设备运行接收程序并完成应用授权，不能因为手机已蓝牙配对就显示语音可用。媒体与语音在不同设备之间不隐式分流。Wi-Fi 保留为显式选择的路径，不把执行状态未知的命令自动重发到另一个目标。

## 构建实验固件

普通编译没有实验服务。专用构建：

```sh
arduino-cli compile \
  --fqbn 'esp32:esp32:esp32c6:CDCOnBoot=cdc,FlashSize=16M,PartitionScheme=no_ota' \
  --build-property compiler.cpp.extra_flags=-DDESKMATE_BLE_LAB=1 \
  --build-path build/ble-voice-lab firmware/desk-mate-app
arduino-cli upload \
  --fqbn 'esp32:esp32:esp32c6:CDCOnBoot=cdc,FlashSize=16M,PartitionScheme=no_ota' \
  --port /dev/cu.usbmodem101 --input-dir build/ble-voice-lab
```

实验期间请求 15ms 连接间隔与 2M PHY（是否接受由 Mac 决定），结束后恢复 15–30ms 的正常连接间隔。实验使用协商后的 MTU 分包，通知包不超过 244 字节，包头 16 字节。带 run ID、序号、绝对偏移、长度以及可重新生成的校验数据。接收窗口默认 24 包，Mac 每四包或尾包累计 ACK；发送失败不推进偏移，停顿两秒退出，最多运行 60 秒且总数据不超过 3.2MB。不保存模拟音频。

GATT：service `d35c0001-69b0-4b89-9a72-5fd60d200001`，control `d35c0002-69b0-4b89-9a72-5fd60d200001`，data `d35c0003-69b0-4b89-9a72-5fd60d200001`。info `d35c0004-69b0-4b89-9a72-5fd60d200001` 只读返回实际连接数、MTU、连接间隔和会话代数。控制报文是 little-endian `<BBHIII>`：version、opcode（1 start / 2 ACK / 3 stop）、window、run、a、b。Start 的 a/b 是总字节数/目标字节率；ACK 是 next sequence/累计有效字节数。数据同样用 `<BBHIII>` 头：version=1、kind=2、payload length、run、sequence、byte offset。状态 `<BBHIIIIII>`：version、state、payload size、run、sent sequence、sent bytes、ack sequence、ack bytes、rate；state 0 idle、1 running、2 complete、3 stopped、4 stalled、5 low MTU、6 invalid、7 voice busy。

CCCD 保存预算为 32（八台 × HID、battery、service-change、实验通知），由同一 `ble_build_config.h` 统一配置 C/C++。NimBLE HID 工厂同时创建可订阅的电池服务，不能遗漏其预算。扩容不是增加连接数。实验服务添加在已有 HID 服务之后，尽量保持原服务顺序；若 macOS 仍缓存旧版 GATT 表，新增的 info 辅助诊断可能不可见；接收工具会注明诊断缺失并继续测已有数据通道，不自动清除配对。此时报告用同一 BleakClient 且全程无断开来验证两轮共用连接，不声称已读取到固件连接数或会话代数。

## Mac 数据实测

在单独虚拟环境安装可选依赖；不需要加载整个语音模型环境：

```sh
python3 -m venv build/ble-lab-python
build/ble-lab-python/bin/python -m pip install -r mac/requirements-ble.txt
PYTHONPATH=mac/src build/ble-lab-python/bin/python -m voice_assistant.ble_lab scan
```

先在 Desk-Mate 第 2 屏选择并连接这台 Mac。macOS 首次使用时按系统提示授权 Bluetooth。Scan 同时查找广播设备与已经连接的 Desk-Mate HID 外设；macOS 返回的是 CoreBluetooth UUID，不是 BLE MAC 地址。已连接设备的枚举使用 Apple 正式 API，经固定 Bleak 3.0.2 的小型兼容接口访问；升级依赖时需验证此接口。

用扫描得到的地址运行（`--suite` 会在同一条连接里连续测两档数据率）：

```sh
PYTHONPATH=mac/src build/ble-lab-python/bin/python -m voice_assistant.ble_lab run \
  --address '扫描返回的UUID' --suite --seconds 10 --report build/ble-lab-suite.json
```

测试成功仅表示：完整数据按序到达且内容正确、设备确认全部收到 ACK、有效速率达到目标的 95%、最大接收停顿不超过 250ms。连续长时测试、复杂无线环境、休眠恢复、真实麦克风和识别、与媒体键同时使用都需后续验证；没有测得数值时不宣称“BLE 语音已验收”。真实软件端到端控制继续由用户完成。

测试结束后可按普通编译流程刷回生产固件，或保留实验固件使用原 Wi-Fi 语音；实验数据只在接收程序明确发起时发送。

## 对端接收程序如何演进

对端程序是长期运行的设备桥接与指令处理层，现有代码已经有 session、STT、planner、pipeline、executor、adapter、storage 和 personalization，不需要重写识别执行链。

| 层 | 职责 | 当前与后续 |
|---|---|---|
| 传输接入 | BLE/WSS 分包、流控、重连、链路状态 | WSS 已有；BLE 当前实验 |
| 会话与授权 | 设备/应用身份绑定、能力协商、命令归属、取消、提交、幂等 | 保留现有提交与取消契约；正式 BLE 需双向应用认证 |
| 识别和规划 | 语音转文本、意图理解、规划、必要确认 | 复用现有代码 |
| 平台/应用适配器 | 执行支持的操作并验证结果 | 当前 macOS/Bilibili；其他平台逐个接入 |
| 记录与优化 | 操作历史、反馈、诊断、版本与效果评估 | 继续本地记录和评估后更新，不自行扩大权限 |

下一阶段先将 WebSocket handler 内的消息调度抽为共享应用层，再让 BLE 输入进入同一套 begin/audio/seal/commit/cancel/status 流程。蓝牙加密配对不等于应用授权；正式执行前要把 BLE 身份与既有 server/device 身份显式绑定，并加入新鲜挑战的双向认证、凭据撤销。手机、平板的跨应用操作受各系统权限限制，不能沿用 Mac 能力直接承诺全部支持。

完整方案见 [设计](superpowers/specs/2026-11-02-ble-voice-bridge-design.md) 和 [实施计划](superpowers/plans/2026-11-02-ble-voice-bridge.md)。

## 2026-11-02 实际验证记录

| 测试 | 结果 | 边界 |
|---|---|---|
| 主机单元测试 | 85 项通过，其中 BLE 15 项 | Mock 不代表无线吞吐 |
| 原生/UI | CTest 8/8；32KB LVGL8/协议专项 2/2 | 不执行实际软件操作 |
| 普通/实验固件 | 均编译通过；普通固件确认不含实验服务 | 固件 Flash 使用约 98% |
| 优化前 32KB/s × 10s | 收到 258,096 字节，未完成，超时 | 已收到的数据无校验损坏/乱序；不算达标 |
| 优化前 16KB/s × 5s | 全部 80,000 字节确认；含启动的平均 14,642.8 B/s，最长停顿 143.46ms | 低于 95% 速率标准，仍不算达标 |
| 调优后 32KB/s ×10s | 320,000 字节全部确认；31,598.9 B/s；最长停顿 82.89ms | 达到 95% 门槛，通过 |
| 调优后 50KB/s ×10s | 500,000 字节全部确认；38,619.1 B/s；最长停顿 132.37ms | 完整性通过，速率未达 47,500 B/s 门槛 |
| 调优后 32KB/s ×60s | 1,920,000 字节全部确认；31,919.2 B/s；最长停顿 152.14ms | 持续数据率通过；报告 build/ble-lab-32k-60s.json |

调优版已刷入并通过写入哈希校验；其改动是传输期间请求 15ms 间隔/2M PHY、24 包窗口、通过 BLE 读取实际单连接/MTU/间隔/会话代数。对端可以拒绝参数请求，最终以完整数据实测为准。Wi-Fi/WSS 正常语音路径保留。

两档测试在同一个 BleakClient 连接内连续运行，无断开事件，MTU 247，无数据校验损坏、乱序或旧 run 数据。macOS 缓存的旧 GATT 表未暴露新增 info 特征，报告如实将固件连接计数/代数标记为未读取；同连接结论来自接收端连接生命周期。15ms/2M PHY 是固件请求值，未通过本次辅助诊断确认协商结果。完整结果：`build/ble-lab-suite.json`。

本次结论：32KB/s 原始语音等效短测和 60 秒持续测试通过；50KB/s 余量测试未达标。不能把当前配置下的 38.6KB/s 测量值当成芯片带宽上限，也不能把数据测试等同于真实麦克风/语音控制验收。
