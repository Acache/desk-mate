# Agent 接手说明（HANDOFF / 2026-11-03）

> 这是当前唯一的交接文档，覆盖式替换 2026-11-02 版本。新接手者先读完本文，再动代码。
> 用户预期：**真机验证优先于口头完成**；不要把单测/夹具/模拟器通过写成真机验收通过。

## 1. 当前状态一句话

第 3 屏 BLE 语音链路已稳定；R2 的 10 类播放器动作（点赞/投币/收藏/收藏到文件夹/评论/快进/跳到指定秒/上一集/下一集/音量三种/弹幕）全部接入并有真机证据；B 站 PC 客户端已通过**本地 CDP（127.0.0.1:9222）**实现毫秒级控制与回读，命令路径已**不再调用任何 B 站公开网络接口**。

## 2. 本轮（2026-11-03）新增与修复

### 2.1 第 3 屏语音稳定性（真机复现后修复）

- **会话被误重置**：`ble_voice_poll()` 只要「缓存 ready ≠ 本次 ready」就重新生成 epoch → `ble_session_impl` 判定传输丢失 → 屏显 OFFLINE。修复：epoch 只在 generation 变化 / 显式 reset / RX 溢出时重置；HID 就绪抖动只更新标志。
- **语音就绪与 HID 解耦**：`ble_manager_poll` 改为按「连接是否存在」传入 ready，不再依赖 `ble_model_ready()`。
- **就绪抖动去抖**：`poll_ble_session` 对 not-ready 保持 1.5 秒宽限；音频 drain 遇 not-ready 保留帧等待恢复。
- **ACK/通知失败不再杀会话**：`tx->notify` 失败不再 reset；非法 ACK 忽略；已收消息队列 2→8；ACK 窗口停滞阈值 2s→10s 并单独打日志。
- **pong 看门狗** 16s→30s；ping 日志带 `sent/acked`；FSM 每次状态切换打印 `VOICE: fsm t=<ms> state=<n> result=<r>`。
- **主机端**：通知停滞 7s 后重订阅（失败不关闭会话），最多 4 次；`subprocess` 不再继承管道（`open -b` 曾导致挂死几十秒）；AX 调用加 2s 消息超时。

### 2.2 R2 播放器动作（契约→意图→计划→执行→回读→设备文案）

| 语音 | action | 参数 |
|---|---|---|
| 点赞 | `bilibili.like` | — |
| 投币 | `bilibili.coin` | `count` 1/2 |
| 收藏 | `bilibili.favorite` | 默认收藏夹 |
| 收藏到某文件夹 | `bilibili.favorite` | `folder`（≤32 字符） |
| 打开/关闭评论区 | `bilibili.comments` | `open` bool |
| 快进 N 秒 / 到 X 分 Y 秒 | `bilibili.seek` | `delta_s` **或** `target_s`（二选一，严格校验） |
| 上一集 / 下一集 | `bilibili.prev` / `bilibili.next` | — |
| 音量调大/调小/调到百分比 | `system.vol_up` / `vol_down` / `system.volume` | `delta` / `target 0-100` |
| 发送弹幕 | `bilibili.send_danmaku` | `text`（≤30 字） |

- 契约：`contracts.py` 新增参数模型与 `ENABLED_ACTIONS`（R1 + 上述动作，共 16 项能力）。
- 意图：`intent.py` 增加确定性语法（含中文数字解析 `_cn_number`、快进方向、音量百分比、文件夹名）；「关注」仍拒绝。
- 计划：`planner.py` 对 like/favorite/danmaku/next/prev 绑定当前播放器快照；seek/comments/coin 走无绑定。
- 设备文案：`pipeline.action_detail()` → `LIKED / COIN SENT / FAVORITED / COMMENTS / SEEKED / DANMAKU SENT / NEXT EPISODE / PREV EPISODE / VOLUME UP / VOLUME DOWN / VOLUME SET`，由 `Result.detail` 下发。
- 适配器：`adapters/bilibili.py`、`adapters/macos.py` 完成 prepare/invoke/回读；无网路接口调用。

### 2.3 CDP 本地播放器接口（本轮最大提速）

- B 站用 `--remote-debugging-port=9222` 启动后，`127.0.0.1:9222/json` 暴露页面 target。
- 新增 `mac/src/voice_assistant/adapters/cdp_player.py`：短连接 WebSocket，直接读写播放页 DOM。
  - `like()`：`.like` 点击 + 类名 `.on` 回读
  - `coin(n)`：`.coin` → 弹窗 → `.app-cionpanel--btn-sure`，回读计数/类名；额度用尽返回 `coin_limit`
  - `favorite(folder)`：`.collect` → `.app-collection--list-item` 按文件夹名点选 → `button.btn.submit-move`
  - `seek_to/seek_by`：`video.currentTime`（精确、即时）
  - `next_episode/prev_episode`：优先发送真实快捷键 **`>` / `<`**（Shift+. / Shift+,）并用 `page=`/`cid` 变化校验；DOM 里「多P上一P/下一P」按钮宽度为 0，`.click()` 不可靠
  - `danmaku(text)`：`Input.dispatchMouseEvent` 唤出控制栏 → `.bpx-player-dm-input` 写值 + Enter
  - `comments(open)`：切换简介/评论标签
  - `home()`：DOM 点击「回到主界面」——**实测无效**，真实导航仍靠 AX press（见 §5）
  - `available()/ensure(restart=True)`：检测端口；必要时优雅退出并带参数重启 B 站（会中断播放，必须由用户确认后调用）
