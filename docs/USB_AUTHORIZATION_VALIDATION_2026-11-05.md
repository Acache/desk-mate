# USB 授权防复位与连接交互验证

2026-11-05，固定发布路径的 **Desk-Mate 0.3.2 build 14** 与更新后的固件已完成同一块 ESP32-C6、同一台 Mac 的真实授权、重复添加、显式替换和重连验收。最终报告 `build/usb-authorization-acceptance.json` 为 `accepted: true`。

## 旧复位原因与对照证据

- 旧流程：设备已认证时，使用原生产流程相同的 pySerial False/False 打开方式，在发送任何授权命令前出现 `ESP-ROM`、`rst:0x15 (USB_UART_HPSYS)`，4.4 秒后出现新的 `READY heap`，原认证中断。提前设置 DTR/RTS 为 false 仍不能避免分次写控制线造成的复位。
- 对照：纯 `os.open`/close 全程保持认证；原子更新 DTR/RTS 的 open → `VA_SETUP_READY` → close 也全程保持认证，无 ROM、复位或启动 READY 输出。
- 正式安全串口模块的初步实板检查记录于 `build/safe-usb-port-acceptance.json`：握手成功、无启动重刷，服务与模型 PID 不变。该检查只证明安全打开和握手，未被当作完整换凭据验收。
- 原始本机证据位于 `build/usb-open-diagnostic/`、`build/usb-atomic-diagnostic/`。没有关闭固件的全局 USB 复位机制，也没有更改蓝牙自动重连偏好。

## 完整联测发现并修复的连接问题

首次完整验收中，USB 写入获得 ACK、数据库保存成功且没有复位，但运行服务未能认证新凭据。因此当时的结果仍判为失败，继续追查至真实认证恢复。

1. **凭据热更新与通知订阅的会话生命周期。** 服务端空闲时有界轮询固件 STATUS，发现 epoch 改变即重建认证，即使旧密钥暂时仍有效也不会停留在旧会话；录音期间跳过这类 ATT 读取。固件在通知订阅从关闭变为开启，或物理连接代数改变时建立新会话；同一代数的重复订阅不清空正在组装的帧。会话刷新安静处理，不弹断连通知。
2. **历史 peer 绑定阻止已明确 USB 重授的电脑认证。** 实板门限记录为 `slot=1 nonce=1 pending_other=0 binding_other=1`，并非凭据 proof 不匹配。USB 保存当前电脑配置后，固件仅为该配置记录一次性绑定迁移许可；只有完整 HMAC proof 成功、候选凭据仍与当前存储匹配且 peer 身份有效，才迁移当前加密 peer 的绑定并消费许可。添加电脑时不提前改正在使用的绑定；失败 proof 不消费许可，未获新 USB 许可的旧配置仍不能覆盖其他绑定。持久化失败时拒绝完成认证，可重新明确 USB 授权恢复。

历史绑定迁移的独立实板记录为 `build/usb-binding-bootstrap-acceptance.json`，`accepted: true`：由原先的认证超时恢复为新凭据的真实双向认证，物理连接代数和服务、模型、STT PID 均保持。

## build 14 完整实板结果

`mac/scripts/verify_usb_authorization.py` 从正式发布 App 加载代码，针对实际运行服务和设备执行操作，报告保存于 `build/usb-authorization-acceptance.json`。

| 检查 | 实测结果 |
| --- | --- |
| 已登记设备正常添加 | 复用本机记录，串口打开次数为 0，连接继续认证 |
| 显式 USB 重新授权 | 仅打开一次串口；ACK 后运行服务使用新设备凭据完成双向认证 |
| 两次重复添加 | 均不生成凭据、不进入串口发送流程，所有凭据行保持不变 |
| 定向替换 | 仅撤销这块 USB 板映射的旧凭据，新增一项；其他已有设备行逐项保持不变 |
| USB 操作是否复位 | 全程无 ROM、`rst:` 或启动 `READY heap`；USB 授权前后物理蓝牙 generation 均为 1 |
| 真实重新连接 | 建立新的语音 epoch；设备凭据、服务、模型和 STT PID 均不变 |
| 关闭诊断串口后 | 两次新刷新的健康快照均保持新凭据认证，排除了使用关串口前缓存状态的情况 |
| 设备状态与偏好 | 实际 STATUS 为 `net=READY`；BTM 仍为 `hosts=5 enabled=1 auto=1 target=4 current=4`，与安全串口对照中的记录一致 |

完整验收中，USB 换凭据只更新语音会话 epoch，未改变物理蓝牙连接代数。主动重新连接也成功建立新 epoch，未停止服务、STT 或模型。

之后在正式原生 UI 再点击普通“添加设备”，显示“已有本机授权记录 · 未打开 USB 或更换凭据”；连接仍为已验证，两项 macOS 原生权限仍显示已授权。操作前后只读比对确认设备凭据表摘要、记录数量、语音 epoch、服务/模型/STT PID 均不变；`build/usb-native-ui-acceptance.json` 的全部九项检查通过，`accepted: true`。

## 软件与发布验证

- 284 项单元测试、8 项语音协议集成用例、6 项 native C/C++ 测试通过。覆盖安全控制线、USB 身份、幂等授权、定向替换、错误 ACK、数据库事务/COMMIT 失败、摘要写入失败、会话刷新/订阅及一次性绑定许可等相关行为。
- Swift 优化编译、授权状态和安静通知自检、代码签名验证通过；固件编译与实际刷写校验通过。
- 发布固定为 `build/usability-release/Desk-Mate.app`，版本 **0.3.2 build 14**，沿用持久本机签名证书。没有执行 staging App 或重置 macOS 权限。
- 数据库为 v3，升级前保留私有 SQLite 备份；发布资源的 37 个 Python 模块、3 份 SQL 迁移文件均与源码逐字节一致。
- 最终软件一致性、原生权限与实际连接状态汇总于 `build/usb-software-validation.json`；实板和原生 UI 两项均为 passed。代码保留未提交，供用户验证。

## 验证范围

以上真实硬件结论限于本机与同一块 ESP32。其他电脑的数据库记录未改、固件主机数量及自动连接偏好保留，也有原生测试覆盖其他 profile/Wi-Fi 配置不被清除；**没有实际切换第二台电脑或执行完整 Wi-Fi 配网联测**，不得将这些写成已完成的现场验收。

`build/` 下报告和原始诊断是本机验证产物，不进入发布包，也不包含明文设备 token 或可直接用于 BLE 认证的密钥。
