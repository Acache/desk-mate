# Agent 接手说明（2026-11-02）

## 从这里继续

用户准备交由另一 agent 接手。此分支是本轮多阶段开发的完整检查点，基于原 main 的 `5f1c55b`。此前改动一直保存在同一工作区，尚未按功能逐次提交；本次集中保存，避免遗漏新文件。没有重写旧提交，也没有推送远端。

**下一步是用户真实语音验收及根据反馈修复。** 不要把协议夹具、模拟 PCM 或连接心跳验证写成真实录音/STT/应用控制验收通过。用户明确要求真实语音与端到端测试由其完成；不要自动录音或提交执行指令。

## 当前实现

- 全局 PWR 4 秒硬件关机，环形反馈、整数秒、大字号与取消倒放/渐隐。语音入口使用独立 KEY（GPIO10），不复用 PWR。
- 第 3 屏英文语音优先界面，保留本地实时 RMS 音波、原顶部三点分页；电脑管理保存 5 份 USB 授权。
- BLE 管理保存 8 个配对，同一时刻仅 1 条物理连接；元数据兼容原 3 槽格式，不自动清空旧配对。
- 第 2 屏 HID 与第 3 屏语音共用选定 BLE 目标。原始音频为16kHz/mono/s16le、32KB/s、最长60秒，无压缩。
- 默认 BLE，Wi-Fi/WSS 可手动选择。无自动目标切换或未知指令重发。待结算指令、重放和反馈绑定原 server_id。
- BLE 双向 USB 凭据证明、随机挑战、蓝牙身份绑定；Mac 端 BLE/WSS 共用 `remote_session.py`，继续使用既有暂存/封存/提交/取消/执行流水线。

主要文档：[BLE正式用法](BLE_VOICE.md)、[进展](STATUS.md)、[设计与验证记录](superpowers/plans/2026-11-02-ble-voice-production.md)、[系统规格](../spec/MASTER_SPEC.md)。

## 已验证与未验证

- 103 项 Mac 单元测试通过；CTests9/9，实际32KB LVGL8池专项2/2通过；原语音FSM时序、音频转换/环形缓冲测试通过。
- 最终固件编译：Flash 2,072,860 / 2,097,152B；静态RAM164,744B。Flash空间余量较少，继续开发须检查实际镜像容量。
- 最终固件已刷入，上传哈希校验通过。BLE 双向认证、Ready、心跳持续验证通过，MTU247、单物理连接、同一代数；没有真实语音识别或应用动作测试。
- 运行时空闲堆约76KB、最低历史堆约65KB，语音任务最小剩余栈4940B；保留5条已有配对，自动回连保持关闭。
- 原型实验测得：32KB/s×60s全量接收，约31,919B/s；50KB/s实验仅约38,619B/s。这是前置数据实验结果，不是最终真实语音时延测量。
- **仍待用户验收：** 实际说话、长句、取消、识别效果、Bilibili执行、不同电脑/手机兼容及真实多机场景。不要假定手机已具备语音执行适配器。

## 关键修复，避免回退

1. 已真实复现并修复连接后约10秒重启：`voice_transport` 任务栈溢出。嵌套2KB收包/序列化/队列复制缓冲已移到各自任务独占的固定存储；这些函数依赖单任务、非重入所有权。不要再次改成嵌套栈数组，也不要从其他任务直接调用发送函数。
2. CoreBluetooth系统已连接的HID，在新应用central中 `CBPeripheral.state()` 可能仍为0。不能用此状态过滤 `retrieveConnectedPeripheralsWithServices_` 的返回值。隔离并锁定 Bleak3.0.2 私有shim；HID服务可能隐藏，查询battery180f/device-info180a及新旧自定义服务。只按明确设备UUID选择。
3. NimBLE `getPeerInfo(uint8_t)` 接受索引；按连接句柄查询必须用 `getPeerInfoByHandle(uint16_t)`。
4. HID订阅可能早于ATT MTU协商，必须更新后续协商到的MTU，否则23字节初值会导致4字节负载分片，无法正常传语音。
5. 服务变化提示按连接generation仅发送一次，不能依赖可能跳过的远端名称查询generation。
6. 音频环先检查回压再pop；单轮发送数量也必须先检查，避免额外取走一帧。
7. `build_opt.h` force-include `ble_build_config.h`，先读SDK配置再覆写NimBLE宏，并作用到所有C/C++单位。普通 `-D` 会被SDK覆盖。保持8 bonds、1 connection、CCCD预算40。