- `player_ui.py` 所有动作改为 **CDP → 快捷键 → OCR → 像素** 的优先级链；CDP 不可用时自动回退。
- 宿主 `_launch()` 已带 `--remote-debugging-port=9222`。

### 2.4 全局提速

- **Seal 即开始 STT**：设备 Seal（音频收完）时后台启动转写，藏进 840ms 取消窗口；Commit 直接取结果，失败/取消回退。取消会 cancel 该任务。单测覆盖。
- 去掉命令路径上的网络调用：seek 不再取 `duration`（曾把 0.29s 拖到 8.2s）；上一集不再查合集接口；指定 BV 播放不再查标题。
- 真机耗时（动作层）：点赞 0.03s、快进 0.26–0.29s、评论区 0.34s、投币 0.6s、弹幕 1.4s、收藏 1.8s；搜索（含回主页+解析 62 卡片）2.4s。

### 2.5 ax_helper 扩展

`mac/scripts/ax_helper.swift` 新增：`type`（Unicode 输入）、`ocr`（Vision，返回文字+坐标）、`keyseq`（一次进程连按 N 次）、`set-window --name`（按标题定位窗口）、`press-dom`（按 AXDOMIdentifier/class 查找并 press）、probe 输出 `domid/classes`、`AXUIElementSetMessagingTimeout(2s)`。

## 3. 架构与文件地图

```
firmware/desk-mate-app/             ESP32-C6 固件（LVGL 8.4 / NimBLE 2.5）
  focus_ui.h                        第 3 屏 UI（状态/进度/result detail/反馈）
  src/bili_voice/{voice_fsm.c,transport.cpp,voice_controller.cpp,ble_session_impl.h,adpcm.c}
  src/media_ble/{ble_manager.cpp,ble_voice.cpp}
mac/src/voice_assistant/
  ble_bridge.py                     BLE 通道（ACK 批次、停滞重订阅、credit）
  remote_session.py                 BLE/WSS 共用会话（hello/begin/seal/commit/cancel/status/feedback）
  sessions.py                       StageStore/CommandService（staging、WAV、SHA）
  pipeline.py                       STT→意图→计划→执行→结果（result_detail/action_detail、early STT）
  intent.py / planner.py / contracts.py / executor.py
  adapters/bilibili.py              B 站适配器（search/play/pause/R2 动作接线）
  adapters/player_ui.py             CDP→快捷键→OCR→像素 的动作层与回读
  adapters/cdp_player.py            本地 CDP 客户端（本轮新增）
  adapters/macos.py                 系统音量/截图/打开应用
mac/scripts/ax_helper.swift         辅助程序源码（mac/bin/ax_helper 由 swiftc 编译，未入 Git）
mac/tests/unit/                     pytest（141 项）
tools/lv_sim/                       LVGL 模拟器与 CTest（11 项，含 FSM 时序、UI 14 状态）
docs/                               BLE_VOICE.md / STATUS.md / VOICE_COMPUTERS.md / 本文件
```

## 4. 运行、构建、测试

```bash
# 主机（必须在 Terminal 里启动，才能继承辅助功能权限）
./run.sh --ble-address 7A640D0F-7BAA-C88F-4CE8-2D09289414B0
# 或：open -a Terminal build/start-server.command（内容同上，tee 到 build/voice-ble-test.log）

# 单测
PYTHONPATH=mac/src .venv/bin/python -m pytest mac/tests/unit -q      # 141 passed

# 模拟器 / CTest
cmake --build build/sim -j4
SDL_VIDEODRIVER=dummy ctest --test-dir build/sim --output-on-failure   # 11/11

# 固件
arduino-cli compile --fqbn esp32:esp32:esp32c6:CDCOnBoot=cdc,FlashSize=16M,PartitionScheme=no_ota \
  --build-path build/ble-heal firmware/desk-mate-app
arduino-cli upload --fqbn ... --port /dev/cu.usbmodem1101 --input-dir build/ble-heal firmware/desk-mate-app

# 辅助程序（改 Swift 后必须重编）
swiftc -O -o mac/bin/ax_helper mac/scripts/ax_helper.swift

# 带 CDP 启动 B 站（宿主 _launch 已带；手动验证时）
open -b com.bilibili.bilibiliPC --args --remote-debugging-port=9222
curl -s http://127.0.0.1:9222/json | head
```

