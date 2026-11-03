# 本地决策与 macOS App 开发验收（2026-11-03）

## 已验证

- Python 单元测试：158 passed。
- Swift 原生壳、AX helper 编译成功；菜单结构自检通过。
- 独立包关键模块导入通过；原生库加载路径已审计，外部非系统动态库收进 bundle。
- `codesign --verify --deep --strict` 通过。仅 ad-hoc 签名，未公证。
- 独立 Python 分发取代原 `.venv`，运行依赖根据锁定清单复制。模型按指定 manifest 复制并校验 SHA-256。
- 全新私有数据目录、仅系统 PATH 下，STT 与内置 Ollama 模型实际加载，HTTPS 服务启动成功。
- 同一数据目录重复实例被拒绝；主动终止后台服务后自动恢复；停止 supervisor 后其 Ollama、服务和 STT 子进程全部退出。
- 早期独立包移动到含空格目录后，STT、Ollama、HTTPS 服务仍能启动；该测试不等于另一台干净机器验收。

后台证据：`build/desktop-test/app-smoke-final.json`；菜单证据：`build/desktop-test/native-menu-final.json`；构建/审计日志位于同目录。

## 真实模型比较

冻结相同提示词、策略与语料，使用同一隔离 Ollama 实例串行评测，仅生成提案和验证计划，绝不执行播放器或账号动作。不加载个人训练分类器。

| 模型 | 基础集 | 额外表达集 | 模型路径中位耗时（两组） |
|---|---:|---:|---:|
| Qwen2.5 1.5B | 24/24 | 20/20 | 747ms / 743ms |
| Qwen2.5 3B | 23/24 | 19/20 | 1216ms / 1365ms |

共44条，其中15条规则、29条真实模型。规则耗时与模型耗时分别记录；以上耗时不含 STT、BLE 或播放器执行。首次请求提示词预填充的耗时计入分位统计。保留1.5B作为默认，3B仅存于开发测试目录，不增加交付包体积。

语料覆盖明确指令、口语、组合、否定/修正、字面内容、缺失上下文和未支持动作。两组语料都已参与开发迭代，**不是独立盲测**，44/44不能外推为任意自然语言100%准确。后续应持续加入真实使用中未用于调优的表达。

报告：`build/desktop-test/final-1.5b-base.json`、`final-1.5b-extra.json`、`final-3b-base.json`、`final-3b-extra.json`。报告包含逐条提案、参数、策略干预、遗漏/额外动作、耗时以及模型/提示词/策略代码/语料摘要。最终策略摘要：`03ff9a93d31153708ad665a46a259576c14df98559ad95ed37f0a0f8113d33a8`。

## 产物与未验收边界

产物：`build/release/Desk-Mate.app`、`build/release/Desk-Mate-macOS-arm64.zip`。Apple Silicon，当前依赖最低 macOS 26.3；构建机实际测试系统为 macOS 27.0.1。包不包含用户数据或配对私钥，首次启动生成独立身份。

分发 ZIP 为2,193,307,132字节，全部条目CRC校验通过；SHA-256 记录在 `build/release/SHA256SUMS`，归档校验记录在 `build/desktop-test/archive-check.json`。

尚未完成：安装到另一台没有开发环境的 Mac、Gatekeeper/Developer ID/公证、实际菜单交互、辅助功能/屏幕录制/蓝牙授权归属、登录启动、USB 授权和 BLE+B站完整真机链路。没有把既有开发版的真机证据转记为新 App 的验收结果。

本轮没有向设备写入新授权，没有执行投币/点赞/收藏/弹幕，没有自动重启 B 站。UI 自动化调用曾阻塞桌面客户端；恢复后采用命令行验证，没有再次调用该接口。

## 0.2.1 连接管理易用性更新

- 单测172项通过，新增 BLE 热切换顺序、忙碌保护、断开超时、私有 socket 权限与真实 RPC、离线保存、切换期间拒绝 begin、B站冷启动/确认后重启/端口冲突/启动失败等覆盖。
- `build/desktop-test/app-usability-smoke.json`：包内私有控制请求前后服务、Ollama、STT PID 保持不变；重复实例拒绝、异常恢复及退出清理通过。集成测试未连接真实蓝牙设备，只发送断开输入请求；两目标交接由模拟连接器单测验证。
- 新菜单及 helper 编译通过。`application-info` 实际读到已安装、正在运行的 B站元数据，该查询不需要辅助功能授权，也不启动/激活客户端。
- 修复一项既有并发测试的调度假设：不足10帧时持久化进度可能尚未刷新，测试现检查连续音频前缀与 seal 后的真实持久化计数；没有更改音频传输实现。
- 新包：`build/usability-release/Desk-Mate.app`，版本0.2.1，Apple Silicon、macOS 26.3+，仍为 ad-hoc 签名且未公证。完整硬件切换、真实 B站启动及原生界面操作仍待用户验收。
