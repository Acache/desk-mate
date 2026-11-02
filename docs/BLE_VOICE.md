# BLE 语音桥接 R1

第 2 屏媒体快捷键与第 3 屏语音共用同一条蓝牙连接，同一时间连接/控制一台对端。BLE 保存 8 台配对记录；语音授权电脑仍为现有 5 个 USB 凭据槽。默认语音链路改为 BLE；Wi-Fi/WSS 保留为手动选择，不会因断线自动换电脑或重发指令。

## 启动与使用

首次授权继续使用 USB `pair` 命令；已有授权无需重新配对。BLE 使用已保存凭据的双向 HMAC 证明，不发送原始 token。电脑蓝牙身份在认证后绑定助手 server_id，名称不参与认证或自动选择。

```bash
.venv/bin/python -m pip install -r mac/requirements-ble.txt
# 用实验工具列出 Desk-Mate 的 CoreBluetooth UUID（macOS 上不是蓝牙 MAC 地址）
PYTHONPATH=mac/src .venv/bin/python -m voice_assistant.ble_lab scan
# 指定这台 Desk-Mate；与原来的 WSS 服务共用同一个助手进程
./run.sh --ble-address '<Desk-Mate CoreBluetooth UUID>'
```

1. 第 2 屏蓝牙管理选择当前 Mac 并连接。自动回连设置保持用户原来的选择。
2. 第 3 屏管理 → Network → `USE BLUETOOTH`。页面提示 `START_MAC_ASSISTANT` 时启动上面的助手进程。
3. 助手完成双向认证并确认模型/应用能力可用后，语音页进入 Ready。
4. 按住 KEY 说话，保留实时音波；松开后仍有原来的取消窗口。PWR 继续负责全局电源操作。
5. 需要原来的链路时选择 `USE WI-FI`；其电脑目标单独记忆。`CHANGE WI-FI` 保留原有手机热点配网。

BLE 不依赖同一 Wi-Fi、路由器客户端互访或公网。Mac 仍可正常上网。手机和平板能否执行语音指令取决于后续适配器，现阶段正式接收端为 Mac 助手。

## 故障与边界

- `CONNECT_BLUETOOTH`：先在第 2 屏连接 Mac。
- `START_MAC_ASSISTANT`：蓝牙媒体已连接，但助手尚未认证。
- `BLE_PAIRING_REQUIRED`：当前助手没有 USB 授权，或这条蓝牙身份已绑定另一助手。不要通过改设备名绕过；确认助手身份、USB 授权，必要时从设备管理中明确忘记对应记录后重新配对。
- `BLE_AUTH_FAILED/TIMEOUT`：凭据不匹配或认证超时。检查设备是否撤销/重新授权。
- Mac 提示 `BLE voice service missing`：系统可能缓存旧 GATT；先断开/连接蓝牙刷新。程序不自动清空配对。
- 未提交的录音断线会清理暂存；已提交但结果未知的命令只向原电脑查询。不会把它发给另一台设备。
- BLE/语音工作忙碌或存在待确认结果时禁止切换目标。手动断开会终止传输。

## 实现与验证

- 原始 16kHz、单声道、s16le PCM：32KB/s，最长 60 秒；无音频压缩。
- 生产服务 `d35c1001-69b0-4b89-9a72-5fd60d200001`；与实验服务分开。HID 服务保持注册顺序，发送 GATT 服务变化提示。
- 每消息上限 2048B，16B 包头、MTU 分片、24 包累计确认窗口、2 秒无确认超时。连续帧号、长度、连接 epoch 校验及有界队列防止旧连接数据混入。
- NimBLE owner 处理无线操作，传输任务处理应用消息；两种链路共用 Mac `remote_session.py` 和原有提交/执行流程。
- USB 凭据派生密钥、双向随机挑战、不同方向 HMAC 标签、10 秒认证有效期、持久身份绑定；Mac 每秒检查撤销。
- 本轮自动验证使用合成 PCM、假通道及临时数据库，不调用真实语音识别或应用动作。真实语音与控制效果由用户验收。

相关：[实验吞吐记录](BLE_VOICE_LAB.md)、[设计](superpowers/specs/2026-11-02-ble-voice-production-design.md)、[实施记录](superpowers/plans/2026-11-02-ble-voice-production.md)。

## 2026-11-02 本机验证记录

103 项 Mac 单元测试、9 项 CTest、实际32KB LVGL8池专项2项，以及原语音FSM/音频转换测试通过。纯协议夹具校验60秒模拟帧序列与MTU变化，不采集真实音频。最终固件 Flash 2,072,860B，静态RAM164,744B。

真实蓝牙验证只检查认证/心跳：MTU247、同一连接代数、单个物理连接、Ready持续超过2分钟。运行时空闲堆约76KB、最小历史堆约65KB，语音任务最小剩余栈4,940B。早期连接后重启已定位为嵌套临时缓冲区造成任务栈溢出，改为任务专用固定缓冲区后未再复现。CoreBluetooth状态过滤和初始MTU未更新也已修复。

真实录音、识别、取消体验和Bilibili操作验收由用户执行，本轮没有代替用户做真实语音端到端测试。

USB维护命令 `VA_BLE_CONNECT_SELECTED` 只显式连接当前已选的配对目标，遵循同一管理队列和忙碌保护；它不改自动回连设置，不采集音频，不提交指令。
