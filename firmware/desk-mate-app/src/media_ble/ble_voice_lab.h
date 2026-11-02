#pragma once
#ifdef DESKMATE_BLE_LAB
#include <stdint.h>
class NimBLEServer;
bool ble_voice_lab_begin(NimBLEServer *server);
void ble_voice_lab_poll(uint16_t handle,uint32_t generation,bool ready);
void ble_voice_lab_disconnected(uint16_t handle);
#endif