- `.venv` 是指向 `/Users/acache/AI_PROJ/bili-esp32-ctrl/.venv` 的符号链接，未入 Git。
- 健康检查：`curl -sk https://127.0.0.1:8765/healthz`（自签名）。返回能力集（16 项）与 BLE 阶段。
- 串口抓日志用 pyserial 且 DTR/RTS=false；直接 `arduino-cli monitor` 会复位 ESP32。
- USB 口现在是 `/dev/cu.usbmodem1101`（旧文档的 `101` 已失效）。

## 5. 已验证（真机，2026-11-03）与未验证

**已验证**
- 第 3 屏：命令成功后不再跳 OFFLINE/CHECKING；`SENDING→THINKING→DONE`。
- 点赞（像素回读）、投币（计数 631→632）、收藏默认、收藏到「房车」、评论区开关（OCR 回读）、快进（CDP 0.29s）、音量三种（回读 45）、弹幕（1.4s，输入框清空）、下一集（bvid 变更）、多P 上一集/下一集（page 2→3→2，`<`/`>` 快捷键）、搜索（回主页+62 卡片，2.4s）。
- 单测 141、CTest 11/11、FSM 13 场景、UI 14 状态文案。

**未验证 / 已知限制**
- CDP 不可用时（用户从 Dock 重启 B 站）会回退：点赞/上下集仍快（快捷键），投币/收藏/弹幕/快进退化到 OCR 7–20s，且依赖右侧面板可见。
- CDP 的 `home()` DOM 点击无效；`search` 走 CDP 无法把关键词带进路由（Vue 内部状态），所以 **回主页/搜索仍走 AX 助手**（可靠，2.4s）。
- 单P视频的下一集/上一集正确返回不可用（不是 bug）。
- 多P 路径只在 BV1hT4w68EQj 上验证过一次。
- 评论区「关闭」的验证只做了「简介/评论」标签切换，没有负向断言。

## 6. 已知风险与坑（不要回退）

1. **不要把命令路径改回公开 API**：已移除；`api.bilibili.com` 是公网请求，用户明确要求纯本地。
2. **CDP 会话每个动作新建连接**（约 5ms），不要改成全局长连接除非处理断线重连。
3. **`subprocess` 不要 capture 管道**：`open -b`/`screencapture` 会派生子进程继承 stdout 导致挂死（已修，见 `player_ui._run`）。
4. **点击前必须校验目标**：像素路径要 `panel_ready()`（读到点赞/投币计数）或 `bar_visible()`（OCR 到时间码）才允许点。
5. **收藏/投币弹窗以播放窗口居中**，坐标必须相对窗口/OCR 文本推导，不能写死。
6. **B 站单视频投币上限 2**：第三枚会弹「投币枚数已用完」，代码返回 `coin_limit`。
7. **`mac/bin/ax_helper` 未入 Git**，新 checkout 必须 `swiftc` 重编；辅助功能权限按 Terminal 授权。
8. **服务端必须从 Terminal 启动**，否则 caps 不含辅助功能（`caps=1` 而非 `caps=5`）。
9. **不要提交** `.venv`、`build/`、`mac/bin/`、用户 `Application Support` 数据、录音、证书。

## 7. 账号副作用（测试遗留）

为验证动作，测试视频（BV1tC4y1f7SD / BV1hT4w68EQj 等）已被：点赞、投 1–2 币（余额 31→28）、收藏到「房车」、发送过「测试一下/测试弹幕」、切换过 P1/P2。需要时用户可自行取消。

## 8. 下一阶段建议（按优先级）

1. **打包成 macOS App 的启动管理**：启动时检测 `9222` 端口 + `ps -o command=` 判断启动方式；未带参数时给出「重启 B 站以启用本地控制」按钮（调用 `cdp_player.ensure(restart=True)`，会中断播放，需确认）；`/healthz` 暴露 CDP 状态。
2. **常驻 helper**：`ax_helper serve` 已有雏形，可把 search/home/OCR 命令并入，省掉每次 1–1.5s 进程启动（现在只影响搜索/主页/OCR 兜底）。
3. **弹幕/评论的 CDP 兜底**：当前弹幕 CDP 依赖控制栏鼠标事件；若窗口隐藏需补 OCR 兜底（已有）。
4. **多P 更多样本验证**，以及「上一集」在合集视频上的回归（快捷键 `[`）。
5. **时延收尾**：取消窗口 840ms 是用户可操作时间，不建议再砍；如要再快，可做「seal 后 host 预热计划」但收益有限。

## 9. 本轮改动清单（git）

本轮提交包含：固件语音稳定性修复、R2 契约/意图/计划/能力集/设备文案、`player_ui.py`、`cdp_player.py`、`ax_helper.swift` 扩展、early STT、去网络化、141 项单测、11 项 CTest、模拟器 UI 状态测试、若干文档更新。详细逐文件见 `git show --stat HEAD`。
