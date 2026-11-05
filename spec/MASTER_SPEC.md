# MASTER_SPEC — Desk-Mate 语音助手系统规格

> 本规格于 2026-10-25 依据代码重建，2026-11-01 结合合并后的源码、历史讨论和运行台账修订。
> 原版设计稿的完整文件未在当前仓库定位；历史会话仅补充设计背景。代码定义当前实现，完成状态与后续待办统一见 [项目进展](../docs/STATUS.md)。
> **当前阶段：B 站 R1 链路已打通，进入更新迭代；协议中声明的 R2 动作不等于已交付能力。**

---

> 2026-11-05 当前行为补充：发布固定路径、权限探测、首页/搜索/播放页上下文与精确/模型路由，以 [当前整理记录](../docs/RELEASE_AND_CONTEXT_2026-11-05.md) 为准；历史“先搜索才能播放第 N 个”的假设不适用。

## 0. 项目一句话

> 把一块 ESP32 触摸屏变成 Mac 的**物理入口**：桌面计时器 + 蓝牙媒体键 + 按住说话执行真实应用操作（首个落地场景：B站）。
> 设备只负责**输入、采集、显示**；主机负责**理解、决策、执行**。两端各自独立校验同一份类型化契约。

---

## 1. 系统边界与职责分离

| 侧 | 拥有 | 绝不做什么 |
|---|---|---|
| **设备**（ESP32-C6） | 输入采集、音频环形缓冲、显示状态、本地计时器 | **不决定一条指令的含义** |
| **主机**（macOS） | 语义解析、策略裁决、执行 | **不渲染端侧 LVGL UI**（主机有桌面配置窗口） |

- 命令是**类型化契约**（typed contract），两端**各自独立校验**
- 设备侧进程与主机侧服务**故障隔离**：主机挂了，屏 1/2（计时器/媒体）仍可用

---

## 2. 硬件

### 2.1 板子

| 项 | 值 |
|---|---|
| 型号 | **微雪 Waveshare ESP32-C6-Touch-AMOLED-2.16** |
| 显示 | 480×480 AMOLED，代码使用 SH8601 驱动，GPIO15 = LCD CS |
| 触摸 | I2C 地址 0x5A（GPIO5 = INT，GPIO11 = RST）；以当前触摸驱动为准|
| 麦克风 | **ES7210** 双麦，I2S（GPIO19 MCLK / 20 SCLK / 21 DIN / 22 LRCK / 23 DOUT）|
| 电源 | AXP2101 PMIC（GPIO7/8 = I2C SCL/SDA，0x34）|
| IMU | QMI8658（GPIO16 = INT1）|
| RTC | PCF85063（I2C）|
| 存储 | SPI SD 卡（GPIO0 CLK / 1 MOSI / 2 MISO / 3 DATA3 / 4 DATA2 / 6 CS）|

> 本仓库目标为 **ESP32-C6-Touch-AMOLED-2.16，480×480，三物理按键**。旧资料中的 1.43 寸、466×466、CO5300 和触摸录音描述不作为当前基线，也不重复列为阻断已交付 R1 的未知项。

### 2.2 三个物理按键

| 按键 | 接入 | 当前语义 |
|---|---|---|
| BOOT | GPIO9，上拉、按下拉低 | Focus 重置；Mac 上一曲/音量减，长按切页；Voice 准备/确认安全重放 |
| KEY | GPIO10，上拉、按下拉低 | Focus 重新开始/长按选预设；Mac 下一曲/音量加，长按切模式；Voice 按住说话 |
| PWR | AXP2101 PEK，非普通按键 GPIO | 短按按页面路由；Voice 请求取消并返回 Focus；全局长按 4 秒由 PMIC 断电，约 1 秒后显示 3 秒圆环提示；松手倒放并渐隐 |

运行时 BOOT 不是重启键，KEY 不是预留键。完整映射与时间参数见 [引脚与按键](../firmware/desk-mate-app/pinout.md)。

蓝牙连接管理见 [蓝牙管理用法](../docs/BLUETOOTH.md)。BLE 语音与媒体共用当前选定对端，Wi-Fi 语音目标单独记忆。

### 2.3 屏幕分区（LVGL pager）

