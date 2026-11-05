#ifndef DESKMATE_VOICE_HOSTS_H
#define DESKMATE_VOICE_HOSTS_H
#include <stdbool.h>
#include <stdint.h>
#define VH_CAPACITY 5
#define VH_NAME_BYTES 33

typedef struct { bool present; char id[37]; char name[VH_NAME_BYTES]; } VoiceHost;
typedef struct {
    VoiceHost hosts[VH_CAPACITY];
    int active, target, preferred, previous;
    uint32_t revision;
    bool switching;
} VoiceHostCatalog;
typedef struct {
    VoiceHostCatalog catalog;
    bool ble_mode;
    bool wifi, connected, ready, paused, network_setup, command_busy;
    char ssid[33], status[48], message[64];
    char added_id[37];
    uint32_t provision_revision;
} VoiceHostView;
typedef enum { VH_CONNECT=1, VH_DISCONNECT, VH_DEFAULT, VH_RENAME, VH_FORGET,
    VH_RETRY_WIFI, VH_SETUP_WIFI, VH_CANCEL_SETUP, VH_TRANSPORT_BLE, VH_TRANSPORT_WIFI } VoiceHostOperation;
typedef struct { VoiceHostOperation op; char id[37], name[VH_NAME_BYTES]; } VoiceHostRequest;
typedef void (*VoiceHostRead)(VoiceHostView *);
typedef bool (*VoiceHostSubmit)(const VoiceHostRequest *);
typedef bool (*VoiceHostGate)(bool open);
#ifdef __cplusplus
struct VoiceConfig;
void vc_hosts_init();
void vc_hosts_catalog(VoiceHostCatalog *out);
bool vc_hosts_load(int slot, VoiceConfig *out);
bool vc_hosts_save(const VoiceConfig *cfg, const char *name);
bool vc_hosts_binding_pending(int slot, const char *expected_device_id);
/* Called only with the result of the complete HMAC proof. Validates the
 * candidate still matches NVS before consuming its one-use USB binding grant. */
bool vc_hosts_finalize_binding(int slot, const VoiceConfig *expected,
                             const char *peer_address, uint8_t peer_type,
                             bool complete_proof_verified);
bool vc_hosts_edit(const VoiceHostRequest *request);
bool vc_hosts_remember(int slot);
void vc_hosts_added(char out[37]);
uint32_t vc_hosts_credentials_revision();
bool vc_hosts_network(const char *ssid, const char *password);
void vc_hosts_read_network(char ssid[33],char password[65]);
void vc_host_read(VoiceHostView *out);
bool vc_host_submit(const VoiceHostRequest *request);
#endif
#endif
