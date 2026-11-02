#pragma once
#include "ble_contracts.h"
bool ble_manager_begin();
void ble_manager_poll();
void ble_manager_read(BleSnapshot *out);
bool ble_manager_submit(const BleRequest *request);
bool ble_manager_send(uint16_t usage);
bool ble_manager_ready();
