#pragma once
#include "src/media_ble/ble_manager.h"
/* Compatibility boundary: the sketch owns lifecycle; UI only queues reports. */
static bool media_ready() { return ble_manager_ready(); }
static void media_begin() { ble_manager_begin(); }
static bool media_send(uint16_t usage) { return ble_manager_send(usage); }
static void media_poll() { ble_manager_poll(); }
