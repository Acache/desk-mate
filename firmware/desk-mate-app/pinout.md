# Desk-Mate 固件引脚定义

> 来源：微雪官方 ESP32-C6-Touch-AMOLED-2.16 接口图

## 三按键

| 按键 | GPIO | 说明 | 代码 |
|:---|:---:|:---|:---|
| **BOOT** | GPIO9 | 普通 GPIO，外接上拉，按下拉低 | 中断 → `esp_restart()` |
| **PWR** | — | 连接到 AXP2101 PEK 脚，非 GPIO | `axp2101.shutdown()` |
| **KEY** | GPIO10 | 普通 GPIO，外接上拉，按下拉低 | 中断 → 预留录音 |

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
| GPIO18 | PMIC PEK | AXP2101 |
| GPIO19 | I2S MCLK | ES8311/ES7210 |
| GPIO20 | I2S SCLK | ES8311/ES7210 |
| GPIO21 | I2S DIN | ES8311/ES7210 |
| GPIO22 | I2S LRCK | ES8311/ES7210 |
| GPIO23 | I2S DOUT | ES8311/ES7210 |

## 重要注意

- GPIO0/1/2 **共用**于 LCD QSPI 和 SD 卡 SPI，同一时间只能用一个
- GPIO7/8 是 I2C 总线，连接到 AXP2101(0x34)、QMI8658、PCF85063、ES8311、ES7210
- GPIO18 是 AXP2101 PEK，不能作为普通 GPIO 使用
- GPIO9 同时也是 ESP32-C6 的 BOOT 模式选择引脚（下载时需要拉低）
