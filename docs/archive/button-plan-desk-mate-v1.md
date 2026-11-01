# 物理按键功能方案

> 微雪 ESP32-C6-Touch-AMOLED-2.16

## 硬件现状

| 按键 | 引脚 | 功能 |
|:---|:---:|:---|
| **BOOT** | GPIO0 (默认拉高，按下拉低) | 原生：进入下载模式 |
| **PWR** | AXP2101 PMIC 控制（非 GPIO） | 原生：长按关机，单击开机 |
| **KEY** | GPIO10 (从接口图确认) | 原生：未定义，可自定义 |

## 引脚确认（从微雪官方接口图确认）

| 按键 | GPIO | 类型 |
|:---|:---:|:---|
| **BOOT** | GPIO9 | 普通 GPIO（上拉输入） |
| **PWR** | GPIO18 → AXP2101 PEK | 电源管理芯片专用 |
| **KEY** | GPIO10 | 普通 GPIO（上拉输入） |

## 功能分配

### BOOT 按键 — 重启

ESP32-C6 的 BOOT 按键接 GPIO0。在代码中可监听 GPIO0 的下降沿中断：

```c
#define BOOT_BUTTON_GPIO  GPIO_NUM_0

static void boot_button_handler(void *arg) {
    // 防抖延时
    vTaskDelay(pdMS_TO_TICKS(50));
    if (gpio_get_level(BOOT_BUTTON_GPIO) == 0) {
        // 执行重启
        esp_restart();
    }
}

void init_boot_button() {
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << BOOT_BUTTON_GPIO),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .intr_type = GPIO_INTR_NEGEDGE,
    };
    gpio_config(&io_conf);
    gpio_install_isr_service(0);
    gpio_isr_handler_add(BOOT_BUTTON_GPIO, boot_button_handler, NULL);
}
```

### PWR 按键 — 断电/供电

PWR 按键连接到 AXP2101 电源管理芯片的 PEK 引脚。不能直接作为 GPIO 使用。需要通过 I2C 读写 AXP2101 寄存器来控制：

**关机（断电）：**
```c
#include <XPowersLib.h>

extern XPowersPMU axp2101;

void power_off() {
    // AXP2101 寄存器 0x10 bit0 = 1 → 关机
    uint8_t val;
    axp2101.readRegister(0x10, &val, 1);
    val |= 0x01;
    axp2101.writeRegister(0x10, &val, 1);
}
```

**开机**由硬件控制：在关机状态下短按 PWR 键，AXP2101 自动重新上电给 ESP32-C6，无需代码干预。

**注意：** AXP2101 的中断引脚（IRQ）可能连接到 ESP32-C6 的某个 GPIO，可以通过中断监听按键事件，但 PWR 按键的默认行为（长按关机、短按开机）已经由 AXP2101 硬件实现，代码里不需要额外处理。

### KEY 按键 — 预留（按住录音）

KEY 按键接 GPIO9（待原理图确认）。代码框架：

```c
#define KEY_BUTTON_GPIO GPIO_NUM_9  // 待确认

static void key_button_handler(void *arg) {
    vTaskDelay(pdMS_TO_TICKS(30));
    if (gpio_get_level(KEY_BUTTON_GPIO) == 0) {
        // 按下 → 开始录音
        start_recording();
    }
}

static void key_button_release_handler(void *arg) {
    vTaskDelay(pdMS_TO_TICKS(30));
    if (gpio_get_level(KEY_BUTTON_GPIO) == 1) {
        // 松开 → 停止录音
        stop_recording();
    }
}

void init_key_button() {
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << KEY_BUTTON_GPIO),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .intr_type = GPIO_INTR_ANYEDGE,  // 上升沿和下降沿都触发
    };
    gpio_config(&io_conf);
    gpio_isr_handler_add(KEY_BUTTON_GPIO, key_button_handler, NULL);
}
```

## 整合代码

在 `desk-mate-app.ino` 的 `setup()` 函数中调用：

```c
void setup() {
    // ... 现有初始化代码 ...

    init_boot_button();    // BOOT键 → 重启
    // PWR键：硬件级开关，无需代码
    // KEY键：后续添加录音功能时启用
}
```

## 依赖的库

| 功能 | 所需库 | 用途 |
|:---|:---|:---|
| 重启 | 无（`esp_restart()` 是 ESP-IDF 内置） | 软件复位 |
| 关机 | `XPowersLib`（已安装 0.3.3） | AXP2101 电源管理 |
| 按键中断 | 无（`gpio_isr_handler_add` 是 ESP-IDF 内置） | GPIO 中断 |

## 待确认事项

1. KEY 按键的确切 GPIO 编号——需要查阅原理图文档
2. AXP2101 的 IRQ 中断引脚是否接到 ESP32-C6，用来检测 PWR 按键事件
3. 17. 现有的 `user_config.h` 中是否有 GPIO 定义

---

由 AI 根据微雪官方文档、示例代码和小智项目代码整理。
