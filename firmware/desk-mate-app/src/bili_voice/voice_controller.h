#ifndef DESKMATE_VOICE_CONTROLLER_H
#define DESKMATE_VOICE_CONTROLLER_H

#include <Arduino.h>

#include "voice_contracts.h"

struct VoiceUiStatus {
    voice_state_t state;
    voice_result_t result;
    int code;
    bool connected;
    bool ready;
    bool audio_ready;
    int page;
    uint32_t recording_ms;
    uint32_t cancel_remaining_ms;
    uint16_t pcm_frames;
    float rms;
    char summary[129];
    char transport[64];
    char stage[16];
    char command_id[37];
};

bool vc_controller_begin();
void vc_controller_preinit_audio();
void vc_controller_set_page(int page);
void vc_controller_pmic_short();
void vc_controller_feedback(const char *command_id, const char *rating,
                            const char *dismissed_by, int touch_count, int countdown_ms, const char *server_id = nullptr);
void vc_controller_read(VoiceUiStatus *out);
bool vc_controller_active_input();
bool vc_controller_busy();
bool vc_controller_management(bool open);

#endif