| 屏 | 内容 | 交互 |
|---|---|---|
| 1 | **Focus 番茄钟** | 倒计时、触摸滚轮、开始/暂停/重置、5/15/25 分钟预设|
| 2 | **Media 媒体** | BLE HID 媒体/音量键；最多 8 台已配对主机、同一时刻只连接和控制 1 台；每页 2 台设备 |
| 3 | **Voice 语音** | 物理 KEY 按住录音；松开后留取消窗口，再提交执行 |

---

## 3. 通信协议

### 3.1 传输

2026-11-02 正式加入 BLE 语音，默认通过与 HID 共用的单连接传输；BLE 链路用 IMA ADPCM 4:1 压缩（逻辑音频仍为 16kHz/s16，主机解码后落盘与识别），Wi-Fi 保持原始 PCM；Wi-Fi/WSS 可手动选择。两条链路复用同一套会话、取消、封存、提交和执行契约；BLE 额外以 USB 凭据派生密钥做双向挑战证明并绑定蓝牙对端身份。详见 [BLE 语音桥接](../docs/BLE_VOICE.md)。原始吞吐依据见 [实验记录](../docs/BLE_VOICE_LAB.md)。

- **WSS（TLS WebSocket）**，默认端口 **8765**
- 双向 JSON 消息，**客户端（设备）→ 服务端（主机）** 与 **服务端→客户端** 各自一组类型
- 证书校验强制；设备 token 通过 **USB 预配**（`provisioning.py`）
- 服务发现方向：Mac 发布 `_voice-assistant._tcp.local.`，ESP32 查询；mDNS 只提供地址线索，TLS 验证身份

### 3.2 设备 → 主机

| type | 类 | 用途 |
|---|---|---|
| `hello` | `Hello` | 握手，携带设备身份 |
| `begin` | `Begin` | 开始一次命令（携带 command_id）|
| `replay_prepare` | `ReplayPrepare` | 重放准备（复现历史命令）|
| `seal` | `Seal` | 音频封口（标记流结束）|
| `commit` | `Commit` | 提交 → 触发执行 |
| `cancel` | `Cancel` | 取消 |
| `status` | `StatusQuery` | 查询状态 |
| `ping` | `Ping` | 心跳 |
| `confirm` | `Confirm` | 回应确认请求 |
| `feedback` | `Feedback` | 用户 GOOD/BAD 反馈 |

### 3.3 主机 → 设备

| type | 类 | 用途 |
|---|---|---|
| `hello_ack` | `HelloAck` | 握手应答 |
| `begin_ack` / `append_ack` / `seal_ack` | `*Ack` | 各阶段确认 |
| `replay_ready` | `ReplayReady` | 重放就绪 |
| `commit_ack` | `CommitAck` | 提交应答 |
| `cancel_ack` | `CancelAck` | 取消应答（`canceled` / `too_late` / `already_terminal`）|
| `step_result` | `StepResult` | 单步结果 |
| `result` | `Result` | 整体结果 |
| `feedback_ack` | `FeedbackAck` | 反馈应答 |
| `status_snapshot` | `StatusSnapshot` | 状态快照 |
| `progress` | `Progress` | 进度推送 |
| `error` | `ErrorMessage` | 错误 |
| `pong` | `Pong` | 心跳应答 |
| `confirm_preview` / `confirm_required` | `ConfirmPreview` / `ConfirmRequired` | **危险操作二次确认** |

### 3.4 命令状态机

```
STAGING → SEALED → COMMITTED → TRANSCRIBING → PLANNING
   → [AWAITING_CONFIRMATION] → EXECUTING → {SUCCEEDED | PARTIAL | FAILED}
                                    → CANCELED | ABANDONED | INTERRUPTED | OUTCOME_UNKNOWN
```

终态集合：`SUCCEEDED / PARTIAL / FAILED / CANCELED / ABANDONED / INTERRUPTED / OUTCOME_UNKNOWN`

### 3.5 单步状态

`PENDING → PREPARED → STARTED → {SUCCEEDED | NOOP_VERIFIED | FAILED | SKIPPED | OUTCOME_UNKNOWN}`

> `NOOP_VERIFIED` 是重要的设计：**目标已经是那个状态时，也要有可验证的证据**，而不是静默跳过。

