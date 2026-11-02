#pragma once


#include "i2c_bsp.h"

void Custom_PmicRegisterInit(void);
void Custom_PmicPortInit(I2cMasterBus *i2cbus, uint8_t dev_addr);

void Axp2101_SetAldo2(uint8_t vol);
void Axp2101_SetAldo3(uint8_t vol);

/* 返回电池电量百分比 0-100，<0 表示读取失败。实现在 axp2101_bsp.cpp */
int PMU_GetBatteryPercent(void);
void PMU_EnableButton(void);
/* POWER_KEY_* flags from power_hold.h; one coherent, acknowledged snapshot. */
uint8_t PMU_PollButton(void);