## 本机运行环境与命令

仓库：`/Users/acache/AI_PROJ/desk-mate`。ESP32-C6 USB：`/dev/cu.usbmodem101`。

本机 `.venv` 是指向 `/Users/acache/AI_PROJ/bili-esp32-ctrl/.venv` 的本地符号链接，未入Git；BLE依赖已安装在该运行环境。新机器/新checkout应按 bootstrap 建独立环境，不复制此绝对路径依赖。`mac/bin/ax_helper` 已本地编译但被忽略，新checkout需重新编译。

```bash
# 在仓库根目录
.venv/bin/python -m pip install -r mac/requirements-ble.txt
./run.sh --ble-address 7A640D0F-7BAA-C88F-4CE8-2D09289414B0
PYTHONPATH=mac/src .venv/bin/python -m pytest mac/tests/unit -q

arduino-cli compile \
  --fqbn esp32:esp32:esp32c6:CDCOnBoot=cdc,FlashSize=16M,PartitionScheme=no_ota \
  --build-path build/ble-production firmware/desk-mate-app
arduino-cli upload \
  --fqbn esp32:esp32:esp32c6:CDCOnBoot=cdc,FlashSize=16M,PartitionScheme=no_ota \
  --port /dev/cu.usbmodem101 --input-dir build/ble-production firmware/desk-mate-app

cmake --build build/sim -j4
SDL_VIDEODRIVER=dummy ctest --test-dir build/sim --output-on-failure
cmake --build build/sim-memory -j4
SDL_VIDEODRIVER=dummy ctest --test-dir build/sim-memory -R 'hardware_lvgl8|voice_wire' --output-on-failure
```

`build/sim*` 为已配置的本地构建目录；新checkout的CMake初始化参数见 `tools/lv_sim/README.md`，其中实际内存池为32768。依赖版本：Arduino ESP32 3.3.8-cn、NimBLE2.5.0、硬件LVGL8.4。

最后启动的Mac助手进程为66304，使用上述BLE UUID。此PID仅是交接时参考，**先检查端口/健康状态，不要重复启动或盲目终止其他进程**。健康端点 `https://127.0.0.1:8765/healthz` 返回 ready 与 ble认证/MTU/包确认计数；本机自签名TLS诊断需要信任配置或仅诊断时忽略验证。

真实BLE操作在受限执行环境中可能假报不支持，需要本机蓝牙权限。USB开串口会触发设备复位；稳定性测试中不要临时再开串口破坏现有连接。USB维护命令 `VA_BLE_CONNECT_SELECTED` 可在持续串口监视中显式连接已选目标，遵循忙碌保护，不改自动回连，不录音、不提交指令。

## 本地证据与隐私

以下在 `build/`，被Git忽略，不随分支迁移：

- `ble-production-compile.log`、`ble-production-upload.log`
- `ble-production-final-stability.log`、`ble-production-health.json`
- `ble-auth-crash.log`（修复前栈保护崩溃证据）
- `ble-lab-suite.json`、`ble-lab-32k-60s.json`（前置吞吐实验）
- `previews/voice-ble-network.png`（界面预览）

本机真实凭据、证书、历史录音和SQLite数据位于用户的Application Support目录，未入Git。不要把这些文件、虚拟环境、编译产物或原始用户指令加入提交。
