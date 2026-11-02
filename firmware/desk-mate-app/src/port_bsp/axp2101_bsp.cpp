#include <stdio.h>
#include <freertos/FreeRTOS.h>
#include "axp2101_bsp.h"
#include "../../power_hold.h"
#define XPOWERS_CHIP_AXP2101
#include "XPowersLib.h"


static XPowersPMU axp2101;
static I2cMasterBus *i2cbus_ = NULL;
static i2c_master_dev_handle_t i2cPMICdev = NULL;
static uint8_t i2cPMICAddress;


static int AXP2101_SLAVE_Read(uint8_t devAddr, uint8_t regAddr, uint8_t *data, uint8_t len) {
  int ret;
  uint8_t count = 3;
  do {
    ret = (i2cbus_->i2c_read_buff(i2cPMICdev, regAddr, data, len) == ESP_OK) ? 0 : -1;
    if (ret == 0)
      break;
    vTaskDelay(pdMS_TO_TICKS(100));
    count--;
  } while (count);
  return ret;
}

static int AXP2101_SLAVE_Write(uint8_t devAddr, uint8_t regAddr, uint8_t *data, uint8_t len) {
  int ret;
  uint8_t count = 3;
  do {
    ret = (i2cbus_->i2c_write_buff(i2cPMICdev, regAddr, data, len) == ESP_OK) ? 0 : -1;
    if (ret == 0)
      break;
    vTaskDelay(pdMS_TO_TICKS(100));
    count--;
  } while (count);
  return ret;
}

void Custom_PmicPortInit(I2cMasterBus *i2cbus, uint8_t dev_addr) {
  if (i2cbus_ == NULL) {
    i2cbus_ = i2cbus;
  }
  if (i2cPMICdev == NULL) {
    i2c_master_bus_handle_t BusHandle = i2cbus_->Get_I2cBusHandle();
    i2c_device_config_t dev_cfg = {};
    dev_cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    dev_cfg.scl_speed_hz = 100000;
    dev_cfg.device_address = dev_addr;
    ESP_ERROR_CHECK(i2c_master_bus_add_device(BusHandle, &dev_cfg, &i2cPMICdev));
    i2cPMICAddress = dev_addr;
  }
  if (axp2101.begin(i2cPMICAddress, AXP2101_SLAVE_Read, AXP2101_SLAVE_Write)) {
    ESP_LOGI(TAG, "Init PMU SUCCESS!");
  } else {
    ESP_LOGE(TAG, "Init PMU FAILED!");
  }
  Custom_PmicRegisterInit();
}

void Custom_PmicRegisterInit(void) {
  axp2101.setVbusCurrentLimit(XPOWERS_AXP2101_VBUS_CUR_LIM_2000MA);

  if (axp2101.getDC1Voltage() != 3300) {
    axp2101.setDC1Voltage(3300);
    ESP_LOGW("axp2101_init_log", "Set DCDC1 to output 3V3");
  }
  if (axp2101.getALDO1Voltage() != 3300) {
    axp2101.setALDO1Voltage(3300);
    ESP_LOGW("axp2101_init_log", "Set ALDO1 to output 3V3");
  }
  if (axp2101.getALDO2Voltage() != 3300) {
    axp2101.setALDO2Voltage(3300);
    ESP_LOGW("axp2101_init_log", "Set ALDO2 to output 3V3");
  }
  if (axp2101.getALDO3Voltage() != 3300) {
    axp2101.setALDO3Voltage(3300);
    ESP_LOGW("axp2101_init_log", "Set ALDO3 to output 3V3");
  }
  if (axp2101.getALDO4Voltage() != 3300) {
    axp2101.setALDO4Voltage(3300);
    ESP_LOGW("axp2101_init_log", "Set ALDO4 to output 3V3");
  }

  axp2101.setPrechargeCurr(XPOWERS_AXP2101_PRECHARGE_50MA);
  axp2101.setChargerConstantCurr(XPOWERS_AXP2101_CHG_CUR_500MA);
  axp2101.setChargerTerminationCurr(XPOWERS_AXP2101_CHG_ITERM_50MA);
}

void Axp2101_SetAldo3(uint8_t vol) {
  if(vol) {
    axp2101.enableALDO3();
  } else {
    axp2101.disableALDO3();
  }
}

void PMU_EnableButton(void) {
  bool off_time_ok = axp2101.setPowerKeyPressOffTime(XPOWERS_POWEROFF_4S);
  axp2101.setLongPressPowerOFF();
  axp2101.enableLongPressShutdown();
  if (!off_time_ok || axp2101.getPowerKeyPressOffTime() != XPOWERS_POWEROFF_4S)
    ESP_LOGE("PWR", "Failed to configure 4s hardware shutdown");
  axp2101.clearIrqStatus();
  axp2101.enableIRQ(XPOWERS_AXP2101_PKEY_SHORT_IRQ |
      XPOWERS_AXP2101_PKEY_NEGATIVE_IRQ | XPOWERS_AXP2101_PKEY_POSITIVE_IRQ);
}

uint8_t PMU_PollButton(void) {
  // Read INTSTS2 directly: XPowers getIrqStatus converts failed reads to 0xFF.
  // Acknowledge only observed PKEY bits, never erase a new edge with clear-all.
  uint8_t status = 0;
  if (AXP2101_SLAVE_Read(i2cPMICAddress, XPOWERS_AXP2101_INTSTS2, &status, 1) != 0)
    return 0;
  uint8_t observed = status & 0x0B; // short, negative (down), positive (up)
  if (!observed) return 0;
  if (AXP2101_SLAVE_Write(i2cPMICAddress, XPOWERS_AXP2101_INTSTS2, &observed, 1) != 0)
    return 0;
  return ((observed & 0x02) ? POWER_KEY_DOWN : 0) |
         ((observed & 0x01) ? POWER_KEY_UP : 0) |
         ((observed & 0x08) ? POWER_KEY_SHORT : 0);
}

void Axp2101_SetAldo2(uint8_t vol) {
  if(vol) {
    axp2101.enableALDO2();
  } else {
    axp2101.disableALDO2();
  }
}

/* 返回电池百分比 0-100，<0 表示读取失败
 * 硬件：调 AXP2101 getBatteryPercent()，失败时回返 -1
 * 模拟器：走 stub（在 ui_pages.h 里，编译时 LV_USE_SDL 已定义） */
int PMU_GetBatteryPercent(void) {
#ifdef XPOWERS_CHIP_AXP2101
  int pct = axp2101.getBatteryPercent();
  if (pct < 0 || pct > 100) return -1;
  return pct;
#else
  return 85;   /* stub（实际不会被调用，ui_pages.h 已有同名 stub） */
#endif
}