---

## 4. 语音管线

```
物理 KEY 按住 → I2S 采集 (16 kHz mono s16) → 有界 PCM 环形缓冲 → BLE 侧 IMA ADPCM 4:1 编码（640B→164B）
   → BLE 分片或二进制 WS 帧 (WSS) → 共享会话 → SenseVoice STT → 规则 planner → [可选] 本地意图个性化 → [回退] 本地 LLM
   → ValidatedPlan → macOS Accessibility (ax_helper) → Bilibili
   → 结果回传设备；GOOD/BAD 覆盖层喂给个性化闭环
```

### 4.1 STT

| 项 | 值 |
|---|---|
| 模型 | **`iic/SenseVoiceSmall`**（本地）|
| 进程 | `python -m voice_assistant.stt_worker --model <model>` |
| 音频格式 | 逻辑格式 16 kHz / 单声道 / 16 bit（**严格校验**，不合规 → `AUDIO_INVALID`）；BLE 线缆编码为 `codec=ima4`，Wi-Fi 为 `s16le` |
| 失败策略 | **fail closed** —— 模型/运行时缺失 → `MODEL_UNAVAILABLE`，**绝不回退云服务** |

### 4.2 决策层（★ 核心）

`pipeline.py` 定义执行顺序：支持意图预检查 → 确定性规则 → 可选本地个性化 → Ollama 回退 → 策略护栏 → 计划校验。规则优先是当前明确实现，不再列为尚未决定的问题。

| 项 | 值 |
|---|---|
| 规则层 | `intent.py` → `rule_based_proposal()` + `apply_policy_guard()` |
| LLM 层 | `decision.py` → Ollama，`http://127.0.0.1:11434` |
| 模型 | **`qwen2.5:1.5b`**（`config.ollama_model`）|
| Prompt | `mac/prompts/intent-v1.txt`（33 行，含 schema 示例）|
| 输出上限 | `MAX_RESPONSE_BYTES = 32 * 1024` |
| 信任模型 | **模型输出是不可信数据**（`decision.py` 文档字符串原话）|

### 4.3 决策提案 schema

```json
{
  "schema_version": 1,
  "status": "ok | clarify_needed | out_of_scope",
  "reason_code": "none | ambiguous | missing_target | unsupported | too_many_steps | policy_denied",
  "clarification": "string | null",
  "model_confidence": 0.0-1.0 | null,
  "actions": [
    {"id": 0, "action": "bilibili.play", "params": {...}, "after": []}
  ]
}
```

`status` 三态设计很关键：
- `ok` —— 直接执行
- `clarify_needed` —— 信息不足，**反问用户**（`clarification` 字段承载问句）
- `out_of_scope` —— 明确超出能力范围，**不猜**

`model_confidence` 可空 —— **不强迫模型每次都给置信度**，但给了就必须合法（0-1）。

### 4.4 动作契约（18 个：R1 5 个 + R2 13 个）

默认启用集合由 `api.AppState.enabled_actions = contracts.R1_ACTIONS` 定义：`open/search/play/pause/resume`。下表记录全部契约，不代表全部已启用或已实现。

| 动作 | 参数 | 说明 |
|---|---|---|
| `bilibili.open` | — | 打开 B 站 |
| `bilibili.search` | `query`（≤128 字符 / ≤512 字节）| 搜索 |
| `bilibili.play` | **三选一**：`bv` / (`index`+`search_result_id`) / `query` | 播放 |
| `bilibili.pause` | — | 暂停 |
| `bilibili.resume` | — | 继续播放 |
| `bilibili.next` | — | 下一集 |
| `bilibili.prev` | — | 上一集 |
| `bilibili.favorite` | `video_id?` | 收藏 |
| `bilibili.like` | `video_id?` | 点赞 |
| `bilibili.history` | — | 历史 |
| `bilibili.danmaku_on` | — | 开弹幕 |
| `bilibili.danmaku_off` | — | 关弹幕 |
| `bilibili.send_danmaku` | `text`（≤30 字符 / ≤240 字节）, `video_id?` | 发弹幕 |
| `system.open_app` | `app_id`（小写别名）| 打开应用 |
| `system.vol_up` | `delta`（1-10，默认 5）| 音量+ |
| `system.vol_down` | `delta`（1-10，默认 5）| 音量- |
| `system.mute` | — | 静音 |
| `system.screenshot` | — | 截图 |

