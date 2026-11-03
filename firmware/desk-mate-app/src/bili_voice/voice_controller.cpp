#include "voice_controller.h"

#include <esp_random.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <freertos/semphr.h>
#include <Preferences.h>

#include "audio_capture.h"
#include "input_adapter.h"
#include "pcm_ring.h"
#include "provisioning.h"
#include "transport.h"

static QueueHandle_t s_events = nullptr;
static QueueHandle_t s_actions = nullptr;
static pcm_ring_t s_ring;
static voice_fsm_t s_fsm;
static volatile int s_page = 0;
static volatile int s_owner_page = -1;
static volatile bool s_started = false;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static VoiceUiStatus s_ui = {};
static SemaphoreHandle_t s_fsm_lock;
static bool s_management=false,s_wait_release=false;
static unsigned s_management_users=0;
static int64_t s_input_fence=0,s_keys_up_at=0;
static bool fsm_busy() {
    return !(s_fsm.state==VOICE_STATE_READY || s_fsm.state==VOICE_STATE_OFFLINE ||
        s_fsm.state==VOICE_STATE_CONNECTING || s_fsm.state==VOICE_STATE_RESULT);
}

static void ui_publish() {
    VoiceUiStatus snapshot = {};
    const VoiceConfig *cfg = vc_config_peek();
    snapshot.state = s_fsm.state;
    snapshot.result = s_fsm.result;
    snapshot.code = s_fsm.code;
    snapshot.connected = s_fsm.connected;
    snapshot.ready = vc_transport_ready();
    snapshot.audio_ready = vc_audio_ready();
    snapshot.page = s_page;
    int64_t now = esp_timer_get_time();
    if (s_fsm.state == VOICE_STATE_RECORDING && s_fsm.down_us > 0)
        snapshot.recording_ms = (uint32_t)((now - s_fsm.down_us) / 1000);
    if (s_fsm.state == VOICE_STATE_CANCEL_WINDOW && s_fsm.cancel_deadline_us > now)
        snapshot.cancel_remaining_ms = (uint32_t)((s_fsm.cancel_deadline_us - now) / 1000);
    snapshot.pcm_frames = pcm_ring_used(&s_ring);
    snapshot.rms = vc_audio_last_rms();
    snprintf(snapshot.summary, sizeof(snapshot.summary), "%s", s_fsm.summary);
    snprintf(snapshot.transport, sizeof(snapshot.transport), "%s",
             cfg && !cfg->valid ? "UNPAIRED" : vc_transport_status());
    snprintf(snapshot.stage, sizeof(snapshot.stage), "%s", vc_transport_stage());
    snprintf(snapshot.command_id, sizeof(snapshot.command_id), "%s", s_fsm.command_id);
    static int last_logged_state = -1;
    if ((int)s_fsm.state != last_logged_state) {
        last_logged_state = (int)s_fsm.state;
        Serial.printf("VOICE: fsm t=%lu state=%d result=%d\n",
                      (unsigned long)millis(), (int)s_fsm.state, (int)s_fsm.result);
    }
    portENTER_CRITICAL(&s_mux);
    s_ui = snapshot;
    portEXIT_CRITICAL(&s_mux);
}

static void emit_action(void *ctx, const voice_action_t *action) {
    (void)ctx;
    if (!action) return;
    if (action->type == VOICE_ACT_AUDIO_START) {
        if (!vc_audio_start()) {
            voice_event_t ev = {};
            ev.type = VOICE_EV_ERROR;
            ev.code = 6;
            snprintf(ev.summary, sizeof(ev.summary), "microphone unavailable");
            xQueueSend(s_events, &ev, 0);
        }
        return;
    }
    if (action->type == VOICE_ACT_AUDIO_STOP) {
        vc_audio_stop();
        return;
    }
    if (action->type == VOICE_ACT_AUDIO_RESET) {
        vc_audio_reset();
        return;
    }
    if (xQueueSend(s_actions, action, pdMS_TO_TICKS(20)) != pdTRUE) {
        voice_event_t ev = {};
        ev.type = VOICE_EV_ERROR;
        ev.code = 7;
        snprintf(ev.summary, sizeof(ev.summary), "transport queue full");
        xQueueSend(s_events, &ev, 0);
    }
}

static void emit_audio(void *ctx, bool start) {
    (void)ctx;
    if (start) {
        if (!vc_audio_start()) {
            voice_event_t ev = {};
            ev.type = VOICE_EV_ERROR;
            ev.code = 6;
            snprintf(ev.summary, sizeof(ev.summary), "microphone unavailable");
            xQueueSend(s_events, &ev, 0);
        }
    } else {
        vc_audio_stop();
    }
}

