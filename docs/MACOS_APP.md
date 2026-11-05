# 独立 macOS 服务端

## 使用范围

菜单栏 App 包含原生启动壳、独立 Python、运行依赖、AX helper、SenseVoiceSmall、Ollama 运行时及指定的本地决策模型。目标机不需要 Python、Swift、Homebrew、Ollama 或项目源码。当前构建及验证平台是 Apple Silicon；Intel 需要同架构运行时、依赖与单独验收。

本轮产物的原生依赖审计要求 **macOS 26.3 或更高**。构建脚本会根据所有原生依赖的最低部署版本更新 `Info.plist` 和 `bundle.json`，不能仅凭 App 壳的编译目标宣称支持旧系统。若需更早系统，必须换用兼容的运行时/依赖并重新验收。

应用运行于已登录用户的桌面会话。默认数据目录：`~/Library/Application Support/desk-mate`；默认服务端口 `18765`，私有 Ollama 端口 `11435`。它与原开发版的 `voice-assistant` 数据目录分离，首次启动生成身份与证书；不会复制开发机凭据。

## 首次使用

1. 将 `Desk-Mate.app` 放到固定位置（通常 `/Applications`）并启动。菜单栏展示加载和服务状态。
2. 在“权限与首次使用”中给安装后的 App 授予辅助功能、屏幕录制和蓝牙权限。需要时退出并重开 App。权限不能随安装包预授权；开发版依赖 Terminal 的方式不适用于最终验收。
3. 新设备首次使用时，用 USB 连接设备，选择“添加设备…”。已保存的同一 USB 板授权记录会直接复用，不会再次打开串口或换密钥；明确重新授权在高级设置中。见 [USB 授权与日常连接](USB_AUTHORIZATION.md)。
4. 在设备连接面板选择该 Mac，服务端使用“选择设备”选择实际输入。日常断线可用“重新连接”，只重建 BLE 通道，STT、Ollama 和服务保持运行，无需再次 USB 授权。
5. 点击“打开 B站并启用本地控制”：客户端未运行时直接带参数启动；已有本地控制连接时直接使用；正在运行但缺少参数时，只有明确确认后才重启。没有打开视频时会提示“已启用，请打开视频”。
6. 第3屏进行语音真机测试；STT/决策模型就绪和 BLE 认证成功、B站实际动作成功是不同的验收项。

菜单提供服务启停、诊断状态、日志文件夹、诊断 ZIP 导出和登录启动开关。ZIP 只包含有界日志、状态和构建元数据，不包含数据库、私钥或录音。日志中可能包含语音转写，分享前自行检查。

0.2.1 增加“断开蓝牙输入”，并显示当前输入设备和中文连接阶段。选择被接受不等于认证成功，界面会继续显示连接进度；完成认证后才提示连接成功。语音任务进行中会拒绝切换并提示稍后重试。USB 未授权、授权不匹配、设备未选中 Mac、权限不足、超时及 B站端口占用均有具体错误和处理建议；相同后台错误不会反复弹窗。

升级请先退出旧 App，再替换为新版本。已有 Application Support 数据和设备授权不随替换而清空。当前本机发布路径固定为 `build/usability-release/Desk-Mate.app`，版本 0.3.2（本轮 build 14）；不要使用日期目录副本。更新流程见 [发布与上下文约定](RELEASE_AND_CONTEXT_2026-11-05.md)。

## 构建

构建机需要 Swift 编译器、`codesign`、`otool`、`install_name_tool` 和 Python。所有输入都显式提供；脚本不下载模型，不读取或复制用户运行数据。

准备 CPython 3.11 独立分发（例如 python-build-standalone install-only），以及同架构、同 Python ABI 的依赖目录。在干净构建环境中根据 `mac/requirements-runtime.lock` 安装依赖，再将该环境的 site-packages 作为输入。提供完整 Ollama 原生分发（`ollama`、`lib/`、`LICENSE`）；Homebrew 包中的相对符号链接必须在构建输入中解引用。提供 SenseVoiceSmall 模型目录和指定 Ollama 模型仓库。

```bash
python3 mac/scripts/build_app.py \
  --python-runtime /path/to/standalone-python \
  --site-packages /path/to/build-env/lib/python3.11/site-packages \
  --ollama-runtime /path/to/ollama-runtime \
  --ollama-models /path/to/ollama-model-store \
  --stt-model /path/to/SenseVoiceSmall \
  --model qwen2.5:1.5b \
  --identity local \
  --output build/usability-release/Desk-Mate.app
```

构建过程：

