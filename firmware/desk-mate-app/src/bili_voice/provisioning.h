#ifndef DESKMATE_VOICE_PROVISIONING_H
#define DESKMATE_VOICE_PROVISIONING_H

#include <Arduino.h>
#include <stdint.h>

struct VoiceConfig {
    char device_id[37];
    char server_id[37];
    char token[65];
    char tls_name[96];
    char ssid[33];
    char password[65];
    char manual_host[96];
    char ca_pem[4096];
    uint16_t port;
    int64_t trusted_time_unix_s;
    bool valid;
};

void vc_config_load(VoiceConfig *out);
bool vc_config_valid();
const VoiceConfig *vc_config_peek();
void vc_provisioning_begin();
void vc_provisioning_poll();
void vc_config_set_time(int64_t unix_s);
bool vc_config_clear_voice();
void vc_time_apply();
bool vc_config_activate(int slot);

#endif