> R2 包含另外 13 个动作，默认禁用。真实 B 站适配器的能力探测和执行分支主要覆盖 R1；扩展时需要同步实现意图支持、能力探测、prepare/invoke/verify 和实机验收。不能仅扩白名单或补 probe 就宣布可用。

### 4.5 跨步骤引用（★ 关键机制）

参数值可以写成 **`$prev.output.<field>`** 形式，指向**前一步的输出**：

```
search_result_id  ← bilibili.search 输出
video_id          ← bilibili.play 输出
app_id            ← system.open_app 输出
player_generation / view_generation ← 播放器/视图快照代数
```

`planner.py` 的 `OUTPUT_FIELD_SOURCE` 明确规定**谁可以产出哪个字段**：

| 字段 | 唯一来源动作 |
|---|---|
| `search_result_id` | `bilibili.search` |
| `video_id` | `bilibili.play` |

`parse_reference()` 解析 `$prev.output.X`；`source == -1` 表示"当前步之前的最后一步"。

> **设计价值**：这让"搜索 → 播第 3 个"这类多步意图可以用**声明式**方式表达，
> 而不需要模型自己记住搜索结果 —— **模型不用处理它看不到的状态**。

### 4.6 依赖与短路

`ActionStep.after: list[int]` 声明**依赖的前置步骤 id**。

- `planner.py` 校验依赖图合法性（不能引用未来的步骤、不能自引用、不能越界）
- **依赖动作失败 → 后续依赖它的动作短路**（不执行），整条命令可能落到 `PARTIAL`
- `ValidatedPlan` 里 `after` 被**排序**（`sorted(a.after)`）保证确定性

### 4.7 计划校验（planner = 唯一可信入口）

`planner.py` 文档字符串原话：

> "The LLM proposal is untrusted data. This module is the only place that turns it into a ValidatedPlan."

校验内容：
- 动作名在白名单内（`ACTION_PARAM_TYPES`）
- 参数通过 Pydantic 校验（`_Strict` 模型 → 禁止多余字段）
- 依赖图合法
- 引用字段的产出者匹配（`OUTPUT_FIELD_SOURCE`）
- **目标新鲜度**：`player_snapshot` / `search_snapshot` 过期 → `TARGET_STALE`
- 参考目标解析（`parse_reference`）

### 4.8 确认门（危险操作二次确认）

- `ConfirmPreview` / `ConfirmRequired` 消息
- `Confirm` 客户端响应
- 主机协议与规划器已有相关结构；启用 R2 前仍需核验设备 UI、按键与消息处理是否形成真实确认闭环，不将协议存在等同于实机交付
- 超时 → `CONFIRM_EXPIRED`
- `planner._preview_for()` / `pipeline._account_for()` 生成预览

---

## 5. 存储（SQLite，14 表）

### 5.1 表清单（`migrations/001_initial.sql`）

| 表 | 用途 |
|---|---|
| `schema_migrations` | 迁移版本 |
| `devices` | 设备注册 |
| `commands` | 命令主表（状态机落库）|
| `action_steps` | 单步明细 |
| `audio_samples` | 音频留存 |
| `feedback` | 用户反馈 |
| `preferences` | 偏好 |
| `model_versions` | 模型版本指纹 |
| `training_runs` | 训练运行 |
| `command_tombstones` | 墓碑（防重放）|
| `snapshots` | 播放器/视图快照（带 generation）|
| `confirmations` | 确认记录 |
| `account_rate_limits` | 账号级限流 |
| `device_sessions` | 设备会话 |

索引：`idx_commands_device_created` / `idx_commands_state` / `idx_commands_replay_of` / `idx_snapshots_generation`

### 5.2 `002_feedback.sql` 增量

给 `feedback` 加：`rating` / `reason` / `dismissed_by` / `touch_count` / `countdown_at_dismiss` / `context_json` / `source`（默认 `device`）/ `received_at`
新增表：`feedback_analysis_runs`