static void uuid4_cb(void *ctx, char out[37]) {
    (void)ctx;
    uint8_t bytes[16];
    esp_fill_random(bytes, sizeof(bytes));
    voice_fsm_uuid4_format(bytes, out);
}

static void audio_error(int code, const char *summary) {
    voice_event_t ev = {};
    ev.type = VOICE_EV_ERROR;
    ev.code = code;
    snprintf(ev.summary, sizeof(ev.summary), "%s", summary ? summary : "audio error");
    xQueueSend(s_events, &ev, 0);
}

static bool should_forward_input(voice_event_type_t type) {
    if(s_management||s_wait_release)return false;
    if(vc_transport_pending()&&s_fsm.state==VOICE_STATE_READY&&(type==VOICE_EV_A_DOWN||type==VOICE_EV_B_DOWN))return false;
    bool is_down = type == VOICE_EV_A_DOWN || type == VOICE_EV_B_DOWN;
    bool is_up = type == VOICE_EV_A_UP || type == VOICE_EV_B_UP;
    if (is_down) {
        if (s_page == 2) {
            s_owner_page = 2;
            return true;
        }
        s_owner_page = -1;
        return false;
    }
    if (is_up) {
        bool owned = s_owner_page == 2;
        s_owner_page = -1;
        return owned;
    }
    return s_page == 2 || voice_fsm_is_capturing(&s_fsm);
}

