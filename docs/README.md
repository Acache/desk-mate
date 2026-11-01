# docs/ — 文档索引

## 当前有效（以代码为准）

| 文档 | 内容 |
|---|---|
| [`../README.md`](../README.md) | 项目总览、三页功能、构建/烧录/运行、路线图 |
| [`../spec/MASTER_SPEC.md`](../spec/MASTER_SPEC.md) | 系统规格：设备↔主机协议、状态机、18 个动作、SQLite 表、已知缺口（§12）、开放问题（§13） |
| [`../firmware/desk-mate-app/pinout.md`](../firmware/desk-mate-app/pinout.md) | 引脚与三个按键 |
| [`../tools/lv_sim/README.md`](../tools/lv_sim/README.md) | LVGL 9 模拟器用法 |

## 归档（历史资料，不代表当前实现）

| 文件 | 说明 |
|---|---|
| [`archive/TODO-desk-mate-v1.md`](archive/TODO-desk-mate-v1.md) | 2026-05-31 的 v1 七项功能清单，全部标了 ✅。它描述的是已经下线的 v1 页面（桌宠/额度/ACP/蓝牙页），**不要当作现状**；当前待办看 `spec/MASTER_SPEC.md` §12–13 和 README 的 Roadmap |
| [`archive/button-plan-desk-mate-v1.md`](archive/button-plan-desk-mate-v1.md) | 同时期的按键方案草稿。文中 GPIO0/GPIO9 自相矛盾，实际行为以 `firmware/desk-mate-app/desk-mate-app.ino` 为准（BOOT=GPIO9 重启、KEY=GPIO10、PWR=AXP2101 PEK） |
| `archive/bili-chat-log-2026-10-17.md` | bili 项目 2026-10-15 ~ 10-17 的完整对话导出（2,622 行），包含语音控制方案的最早讨论、MVP 取舍、三页/按键语义等决策。**只在本地保留，未进 git**（含个人信息与无关任务，规则见 `.gitignore`） |

## 已经丢失的

- **2026-10-17 写的原版 `MASTER_SPEC.md`（38KB）**：写在一个临时工作目录（`~/Downloads/voice-assistant-design/`），没有落盘到项目里，已无法恢复。现在 `spec/MASTER_SPEC.md` 是 2026-10-25 依据代码逐条重建的版本；早期的设计取舍只能从上面的 chat log 回溯。
- bili 的 `docs/` 目录一直是空的，没有其它设计稿。