---

## 6. 反馈与本地个性化

设备 GOOD/BAD 覆盖层将反馈写入 SQLite。当前实现包含以下四个模块：

| 模块 | 实际职责 | 触发方式 |
|---|---|---|
| `training.py` | 样本量统计、训练提醒、数据导出 | 服务每 6 小时调用提醒循环；另有 CLI |
| `personalization.py` | 字符 n-gram + 逻辑回归分类器，只预测打开/暂停/继续 | CLI `train-personalization`；在线服务仅加载与推理 |
| `auto_training.py` | 从显式 GOOD/BAD 反馈构建样本并验证，新模型验证准确率提高时替换 | CLI `auto-train-feedback`；当前 API 生命周期未自动调用训练 |
| `feedback_analysis.py` | 批量反馈分析，给出供人工审阅的建议 | CLI `analyze-feedback`；默认批次 50，最少显式标签 10 |

这不是 SenseVoice 或 LLM 的后训练/LoRA 微调，不能描述为在线自动训练。`auto_training.readiness()` 当前按至少 10 条可用样本和至少两个类别判断；代码注释中的“50 组”不是该函数实际执行的门槛。

在线识别和控制默认本地运行，原始 PCM 不上传云服务。反馈批量分析可使用显式配置的外部 API，届时会发送转写文本、计划和部分上下文；“完全本地”不能无条件覆盖这一可选入口。