static void controller_task(void *arg) {
    (void)arg;
    int64_t last_tick = esp_timer_get_time();
    bool announced = false;
    for (;;) {
        if (!announced) { announced = true; Serial.printf("VOICE: controller task running heap=%u\n", ESP.getFreeHeap()); }
        xSemaphoreTake(s_fsm_lock,portMAX_DELAY);
        if(s_wait_release&&!s_management){
            if(digitalRead(9)==HIGH&&digitalRead(10)==HIGH){
                if(!s_keys_up_at)s_keys_up_at=esp_timer_get_time();
                if(esp_timer_get_time()-s_keys_up_at>=30000){s_wait_release=false;s_input_fence=esp_timer_get_time();}
            }else s_keys_up_at=0;
        }
        voice_event_t ev;
        while (xQueueReceive(s_events, &ev, 0) == pdTRUE) {
            if(ev.type==VOICE_EV_HOST_CHANGED){
                voice_fsm_io_t io=s_fsm.io;voice_fsm_init(&s_fsm,&io);s_owner_page=-1;
                s_input_fence=esp_timer_get_time();continue;
            }
            if (ev.type == VOICE_EV_A_DOWN || ev.type == VOICE_EV_A_UP ||
                ev.type == VOICE_EV_B_DOWN || ev.type == VOICE_EV_B_UP) {
                if (ev.stable_us<=s_input_fence || !should_forward_input(ev.type)) continue;
            }
            if (ev.type == VOICE_EV_C_SHORT) {
                if (!(s_page == 2 || voice_fsm_is_capturing(&s_fsm))) continue;
            }
            bool unknown=s_fsm.state==VOICE_STATE_OUTCOME_UNKNOWN_ACK;
            voice_fsm_handle(&s_fsm, &ev, esp_timer_get_time());
            if(unknown&&s_fsm.state==VOICE_STATE_READY)vc_transport_ack_unknown();
        }
        int64_t now = esp_timer_get_time();
        if (now - last_tick >= 10000) {
            last_tick = now;
            bool unknown=s_fsm.state==VOICE_STATE_OUTCOME_UNKNOWN_ACK;
            voice_fsm_tick(&s_fsm, now);
            if(unknown&&s_fsm.state==VOICE_STATE_READY)vc_transport_ack_unknown();
        }
        ui_publish();
        xSemaphoreGive(s_fsm_lock);
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

static bool s_audio_preinited = false;

void vc_controller_preinit_audio() {
    pcm_ring_reset(&s_ring);
    s_audio_preinited = vc_audio_init(&s_ring, audio_error);
    Serial.printf("VOICE: audio preinit %d heap=%u\n", s_audio_preinited ? 1 : 0, ESP.getFreeHeap());
}

bool vc_controller_begin() {
    Serial.printf("VOICE: controller begin heap=%u\n", ESP.getFreeHeap());
    vc_config_load(nullptr);
    pcm_ring_reset(&s_ring);
    s_fsm_lock=xSemaphoreCreateMutex();
    if(!s_fsm_lock)return false;
    s_events = xQueueCreate(24, sizeof(voice_event_t));
    s_actions = xQueueCreate(12, sizeof(voice_action_t));
    if (!s_events || !s_actions) return false;
    voice_fsm_io_t io = {emit_action, emit_audio, uuid4_cb, nullptr};
    voice_fsm_init(&s_fsm, &io);
    if (!s_audio_preinited && !vc_audio_init(&s_ring, audio_error)) {
        // Bili is disabled, but the rest of Desk-Mate stays alive.
        Serial.println("VOICE: audio unavailable; existing pages unaffected");
    }
    vc_input_begin(s_events);
    Serial.printf("VOICE: input started heap=%u\n", ESP.getFreeHeap());
    vc_transport_begin(s_actions, s_events, &s_ring);
    Serial.printf("VOICE: transport started heap=%u\n", ESP.getFreeHeap());
    BaseType_t rc = xTaskCreate(controller_task, "voice_ctrl", 5120, nullptr, 4, nullptr);
    Serial.printf("VOICE: controller task rc=%d\n", (int)rc);
    s_started = true;
    Serial.println("VOICE: controller started");
    return true;
}

void vc_controller_set_page(int page) {
    if (page == s_page) return;
    s_page = page;
    if (page != 2) {
        bool capturing = voice_fsm_is_capturing(&s_fsm);
        bool in_commit = s_fsm.state == VOICE_STATE_COMMITTING ||
                         s_fsm.state == VOICE_STATE_WAITING ||
                         s_fsm.state == VOICE_STATE_QUERYING;
        bool replaying = s_fsm.state == VOICE_STATE_REPLAY_ARMED ||
                         s_fsm.state == VOICE_STATE_REPLAY_PREPARE;
        if (capturing || in_commit || replaying) {
            voice_event_t ev = {};
            ev.type = VOICE_EV_C_SHORT;
            ev.source = VOICE_INPUT_INTERNAL;
            int64_t now = esp_timer_get_time();
            ev.raw_us = now;
            ev.stable_us = now;
            xQueueSend(s_events, &ev, 0);
        }
    }
}

void vc_controller_pmic_short() {
    if (!s_started) return;
    if (!(s_page == 2 || voice_fsm_is_capturing(&s_fsm))) return;
    voice_event_t ev = {};
    ev.type = VOICE_EV_C_SHORT;
    ev.source = VOICE_INPUT_PMIC;
    int64_t now = esp_timer_get_time();
    ev.raw_us = now;
    ev.stable_us = now;
    xQueueSend(s_events, &ev, 0);
}

void vc_controller_feedback(const char *command_id, const char *rating,
                            const char *dismissed_by, int touch_count, int countdown_ms, const char *server_id) {
    if (!command_id || !command_id[0] || !rating || !rating[0]) return;
    voice_action_t a = {};
    a.type = VOICE_ACT_FEEDBACK;
    snprintf(a.command_id, sizeof(a.command_id), "%s", command_id);
    snprintf(a.summary, sizeof(a.summary), "%s|%s|%d|%d", rating,
             dismissed_by ? dismissed_by : "", touch_count, countdown_ms);
    // Durable one-slot queue: UI feedback must survive an offline WSS or reboot.
    Preferences prefs;
    if (prefs.begin("va_feedback", false)) {
        prefs.putString("srv",server_id&&server_id[0]?server_id:vc_config_peek()->server_id);
        prefs.putString("cmd", command_id);
        prefs.putString("rating", rating);
        prefs.putString("dismissed", dismissed_by ? dismissed_by : "");
        prefs.putInt("touches", touch_count);
        prefs.putInt("countdown", countdown_ms);
        prefs.end();
    }
    if (s_actions) xQueueSend(s_actions, &a, pdMS_TO_TICKS(20));
}

void vc_controller_read(VoiceUiStatus *out) {
    if (!out) return;
    portENTER_CRITICAL(&s_mux);
    *out = s_ui;
    portEXIT_CRITICAL(&s_mux);
}

bool vc_controller_active_input() {
    return voice_fsm_is_capturing(&s_fsm);
}

bool vc_controller_busy() {
    if(!s_fsm_lock)return false;
    if(xSemaphoreTake(s_fsm_lock,pdMS_TO_TICKS(20))!=pdTRUE)return true;
    bool busy=fsm_busy();xSemaphoreGive(s_fsm_lock);return busy;
}
bool vc_controller_management(bool open) {
    if(!s_fsm_lock)return false;
    if(xSemaphoreTake(s_fsm_lock,pdMS_TO_TICKS(20))!=pdTRUE)return false;
    if(open&&(fsm_busy()||vc_transport_pending()||digitalRead(9)==LOW||digitalRead(10)==LOW)){
        xSemaphoreGive(s_fsm_lock);return false;
    }
    if(open)s_management_users++;else if(s_management_users)s_management_users--;
    s_management=s_management_users>0;s_wait_release=true;s_keys_up_at=0;s_owner_page=-1;s_input_fence=esp_timer_get_time();
    xSemaphoreGive(s_fsm_lock);return true;
}
