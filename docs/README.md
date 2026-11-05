# 文档索引

> 更新：2026-11-05。三页固件与 Mac App 0.3.2 build 14 为当前交付基线；现状与待办统一查看 STATUS.md。

## 阅读顺序

| 文档 | 职责 |
|---|---|
| [项目总览与启动](../README.md) | 功能、安装、构建、烧录和排障 |
| [项目进展与迭代待办](STATUS.md) | 当前阶段、完成依据、能力边界和后续任务；待办统一在这里维护 |
| [系统规格](../spec/MASTER_SPEC.md) | 协议、状态机、动作契约、存储与实际实现边界 |
| [引脚与按键](../firmware/desk-mate-app/pinout.md) | GPIO、分页面按键行为及硬件注意事项 |
| [模拟器用法](../tools/lv_sim/README.md) | LVGL 9 交互预览、LVGL 8 渲染检查、测试与截图 |

## 功能文档

- [正式 BLE 语音桥接](BLE_VOICE.md)：共用媒体连接、凭据认证、BLE/Wi-Fi 选择与用户验收。

- [蓝牙连接管理用法](BLUETOOTH.md)：配对、设备切换、改名、忘记与重连策略。
- [BLE 语音数据实验与对端桥接](BLE_VOICE_LAB.md)：共用 HID 连接、吞吐/完整性测量、正常 Wi-Fi 路径与指令引擎分层。
- [已确认的蓝牙管理设计](superpowers/specs/2026-11-01-bluetooth-manager-design.md) 与 [实施计划](superpowers/plans/2026-11-01-bluetooth-manager.md)。

## 历史资料

| 文档 | 使用方式 |
|---|---|
| [v1 功能清单](archive/TODO-desk-mate-v1.md) | 2026-05-31 旧版页面记录；勾选不表示这些功能仍在当前固件中 |
| [v1 按键方案](archive/button-plan-desk-mate-v1.md) | 历史草稿，包含 GPIO 冲突与已废弃按键语义；禁止直接照抄代码 |
| 本地 `archive/bili-chat-log-2026-10-17.md` | 早期需求讨论原始导出，含无关任务与个人信息，由 .gitignore 排除，不提交、不复制全文 |

早期对话中与当前项目持续相关的要求：沿用已购微雪 ESP32-C6 AMOLED 三按键硬件；保留计时器与媒体页；语音作为第三页；物理按住说话、松开后留取消机会。当前实现与依据见进展文档和系统规格。

2026-10-17 原版设计稿未在当前仓库找到完整文件，现有 MASTER_SPEC 于 2026-10-25 从代码重建。旧会话可补充设计背景，不把“当前未找到原文件”写成无法再次核验的绝对丢失结论。

- [语音电脑管理](VOICE_COMPUTERS.md)：第 3 屏英文界面、最多 5 台 Mac、USB 追加授权、切换、网络与用户验收。
- [USB 授权与日常重连](USB_AUTHORIZATION.md)：何时需要 USB、重复添加复用记录、明确重新授权及避免 USB 控制线复位；[2026-11-05 实测记录](USB_AUTHORIZATION_VALIDATION_2026-11-05.md)。

- [2026-11-05 发布、权限及上下文统一约定](RELEASE_AND_CONTEXT_2026-11-05.md)：固定原发布路径、正常桌面应用、轻量权限重检，以及当前页面优先的直接/模型路由。

## 文件职责与验证入口

| 路径 | 内容 |
|---|---|
| `firmware/desk-mate-app/` | 唯一上板固件；页面、全局连接抽屉、调光及 BLE/语音实现 |
| `firmware/test/`、`tests/` | 固件协议/存储、计时、关机、调光回归与主机桩 |
| `tools/lv_sim/` | LVGL 8/9 模拟器、交互测试和实际 32KiB 内存池检查 |
| `mac/app/` | 原生 App 界面、权限、通知及版本源 `version.json` |
| `mac/src/voice_assistant/` | 服务、上下文/执行、BLE/USB、数据库与迁移 |
| `mac/scripts/` | 构建/固定路径更新、本机签名、helper 和明确触发的实测工具 |
| `mac/tests/` | 主机单元与协议/TLS 集成回归 |
| `spec/` | 当前技术契约；`docs/superpowers/` 保存带日期的方案及计划 |
| `.learnings/`、`AGENTS.md` | 排障经验与后续开发约定 |
| `build/`、`release/`、本机 Application Support | 忽略的构建、报告、模型或私有运行数据；不作为源码提交 |

本轮专题记录按功能保留，不移动固定 App 发布目录：

- [动画 A](ANIMATION_A_VALIDATION.md)、[动画 B 与全局连接页](CONNECTIONS_B_VALIDATION.md)、[界面与通知调整](UI_REFINEMENT_VALIDATION_2026-11-05.md)。
- [Mac 发布、权限与 B 站上下文执行](RELEASE_AND_CONTEXT_2026-11-05.md)。
- [USB 授权与重连验收](USB_AUTHORIZATION_VALIDATION_2026-11-05.md)。

自动回归：`VOICE_ASSISTANT_DISABLE_DISCOVERY=1 PYTHONPATH=mac/src .venv/bin/python -m pytest mac/tests -q`；原生/UI 回归：`cmake --build build/sim -j 4` 后运行 `ctest --test-dir build/sim --output-on-failure`，首次配置参见 [模拟器指南](../tools/lv_sim/README.md)。实板工具会操作设备或播放内容，应按各专题说明明确选择目标，不作为普通提交前检查自动执行。