待复核的个性化数据质量包括：BAD 反馈与正确标签的对应关系、重复语句的训练/验证隔离和模型回滚。跟踪任务见 [迭代待办](../docs/STATUS.md#后续待办)。

---

## 7. 配置

| 项 | 默认 |
|---|---|
| 端口 | 8765 |
| 成功留存 | 1825 天 |
| 失败留存 | 1825 天 |
| 音频配额 | 16 GB |
| STT 后端 | `auto` |
| Ollama 模型 | `qwen2.5:1.5b` |
| Ollama URL | `http://127.0.0.1:11434` |

数据目录权限：`secure_makedirs(mode=0o700)`，文件 `0o600`（`write_private`）。

---

## 8. HTTP / WS 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/healthz` | 健康检查（含 `active_command`）|
| GET | `/v1/status/{command_id}` | 命令状态 |
| WS | `/v1/remote` | 主协议通道 |

**WS 连接管理**：`ConnectionManager`（`api.py`）
- `attach(device_id, ws)` —— 同设备旧连接被**顶掉**（新连接接管）
- `detach` / `send` / `close`
- `notify_for(device_id)` —— 异步推送

---

## 9. 安全属性

| 属性 | 实现 |
|---|---|
| **动作白名单** | `ACTION_PARAM_TYPES` —— 无任意脚本执行 |
| **TLS 证书校验** | 强制 |
| **设备 token 预配** | USB 通道（`provisioning.py`）|
| **有界超时** | 所有外部调用都有 timeout |
| **背压控制** | 音频配额 + 环形缓冲上限 |
| **故障隔离** | 主机服务挂掉不影响屏 1/2 |
| **重放防护** | `command_tombstones` |

---

## 10. 环境要求（★ 有坑）

| 工具 | 版本 | 用途 |
|---|---|---|
| macOS | 13+ | 主机服务、辅助功能 |
| **Python** | **3.11（当前验证环境）** | 对齐依赖锁与原主机环境；其它版本未在本仓库验证，不推断 wheel 可用性 |
| arduino-cli | 1.0+ | 固件 |
| ESP32 core | 3.3.x (`esp32:esp32`) | 固件 |
| Xcode CLT | 较新 | `swiftc` 编译 `ax_helper` |
| Bilibili 桌面版 | 最新 | 语音控制目标 |

---

## 11. 代码地图

```
mac/src/voice_assistant/
├── contracts.py        类型契约：Envelope、状态机、错误码、决策 schema
├── storage.py           SQLite 持久层 + 迁移
├── config.py            配置、身份生成、权限
├── cli.py               命令行
├── pipeline.py          编排：COMMITTED → STT → 计划 → 执行
├── sessions.py          会话与状态机驱动
├── stt.py               SenseVoice 接入
├── executor.py          执行器
├── intent.py            规则 planner + 策略护栏
├── planner.py           计划校验与目标绑定 ★ 唯一可信入口
├── decision.py          Ollama 决策适配器（输出不可信）
├── provisioning.py      设备 token 预配
├── feedback_analysis.py 反馈分析
├── personalization.py   个性化
├── training.py          训练
├── auto_training.py     自动训练触发
├── api.py                     FastAPI + WS
├── stt_worker.py          STT 子进程
├── discovery.py          设备发现
├── ax_daemon.py         辅助功能守护
├── adapters/
│   ├── base.py
│   ├── bilibili.py      B站适配（含 ax_helper 调用）
│   ├── macos.py         系统级动作
│   └── fake.py          测试替身
├── migrations/
│   ├── 001_initial.sql
│   └── 002_feedback.sql

mac/prompts/intent-v1.txt  决策 prompt

mac/tests/
├── unit/{test_contracts,test_feedback,test_intent,test_planner_executor,test_storage}.py
└── integration/{test_protocol,test_tls}.py

firmware/desk-mate-app/
├── pinout.md               当前引脚与按键
├── desk-mate-app.ino        入口与页面输入路由
├── focus_ui.h / focus_model.h
├── ble_ui.h / src/media_ble/ 蓝牙管理面板与连接策略
└── src/bili_voice/          录音、状态机、传输与配网
```

---

## 12. 当前实现边界

| 范围 | 已核对事实 |
|---|---|
| 硬件与按键 | 2.16 寸 480×480 三按键基线；KEY 物理录音已实现，旧“KEY 预留”说明作废 |
| R1 状态 | 用户确认已打通；历史运行台账五类动作有成功记录；不能因服务暂时停止改回未完成 |
| R2 状态 | 默认禁用，真实 B 站执行层主要覆盖 R1；须逐动作贯通后开放 |
| 模型置信度 | `DecisionProposal.model_confidence` 校验范围但未作为执行授权依据；本地意图分类器另有 0.55 推理阈值，两者不同 |
| 账号/公开操作 | 主机有确认结构；R2 开放前核验设备确认、目标绑定、限流和真实执行，不能以模型自报置信度替代确认 |
| 发弹幕限制 | 契约校验 30 字符/240 字节；不将早期讨论的 30 秒限流或置信度阈值当成已交付行为 |
| 个性化 | 在线提醒与推理、CLI 训练/分析；未实现 ASR/LLM 自动微调 |
| 诊断命令 | doctor 的 screen_capture 当前为固定值；退出码要求 Ollama 健康且端口空闲，需按字段判断是否阻断当前动作 |

---

## 13. 后续扩展与验收原则

进展、证据和待办只在 [docs/STATUS.md](../docs/STATUS.md) 维护；本规格不再保留另一份冲突任务清单。

- 新动作须贯通输入、意图、契约校验、能力探测、真实执行与结果验证；协议注册或测试替身通过不是实机交付。
- 启用账号/公开操作前核验确认闭环与目标新鲜度，外部结果不明时不得盲目重复执行。
- 固件变更在同一镜像中回归计时器、BLE、第三页输入隔离、取消/重放与离线恢复。
- 记录版本、环境、真实输入与验证结果。历史完成状态和当次运行状态分别维护。

---

*重建于 2026-10-25；2026-11-01 按合并基线、源码与历史运行记录修订。*

### 2026-11-02 第 3 屏扩展

语音电脑按稳定 server_id 保存最多 5 份独立授权，Wi-Fi 配置整体单独保存。旧单主机 NVS 迁移为第一条；USB 配对追加或更新记录，不覆盖其他电脑或已有 Wi-Fi。仅一台语音目标；新目标语音就绪后提交切换，失败恢复原目标，待结算命令/反馈绑定原服务器。BLE 模式下，第 3 屏连接操作通过认证绑定关联到第 2 屏同一蓝牙目标；Wi-Fi 模式保留独立语音目标。英文 UI 保留实时 RMS 音波、530ms 资格和最早 840ms 提交，以及原顶部分页点和全局 PWR。真实多机/语音验收由用户执行，本轮不运行端到端测试。用法见 [语音电脑管理](../docs/VOICE_COMPUTERS.md)。
