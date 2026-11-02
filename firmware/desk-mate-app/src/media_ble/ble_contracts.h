#ifndef DESKMATE_BLE_CONTRACTS_H
#define DESKMATE_BLE_CONTRACTS_H
#include <stdbool.h>
#include <stdint.h>
#define BLE_HOST_LIMIT 8
#define BLE_NAME_BYTES 21
#define BLE_NO_HANDLE UINT16_MAX
#define BLE_PAIR_MS 120000u
#define BLE_WAIT_MS 30000u
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { BLE_PAUSED, BLE_IDLE, BLE_WAITING, BLE_BACKGROUND, BLE_CONNECTING, BLE_READY, BLE_PAIRING, BLE_ERROR } BleState;
enum { BLE_NAME_DEFAULT, BLE_NAME_REMOTE, BLE_NAME_USER };
typedef struct { char address[18]; uint8_t type; char name[BLE_NAME_BYTES]; uint8_t name_kind; } BleHost;
typedef struct {
    BleState state;
    bool enabled, auto_reconnect, advertising, name_pending;
    uint8_t count, capacity;
    int8_t selected, current;
    uint16_t handle;
    uint32_t generation, window_id, remaining_ms, revision;
    BleHost hosts[BLE_HOST_LIMIT];
    char message[80];
} BleSnapshot;
typedef enum {
    BLE_CMD_CONNECT, BLE_CMD_DISCONNECT, BLE_CMD_PAIR, BLE_CMD_CANCEL,
    BLE_CMD_FORGET, BLE_CMD_RENAME, BLE_CMD_AUTO, BLE_CMD_ENABLE, BLE_CMD_REPORT
} BleOperation;
typedef struct {
    BleOperation op;
    char address[18]; uint8_t type;
    char name[BLE_NAME_BYTES];
    bool value;
    uint16_t usage;
    uint32_t generation;
} BleRequest;
typedef void (*BleReadSnapshot)(BleSnapshot *out);
typedef bool (*BleSubmitRequest)(const BleRequest *request);
static inline const char *ble_state_label(BleState state) {
    switch (state) {
    case BLE_PAUSED: return "Bluetooth paused";
    case BLE_IDLE: return "Disconnected";
    case BLE_WAITING: return "Waiting for host";
    case BLE_BACKGROUND: return "Auto reconnect";
    case BLE_CONNECTING: return "Securing connection";
    case BLE_READY: return "Ready to control";
    case BLE_PAIRING: return "Pairing mode";
    default: return "Connection error";
    }
}
#ifdef __cplusplus
}
#endif
#endif
