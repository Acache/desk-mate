# Desk-Mate 固件引脚定义

> 更新：2026-11-01。板级引脚以 `user_config.h` 为准，应用按键以 `desk-mate-app.ino`、`focus_ui.h` 和 `src/bili_voice/input_adapter.cpp` 为准。历史按键草稿不作为实现依据。

## 三按键与当前行为

| 按键 | 接入 | 当前应用用途 |
|---|---|---|
| BOOT | GPIO9，按下拉低 | 按页面分配动作；运行时没有“单击即重启”的处理。上电/下载时仍是启动选择引脚 |
| KEY | GPIO10，按下拉低 | 计时器/媒体控制，语音页按住说话 |
| PWR | AXP2101 PEK，经 I2C 读取按下/松开/短按事件 | 短按执行当前页动作；全局长按 4 秒由 PMIC 断电，约 1 秒后显示 3 秒圆环提示 |

| 页面 | BOOT | KEY | PWR 短按 |
|---|---|---|---|
| Focus | 短按或长按重置 | 短按重新开始；长按显示 5/15/25 分钟预设，继续按住循环选择，松开确认 | 开始/暂停 |
| Mac | 短按上一曲；音量模式下减音量。长按切回 Focus | 短按下一曲；音量模式下加音量。长按切换媒体/音量模式 | 播放/暂停；音量模式下静音 |
| Voice | READY 且上次动作可重放时，第一次按下准备重放，第二次按下确认；UNKNOWN 时可确认返回 | 按住录音，少于 500ms 不上传；松开进入约 800ms 取消窗口，再按可取消 | 请求取消当前命令并返回 Focus；已经开始的外部动作不保证撤回 |

语音输入每 5ms 采样、20ms 消抖；为处理边界误差，资格判定为 530ms，最早提交为松开后 840ms。离开页面及跨页面松手有输入归属隔离，不能把语音按键透传为媒体键。当前 Voice 页的 TALK 是状态显示，录音入口是物理 KEY。

管理面板打开时：BOOT 上一项、KEY 下一项、PWR 选择，长按 BOOT 返回；关闭后恢复上表。改名键盘也可用三键选择与输入。详见 [蓝牙管理用法](../../docs/BLUETOOTH.md)。

## 全部可见 GPIO

| GPIO | 功能 | 组件 |
|:---:|:---|:---|
| GPIO0 | SPI CLK | LCD / SD 卡共用 |
| GPIO1 | SPI MOSI | LCD / SD 卡共用 |
| GPIO2 | SPI MISO | LCD / SD 卡共用 |
| GPIO3 | SPI DATA3 | LCD |
| GPIO4 | SPI DATA2 | LCD |
| GPIO5 | 触摸 INT | 触摸 (I2C 0x5A) |
| GPIO6 | SD CS | SD 卡 |
| GPIO7 | I2C SCL | 共享（PMIC/IMU/RTC/音频） |
| GPIO8 | I2C SDA | 共享（PMIC/IMU/RTC/音频） |
| GPIO9 | BOOT 按键 | 输入 |
| GPIO10 | KEY 按键 | 输入 |
| GPIO11 | 触摸 RST | 触摸 (I2C 0x5A) |
| GPIO15 | LCD CS | SH8601 |
| GPIO16 | IMU INT1 | QMI8658 |
| GPIO17 | IMU INT2 | QMI8658 |
| GPIO18 | 旧资料标注 PMIC 相关；当前应用未使用该 GPIO | 不等同于 PWR 按键输入 |
| GPIO19 | I2S MCLK | ES8311/ES7210 |
| GPIO20 | I2S SCLK | ES8311/ES7210 |
| GPIO21 | I2S DIN | ES8311/ES7210 |
| GPIO22 | I2S LRCK | ES8311/ES7210 |
| GPIO23 | I2S DOUT | ES8311/ES7210 |

## 重要注意

- GPIO0/1/2 **共用**于 LCD QSPI 和 SD 卡 SPI，同一时间只能用一个
- GPIO7/8 是 I2C 总线，连接到 AXP2101(0x34)、QMI8658、PCF85063、ES8311、ES7210
- PWR 的短按来自 PMIC IRQ 状态寄存器轮询；当前代码不通过 GPIO18 读取按键。GPIO18 的板级连线应另据原理图核对，不把 PEK 与 MCU GPIO 混为一谈
- GPIO9 同时也是 ESP32-C6 的 BOOT 模式选择引脚（下载时需要拉低）

## 全局 PWR 长按反馈

持续按住 4 秒关机：前约 1 秒过滤普通短按，随后浮窗圆环和 0s→3s 读秒同步填满。松手取消后从当前进度倒放（最长 2 秒），再用 1 秒渐隐；再次按住重新计时。取消长按不会执行页面短按动作。浮窗使用现有英文字体，提示 `HOLD 3s TO POWER OFF`；在所有页面和蓝牙管理层上方显示。

开机仍由 PMIC 硬件处理；AXP2101 开机阈值仅支持 128ms/512ms/1s/2s，不能配置成 4 秒，断电时屏幕无法显示动画。

第 3 屏点击顶部电脑条打开语音电脑管理：BOOT 上一项、KEY 下一项、PWR 确认，长按 BOOT 返回；改名键盘中 BOOT/KEY 选字符键、PWR 输入。管理层占用语音按键，关闭并松开全部按键后才恢复录音。语音电脑通过 Wi-Fi/WSS 管理，独立于第 2 屏蓝牙配对。详见 [语音电脑管理](../../docs/VOICE_COMPUTERS.md)。