- 校验运行依赖版本，只复制锁定分发的文件及许可证元数据。
- 编译原生壳和 AX helper；模型只包含指定 manifest 及其引用的 blobs，逐个验证 SHA-256。
- STT 模型不复制例子音频和图片；不复制 `.venv`、用户配置、数据库、录音、私钥或证书。
- 将外部原生库收进 bundle，重写加载路径，拒绝未解析或逃出 bundle 的依赖。
- 对修改过的二进制重新签名并进行严格签名检查，再用内置 Python 检查关键模块导入。

本机增量验证请用 `python3 mac/scripts/update_local_app.py`，在原路径事务式替换并自动递增 build；不重置权限或用户数据。暂存 `.app` 只用于构建与签名，禁止执行；原生检查只在正式路径进行。“重新检测”在运行中的主 App 内调用原生授权 API，不重启 STT/Ollama。普通状态刷新只读取原生 preflight，不触发授权提示。主进程实际观察结果记录于用户数据目录的 `permission-status.json`，命令行子进程的权限不能代替 App 权限。

本机增量更新默认使用固定的本地自签名证书，首次在 `~/Library/Application Support/desk-mate/signing` 创建，后续只复用同一身份。目录权限为 700，钥匙串及密码文件为 600，私钥以不可导出方式导入独立钥匙串；不进入项目或发布包，不更改系统信任或钥匙串搜索列表。丢失、损坏或不匹配时发布会中止，避免悄悄换身份。发布结果打印证书指纹与 designated requirement；同一证书的不同构建会互相验证该要求。显式 `--identity <证书>` 可覆盖默认值；全量本机构建请指定 `build_app.py --identity local`。

从之前的 ad-hoc 构建切换到该固定证书，需要用户在系统设置中给正式 `Desk-Mate.app` 重新关联一次授权。本机已在用户明确允许后完成这两项关联，并实测 build 9 → build 10 更新无需再次授权，主 App 两项原生 preflight 连续多个周期均为 true。旧 `.Desk-Mate-staging.app` 名称如仍出现在已打开的系统设置窗口中，退出并重开系统设置可刷新缓存；不要因此再次切换权限。应用不会读取旧记录冒充授权。

全量分发构建的默认 `--identity -` 仍是 ad-hoc 签名；本地自签名证书也不能替代对外正式交付需要的 Developer ID 签名、公证、staple 和另一台干净 Mac 的 Gatekeeper 验证。模型和第三方组件的许可证随包保留，正式发布前需核对完整分发许可。

## 决策评测

```bash
PYTHONPATH=mac/src .venv/bin/python -m voice_assistant.evaluation \
  --corpus mac/eval/intents-v1.json \
  --output build/eval-routed.json \
  --url http://127.0.0.1:11434

# 额外表达集；--mode model 可绕过规则以单测模型路径，仍保留策略校验
PYTHONPATH=mac/src .venv/bin/python -m voice_assistant.evaluation \
  --corpus mac/eval/intents-holdout-v1.json \
  --output build/eval-extra.json \
  --url http://127.0.0.1:11434
```

评测只生成提案和验证计划，不构造动作适配器、不执行播放器操作、不更改账号。报告包含逐条动作与参数、遗漏/多余动作、澄清结果、路由耗时，以及语料/提示词/模型摘要。规则与模型耗时分别报告，不能把混合中位数当作模型速度。当前不加载个人训练分类器，报告会明确注明。

## 开发验证隔离

原生壳支持 `VOICE_ASSISTANT_DATA_DIR` 和 `DESK_MATE_OLLAMA_PORT` 环境覆盖，用于隔离测试。也可使用内置 Python 的 `voice_assistant.app_runtime --resources ... --root ... --port ... --ollama-port ... serve` 验证后台生命周期。停止 supervisor 必须结束它自己启动的 Ollama、服务及 STT/AX 子进程；不会停止其他用户服务。

服务就绪并不代表权限、BLE 配对或 B 站动作已通过真机验收。当前模型的泛化率也必须依据真实评测记录，不能用规则用例全过代替。

可用 `mac/scripts/smoke_app.py --app <app路径> --root <全新测试目录> --output <报告路径>` 验证独立后台生命周期。该脚本使用独立端口，移除开发工具 PATH，不选择蓝牙设备、不调用播放器；覆盖模型加载、重复实例、后台恢复和退出清理。菜单结构可用 `Desk-Mate.app/Contents/MacOS/DeskMate --self-check` 检查，它立即退出且不启动服务。命令行自检不能替代实际菜单操作和系统权限验收。
