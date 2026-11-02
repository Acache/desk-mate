#include "transport.h"
#include "../media_ble/ble_voice.h"
#include "../media_ble/ble_voice_bindings.h"
#include "../media_ble/ble_manager.h"
#include <mbedtls/md.h>

#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <NetworkClientSecure.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WebServer.h>
#include "voice_hosts.h"
#include "voice_host_model.h"
#include "voice_controller.h"
#include <esp_heap_caps.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <mbedtls/base64.h>
#include <mbedtls/sha1.h>
#include <mbedtls/sha256.h>

#include "provisioning.h"

static QueueHandle_t s_actions = nullptr;
static QueueHandle_t s_events = nullptr;
static pcm_ring_t *s_ring = nullptr;
static NetworkClientSecure s_client;
static volatile bool s_connected = false;
static volatile bool s_ready = false;
static volatile uint32_t s_generation = 0;
static volatile bool s_pending = false;
static volatile bool s_ack_unknown=false;
void vc_transport_ack_unknown(){s_ack_unknown=true;}
static char s_status[64] = "OFFLINE";
static QueueHandle_t s_host_requests;
static VoiceHostCatalog s_hosts;
static VoiceHostView s_host_view;
static portMUX_TYPE s_host_mux=portMUX_INITIALIZER_UNLOCKED;
static bool s_ble_mode=true;
static bool s_paused=false,s_network_setup=false,s_switch_reset=false;
static uint32_t s_switch_until=0,s_retry_at=0,s_setup_until=0;
static char s_host_message[64];
static WebServer *s_setup_server;
static bool s_network_saved=false,s_wifi_force=true;
static void host_switch_finish(bool success);


static const char *WS_PATH = "/v1/remote";
static const char *WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

struct Pending {
    char command_id[37];
    char sha256[65];
    char server_id[37];
    bool exists;
};

struct TransportState {
    char command_id[37];
    char sha256[65];
    bool command_active;
    bool begin_sent;
    bool begin_ack;
    bool seal_pending;
    bool seal_sent;
    bool seal_ack;
    bool commit_sent;
    bool replay;
    uint32_t frame_count;
    uint32_t pcm_bytes;
    uint32_t last_ack_ms;
    uint32_t last_send_ms;
    uint32_t connect_ms;
    uint32_t ping_ms;
    uint32_t last_pong_ms;
    uint32_t generation;
    bool hello_done;
    bool hello_ready;
    Pending pending;
    mbedtls_sha256_context sha;
    bool sha_active;
    bool pending_query;
    bool pending_query_done;
};

static TransportState s_ts;
static bool send_doc(JsonDocument &doc);

/* One durable feedback slot. Preferences are used so a feedback tap survives
 * a reboot before the WSS link is available; the server is idempotent. */
static bool s_fb_pending = false;
static char s_fb_command[37] = {};
static char s_fb_server[37] = {};
static char s_fb_rating[16] = {};
static char s_fb_dismissed[24] = {};
static int s_fb_touches = 0;
static int s_fb_countdown = 0;

static void load_feedback_nvs() {
    Preferences prefs;
    if (!prefs.begin("va_feedback", true)) return;
    snprintf(s_fb_server,sizeof(s_fb_server),"%s",prefs.getString("srv",vc_config_peek()->server_id).c_str());
    String cmd = prefs.getString("cmd", "");
    String rating = prefs.getString("rating", "");
    String dismissed = prefs.getString("dismissed", "");
    int touches = prefs.getInt("touches", 0);
    int countdown = prefs.getInt("countdown", 0);
    prefs.end();
    if (cmd.length() == 36 && rating.length() && dismissed.length()) {
        snprintf(s_fb_command, sizeof(s_fb_command), "%s", cmd.c_str());
        snprintf(s_fb_rating, sizeof(s_fb_rating), "%s", rating.c_str());
        snprintf(s_fb_dismissed, sizeof(s_fb_dismissed), "%s", dismissed.c_str());
        s_fb_touches = touches; s_fb_countdown = countdown; s_fb_pending = true;
    }
}
static void save_feedback_nvs() {
    Preferences prefs;
    if (!prefs.begin("va_feedback", false)) return;
    prefs.putString("srv",s_fb_server);
    prefs.putString("cmd", s_fb_command);
    prefs.putString("rating", s_fb_rating);
    prefs.putString("dismissed", s_fb_dismissed);
    prefs.putInt("touches", s_fb_touches);
    prefs.putInt("countdown", s_fb_countdown);
    prefs.end();
}
static void clear_feedback_nvs() {
    Preferences prefs;
    if (prefs.begin("va_feedback", false)) { prefs.clear(); prefs.end(); }
    s_fb_pending = false;
}
static bool send_feedback_doc() {
    if (!s_fb_pending || !s_connected || !s_ts.hello_done || strcmp(s_fb_server,vc_config_peek()->server_id)) return false;
    JsonDocument doc;
    doc["v"] = 1;
    doc["type"] = "feedback";
    doc["msg_id"] = 8;
    doc["command_id"] = s_fb_command;
    doc["rating"] = s_fb_rating;
    doc["dismissed_by"] = s_fb_dismissed;
    doc["touch_count"] = s_fb_touches;
    doc["countdown_at_dismiss"] = s_fb_countdown;
    doc["page"] = 2;
    if (!send_doc(doc)) return false;
    return true;
}

static void set_status(const char *text) {
    snprintf(s_status, sizeof(s_status), "%s", text ? text : "");
}

static void post_event(voice_event_type_t type, voice_result_t result = VOICE_RESULT_NONE,
                       int code = 0, const char *summary = nullptr, bool replayable = false) {
    if (!s_events) return;
    voice_event_t ev = {};
    ev.type = type;
    ev.result = result;
    ev.code = code;
    ev.replayable = replayable;
    if (s_ts.command_id[0]) snprintf(ev.command_id, sizeof(ev.command_id), "%s", s_ts.command_id);
    if (s_ts.sha256[0]) snprintf(ev.sha256, sizeof(ev.sha256), "%s", s_ts.sha256);
    if (summary) snprintf(ev.summary, sizeof(ev.summary), "%s", summary);
    xQueueSend(s_events, &ev, 0);
}

static int64_t now_us() { return esp_timer_get_time(); }

static void uuid4(char out[37]) {
    uint8_t bytes[16];
    esp_fill_random(bytes, sizeof(bytes));
    voice_fsm_uuid4_format(bytes, out);
}

static bool ws_write_all(const uint8_t *data, size_t len, uint32_t timeout_ms = 1500) {
    size_t sent = 0;
    uint32_t start = millis();
    while (sent < len) {
        if (!s_client.connected()) return false;
        size_t n = s_client.write(data + sent, len - sent);
        if (n == 0) {
            if (millis() - start > timeout_ms) return false;
            delay(1);
            continue;
        }
        sent += n;
    }
    return true;
}

static bool ws_send_frame(uint8_t opcode, const uint8_t *payload, size_t len) {
    if(s_ble_mode)return (opcode==1||opcode==2)&&ble_voice_send(opcode,payload,len);
    static uint8_t frame[8 + VOICE_MAX_CONTROL_BYTES];
    if (len > VOICE_MAX_CONTROL_BYTES) return false;
    uint8_t header[14];
    size_t h = 0;
    header[h++] = 0x80 | (opcode & 0x0F);
    if (len <= 125) {
        header[h++] = 0x80 | (uint8_t)len;
    } else if (len <= 65535) {
        header[h++] = 0x80 | 126;
        header[h++] = (uint8_t)(len >> 8);
        header[h++] = (uint8_t)len;
    } else {
        return false;
    }
    uint8_t mask[4];
    esp_fill_random(mask, sizeof(mask));
    memcpy(header + h, mask, 4);
    h += 4;
    memcpy(frame, header, h);
    for (size_t i = 0; i < len; ++i) frame[h + i] = payload[i] ^ mask[i & 3];
    return ws_write_all(frame, h + len);
}

static bool ws_send_text(const char *text) {
    return ws_send_frame(0x1, (const uint8_t *)text, strlen(text));
}

static bool send_doc(JsonDocument &doc) {
    // Serialization is non-reentrant and owned by this transport task.
    static char buf[VOICE_MAX_CONTROL_BYTES + 1];
    size_t n = serializeJson(doc, buf, sizeof(buf));
    if (n == 0 || n >= sizeof(buf)) {
        Serial.printf("VOICE: send_doc serialize failed n=%u heap=%u\n", (unsigned)n, ESP.getFreeHeap());
        return false;
    }
    bool ok = ws_send_text(buf);
    if (!ok) Serial.printf("VOICE: send_doc write failed len=%u heap=%u\n", (unsigned)n, ESP.getFreeHeap());
    return ok;
}

static bool read_exact(uint8_t *buf, size_t len, uint32_t timeout_ms) {
    size_t got = 0;
    uint32_t start = millis();
    while (got < len) {
        int avail = s_client.available();
        if (avail > 0) {
            int n = s_client.read(buf + got, len - got);
            if (n > 0) { got += n; start = millis(); continue; }
        }
        if (!s_client.connected()) return false;
        if (millis() - start > timeout_ms) return false;
        delay(2);
    }
    return true;
}

static bool ws_handshake(const VoiceConfig &cfg, IPAddress ip) {
    uint8_t nonce[16];
    esp_fill_random(nonce, sizeof(nonce));
    char key[32];
    size_t key_len = 0;
    if (mbedtls_base64_encode((unsigned char *)key, sizeof(key), &key_len, nonce, sizeof(nonce)) != 0) return false;
    key[key_len] = 0;
    char request[1024];
    int req_len = snprintf(request, sizeof(request),
        "GET %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Authorization: Bearer %s\r\n"
        "X-Device-ID: %s\r\n\r\n",
        WS_PATH, cfg.tls_name, key, cfg.token, cfg.device_id);
    if (req_len <= 0 || req_len >= (int)sizeof(request)) {
        Serial.printf("VOICE: ws request overflow %d\n", req_len);
        return false;
    }
    if (!ws_write_all((const uint8_t *)request, (size_t)req_len, 4000)) {
        Serial.printf("VOICE: ws request write failed heap=%u\n", ESP.getFreeHeap());
        return false;
    }
    char response[1024];
    size_t used = 0;
    uint32_t start = millis();
    while (used < sizeof(response) - 1 && millis() - start < 5000) {
        int avail = s_client.available();
        if (avail <= 0) { delay(2); continue; }
        int n = s_client.read((uint8_t *)response + used, sizeof(response) - 1 - used);
        if (n <= 0) continue;
        used += n;
        response[used] = 0;
        if (strstr(response, "\r\n\r\n")) break;
    }
    response[used] = 0;
    if (!strstr(response, " 101 ")) {
        Serial.printf("VOICE: ws upgrade rejected heap=%u resp=%.80s\n", ESP.getFreeHeap(), response);
        return false;
    }
    char expect_src[128];
    snprintf(expect_src, sizeof(expect_src), "%s%s", key, WS_GUID);
    unsigned char digest[20];
    mbedtls_sha1((const unsigned char *)expect_src, strlen(expect_src), digest);
    char accept[64];
    size_t accept_len = 0;
    if (mbedtls_base64_encode((unsigned char *)accept, sizeof(accept), &accept_len, digest, sizeof(digest)) != 0) return false;
    accept[accept_len] = 0;
    if (!strstr(response, accept)) {
        Serial.printf("VOICE: ws accept mismatch heap=%u\n", ESP.getFreeHeap());
        return false;
    }
    Serial.printf("VOICE: ws upgraded heap=%u\n", ESP.getFreeHeap());
    return true;
}
static uint8_t s_ws_rx[3072];
static size_t s_ws_rx_len = 0;
static uint8_t s_ws_msg[VOICE_MAX_CONTROL_BYTES + 1];
static size_t s_ws_msg_len = 0;

static void reset_ws_rx() {
    s_ws_rx_len = 0;
    s_ws_msg_len = 0;
}

static bool connect_ws(const VoiceConfig &cfg) {
    IPAddress ip;
    const char *host = cfg.manual_host;
    if (host[0]) {
        IPAddress parsed;
        if (parsed.fromString(host)) ip = parsed;
        else ip = MDNS.queryHost(host, 2000);
    }
    int port = cfg.port;
    if ((uint32_t)ip == 0) {
        int n = MDNS.queryService("voice-assistant", "tcp");
        for (int i = 0; i < n; ++i) {
            String sid = MDNS.txt(i, "server_id");
            if (sid == cfg.server_id) {
                ip = MDNS.address(i);
                port = MDNS.port(i);
                if ((uint32_t)ip != 0) break;
            }
        }
    }
    if ((uint32_t)ip == 0) {
        ip = MDNS.queryHost(cfg.tls_name, 2000);
    }
    if ((uint32_t)ip == 0) {
        set_status("DISCOVERY_FAILED");
        return false;
    }
    set_status("CONNECTING");
    s_client.stop();
    // A fresh socket must never inherit a previous command's frame counter or
    // seal flags, otherwise queued audio is sent outside a begin.
    s_ts.command_active = false;
    s_ts.begin_ack = false;
    s_ts.begin_sent = false;
    s_ts.seal_pending = false;
    s_ts.seal_sent = false;
    s_ts.commit_sent = false;
    s_ts.hello_done = false;
    s_ts.hello_ready = false;
    s_client.setCACert(cfg.ca_pem);
    s_client.setHandshakeTimeout(10);
    s_client.setTimeout(3);
    Serial.printf("VOICE: TLS connect %s:%d host=%s heap=%u dma=%u dma_largest=%u\n",
                  ip.toString().c_str(), port, cfg.tls_name, ESP.getFreeHeap(),
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_DMA),
                  (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA));
    int rc = s_client.connect(ip, port, cfg.tls_name, cfg.ca_pem, nullptr, nullptr);
    if (rc != 1) {
        char err[160] = {0};
        s_client.lastError(err, sizeof(err));
        Serial.printf("VOICE: TLS connect failed rc=%d err=%s heap=%u dma=%u dma_largest=%u\n",
                      rc, err, ESP.getFreeHeap(),
                      (unsigned)heap_caps_get_free_size(MALLOC_CAP_DMA),
                      (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA));
        s_client.stop();
        set_status("TLS_RETRY");
        return false;
    }
    reset_ws_rx();
    if (!ws_handshake(cfg, ip)) {
        s_client.stop();
        set_status("TLS_ERROR");
        return false;
    }
    reset_ws_rx();
    s_generation++;
    s_ts.generation = s_generation;
    s_ts.hello_done = false;
    s_ts.hello_ready = false;
    s_connected = true;
    s_ready = false;
    set_status("CONNECTED");
    return true;
}

static void send_hello(const VoiceConfig &cfg) {
    JsonDocument doc;
    doc["v"] = 1;
    doc["type"] = "hello";
    doc["msg_id"] = 1;
    doc["device_id"] = cfg.device_id;
    doc["server_id"] = cfg.server_id;
    doc["firmware_version"] = "desk-mate-bili/1.0";
    char boot[37];
    Preferences prefs;
    if (prefs.begin("va_boot", false)) {
        String b = prefs.getString("boot", "");
        if (b.length() != 36) { uuid4(boot); prefs.putString("boot", boot); }
        else snprintf(boot, sizeof(boot), "%s", b.c_str());
        prefs.end();
    } else {
        uuid4(boot);
    }
    doc["boot_id"] = boot;
    send_doc(doc);
}

static void clear_pending() {
    Preferences prefs;
    if (prefs.begin("va_pending", false)) { prefs.clear(); prefs.end(); }
    memset(&s_ts.pending, 0, sizeof(s_ts.pending));
    s_pending = false;
}

static void load_pending() {
    memset(&s_ts.pending, 0, sizeof(s_ts.pending));
    Preferences prefs;
    if (!prefs.begin("va_pending", true)) return;
    String id = prefs.getString("cmd", "");
    String sha = prefs.getString("sha", "");
    String sid = prefs.getString("srv", "");
    prefs.end();
    if (id.length() == 36 && sha.length() == 64) {
        snprintf(s_ts.pending.command_id, sizeof(s_ts.pending.command_id), "%s", id.c_str());
        snprintf(s_ts.pending.sha256, sizeof(s_ts.pending.sha256), "%s", sha.c_str());
        snprintf(s_ts.pending.server_id, sizeof(s_ts.pending.server_id), "%s", sid.c_str());
        s_ts.pending.exists = true;
        s_pending = true;
    }
}

static void save_pending(const char *id, const char *sha, const char *sid) {
    Preferences prefs;
    if (!prefs.begin("va_pending", false)) return;
    prefs.putString("cmd", id);
    prefs.putString("sha", sha);
    prefs.putString("srv", sid);
    prefs.end();
    snprintf(s_ts.pending.command_id, sizeof(s_ts.pending.command_id), "%s", id);
    snprintf(s_ts.pending.sha256, sizeof(s_ts.pending.sha256), "%s", sha);
    snprintf(s_ts.pending.server_id, sizeof(s_ts.pending.server_id), "%s", sid);
    s_ts.pending.exists = true;
    s_pending = true;
}

static void start_sha() {
    if (s_ts.sha_active) mbedtls_sha256_free(&s_ts.sha);
    mbedtls_sha256_init(&s_ts.sha);
    mbedtls_sha256_starts(&s_ts.sha, 0);
    s_ts.sha_active = true;
}

static void finish_sha() {
    unsigned char digest[32];
    mbedtls_sha256_finish(&s_ts.sha, digest);
    mbedtls_sha256_free(&s_ts.sha);
    s_ts.sha_active = false;
    for (int i = 0; i < 32; ++i) snprintf(s_ts.sha256 + i * 2, 3, "%02x", digest[i]);
    s_ts.sha256[64] = 0;
}

static void handle_text(const char *text, size_t len);

static void send_begin(const voice_action_t &a) {
    snprintf(s_ts.command_id, sizeof(s_ts.command_id), "%s", a.command_id);
    s_ts.command_active = true;
    s_ts.begin_sent = true;
    s_ts.begin_ack = false;
    s_ts.seal_pending = false;
    s_ts.seal_sent = false;
    s_ts.seal_ack = false;
    s_ts.commit_sent = false;
    s_ts.frame_count = 0;
    s_ts.pcm_bytes = 0;
    s_ts.last_ack_ms = millis();
    s_ts.last_send_ms = millis();
    s_ts.replay = false;
    start_sha();
    JsonDocument doc;
    doc["v"] = 1;
    doc["type"] = "begin";
    doc["msg_id"] = 2;
    doc["command_id"] = a.command_id;
    doc["kind"] = "audio";
    JsonObject fmt = doc["format"].to<JsonObject>();
    fmt["encoding"] = "s16le";
    fmt["rate"] = 16000;
    fmt["channels"] = 1;
    doc["trigger"] = "esp32_ptt";
    if (!send_doc(doc)) {
        s_connected = false;
        s_client.stop();
    }
}

static void send_seal() {
    if (!s_ts.begin_ack || s_ts.seal_sent || (s_ble_mode&&!ble_voice_writable())) return;
    finish_sha();
    JsonDocument doc;
    doc["v"] = 1;
    doc["type"] = "seal";
    doc["msg_id"] = 3;
    doc["command_id"] = s_ts.command_id;
    doc["frame_count"] = s_ts.frame_count;
    doc["pcm_bytes"] = s_ts.pcm_bytes;
    doc["sha256"] = s_ts.sha256;
    s_ts.seal_sent = true;
    s_ts.seal_pending = false;
    if (!send_doc(doc)) {
        s_connected = false;
        s_client.stop();
    }
}

static void send_commit(bool replay) {
    if (!s_ts.command_id[0] || !s_ts.sha256[0] || s_ts.commit_sent || (!replay&&!s_ts.seal_ack)) return;
    if (!replay && s_ts.sha_active) finish_sha();
    const VoiceConfig *cfg = vc_config_peek();
    save_pending(s_ts.command_id, s_ts.sha256, cfg->server_id);
    JsonDocument doc;
    doc["v"] = 1;
    doc["type"] = "commit";
    doc["msg_id"] = 4;
    doc["command_id"] = s_ts.command_id;
    doc["sha256"] = s_ts.sha256;
    s_ts.commit_sent = true;
    if (!send_doc(doc)) {
        s_connected = false;
        s_client.stop();
    }
}

static void send_cancel(const char *reason) {
    JsonDocument doc;
    doc["v"] = 1;
    doc["type"] = "cancel";
    doc["msg_id"] = 5;
    doc["command_id"] = s_ts.command_id;
    doc["reason"] = reason ? reason : "user";
    if(!send_doc(doc)){s_connected=false;s_client.stop();}
}

static void send_feedback(const voice_action_t &a) {
    if(strcmp(a.command_id,s_fb_command))return;
    char work[sizeof(a.summary)];
    snprintf(work, sizeof(work), "%s", a.summary);
    char *rating = work;
    char *dismissed = strchr(work, '|');
    if (!dismissed) return;
    *dismissed++ = 0;
    char *touches = strchr(dismissed, '|');
    if (!touches) return;
    *touches++ = 0;
    char *countdown = strchr(touches, '|');
    if (!countdown) return;
    *countdown++ = 0;
    snprintf(s_fb_command, sizeof(s_fb_command), "%s", a.command_id);
    snprintf(s_fb_rating, sizeof(s_fb_rating), "%s", rating);
    snprintf(s_fb_dismissed, sizeof(s_fb_dismissed), "%s", dismissed);
    s_fb_touches = atoi(touches);
    s_fb_countdown = atoi(countdown);
    s_fb_pending = true;
    save_feedback_nvs();
    if (!send_feedback_doc()) {
        s_connected = false;
        s_client.stop();
    }
}

static void send_status() {
    if(s_ts.pending.exists&&strcmp(s_ts.pending.server_id,vc_config_peek()->server_id))return;
    JsonDocument doc;
    doc["v"] = 1;
    doc["type"] = "status";
    doc["msg_id"] = 6;
    const char *id = s_ts.pending.exists ? s_ts.pending.command_id : s_ts.command_id;
    doc["command_id"] = id;
    send_doc(doc);
}

static void send_replay_prepare(const voice_action_t &a) {
    snprintf(s_ts.command_id, sizeof(s_ts.command_id), "%s", a.command_id);
    s_ts.command_active = false;
    s_ts.replay = true;
    s_ts.seal_pending = false;
    s_ts.commit_sent = false;
    JsonDocument doc;
    doc["v"] = 1;
    doc["type"] = "replay_prepare";
    doc["msg_id"] = 7;
    doc["command_id"] = a.command_id;
    doc["kind"] = "replay";
    doc["replay_of"] = a.replay_of;
    send_doc(doc);
}

static void send_replay_commit(const voice_action_t &a) {
    snprintf(s_ts.command_id, sizeof(s_ts.command_id), "%s", a.command_id);
    snprintf(s_ts.sha256, sizeof(s_ts.sha256), "%s", a.sha256);
    send_commit(true);
}

static void drain_ring() {
    if (!s_ts.command_active || !s_ts.begin_ack || !s_ring || s_ts.seal_sent) return;
    // Audio arrives at 50 frames/s. Pace network writes to slightly faster
    // than real time instead of bursting the entire ring into TLS; this keeps
    // the server's receive/ack loop responsive and prevents PCM ring overflow.
    if (s_ts.last_send_ms != 0 && millis() - s_ts.last_send_ms < 10) return;
    if(s_ble_mode&&!ble_voice_writable())return;
    uint8_t frame[VOICE_FRAME_BYTES];
    uint16_t len = 0;
    int sent_this_round = 0;
    while (sent_this_round < 1 && pcm_ring_pop(s_ring, frame, &len)) {
        if (len == 0 || len > VOICE_FRAME_BYTES) return;
        uint8_t msg[8 + VOICE_FRAME_BYTES];
        msg[0] = (uint8_t)(s_ts.frame_count & 0xff);
        msg[1] = (uint8_t)((s_ts.frame_count >> 8) & 0xff);
        msg[2] = (uint8_t)((s_ts.frame_count >> 16) & 0xff);
        msg[3] = (uint8_t)((s_ts.frame_count >> 24) & 0xff);
        msg[4] = (uint8_t)(len & 0xff);
        msg[5] = (uint8_t)((len >> 8) & 0xff);
        msg[6] = 0;
        msg[7] = 0;
        memcpy(msg + 8, frame, len);
        if (!ws_send_frame(0x2, msg, 8 + len)) {
            Serial.printf("VOICE: audio frame send failed seq=%u len=%u heap=%u\n",
                          (unsigned)s_ts.frame_count, (unsigned)len, ESP.getFreeHeap());
            s_connected = false;
            s_client.stop();
            return;
        }
        if (s_ts.sha_active) mbedtls_sha256_update(&s_ts.sha, frame, len);
        s_ts.frame_count++;
        s_ts.pcm_bytes += len;
        s_ts.last_send_ms = millis();
        sent_this_round++;
        if (s_ts.frame_count > VOICE_MAX_FRAMES || s_ts.pcm_bytes > VOICE_MAX_PCM_BYTES) {
            post_event(VOICE_EV_ERROR, VOICE_RESULT_FAILED, 4, "audio overrun");
            s_ts.command_active = false;
            return;
        }
    }
    if (s_ts.seal_pending && pcm_ring_used(s_ring) == 0 && !s_ts.seal_sent) {
        send_seal();
    }
}

static void process_action(const voice_action_t &a) {
    if((a.type==VOICE_ACT_COMMIT||a.type==VOICE_ACT_REPLAY_COMMIT||a.type==VOICE_ACT_SEAL)&&strcmp(a.command_id,s_ts.command_id))return;
    switch (a.type) {
    case VOICE_ACT_BEGIN: send_begin(a); break;
    case VOICE_ACT_SEAL: s_ts.seal_pending = true; if (pcm_ring_used(s_ring) == 0) send_seal(); break;
    case VOICE_ACT_COMMIT: send_commit(false); break;
    case VOICE_ACT_CANCEL: {
        if (a.command_id[0]) snprintf(s_ts.command_id, sizeof(s_ts.command_id), "%s", a.command_id);
        if (s_ts.begin_sent || s_ts.command_active) send_cancel(a.reason == VOICE_REASON_TIMEOUT ? "timeout" :
            a.reason == VOICE_REASON_PAGE_EXIT ? "page_exit" :
            a.reason == VOICE_REASON_AUDIO_ERROR ? "audio_error" :
            a.reason == VOICE_REASON_NETWORK_ERROR ? "network_error" : "user");
        else post_event(VOICE_EV_CANCEL_ACK);
        s_ts.command_active = false;
        s_ts.begin_ack = false;
        s_ts.seal_pending = false;
        s_ts.seal_sent = false;
        break;
    }
    case VOICE_ACT_STATUS: send_status(); break;
    case VOICE_ACT_REPLAY_PREPARE: send_replay_prepare(a); break;
    case VOICE_ACT_REPLAY_COMMIT: send_replay_commit(a); break;
    case VOICE_ACT_FEEDBACK: send_feedback(a); break;
    default: break;
    }
}

static voice_result_t map_result(const char *state) {
    if (!state) return VOICE_RESULT_UNKNOWN;
    if (strcmp(state, "SUCCEEDED") == 0) return VOICE_RESULT_SUCCESS;
    if (strcmp(state, "PARTIAL") == 0) return VOICE_RESULT_PARTIAL;
    if (strcmp(state, "FAILED") == 0) return VOICE_RESULT_FAILED;
    if (strcmp(state, "CANCELED") == 0 || strcmp(state, "ABANDONED") == 0) return VOICE_RESULT_CANCELED;
    return VOICE_RESULT_UNKNOWN;
}

static void handle_result(JsonDocument &doc, bool from_status) {
    const char *id = doc["command_id"] | "";
    if (id[0] && s_ts.command_id[0] && strcmp(id, s_ts.command_id) != 0 &&
        !(s_ts.pending.exists && strcmp(id, s_ts.pending.command_id) == 0)) {
        return;
    }
    const char *state = doc["state"] | "";
    if (from_status) {
        JsonObject result = doc["result"].as<JsonObject>();
        if (result.isNull()) return;
        state = result["state"] | "";
    }
    voice_result_t r = map_result(state);
    const char *code = doc["code"] | "";
    const char *summary = doc["summary"] | "";
    bool replayable = doc["replayable"] | false;
    if (from_status) {
        JsonObject rr = doc["result"].as<JsonObject>();
        if (!rr.isNull()) {
            code = rr["code"] | "";
            summary = rr["summary"] | "";
            replayable = rr["replayable"] | false;
        }
    }
    if (r != VOICE_RESULT_UNKNOWN) {
        clear_pending();
        s_ts.pending_query_done = true;
        s_ts.command_active = false;
        s_ts.begin_sent = false;
        s_ts.begin_ack = false;
        s_ts.seal_pending = false;
        s_ts.seal_sent = false;
    }
    post_event(VOICE_EV_RESULT, r, code[0] ? 1 : 0, summary[0] ? summary : nullptr, replayable);
}

static void handle_text(const char *text, size_t len) {
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, text, len);
    if (err) return;
    const char *type = doc["type"] | "";
    if (strcmp(type, "hello_ack") == 0) {
        s_ts.hello_done = true;
        s_ts.hello_ready = doc["ready"] | false;
        s_ready = s_ts.hello_ready;
        if (s_ts.hello_ready) {
            if(s_hosts.switching)host_switch_finish(true);
            if(s_switch_reset){post_event(VOICE_EV_HOST_CHANGED);s_switch_reset=false;}
            set_status("READY");
            post_event(VOICE_EV_TRANSPORT_READY);
            if (s_fb_pending) send_feedback_doc();
            if (s_ts.pending.exists && !strcmp(s_ts.pending.server_id,vc_config_peek()->server_id)) {
                s_ts.pending_query = true;
                send_status();
            }
        } else {
            set_status((doc["code"] | "MODEL_UNAVAILABLE"));
        }
    } else if (strcmp(type, "begin_ack") == 0) {
        s_ts.begin_ack = true;
        s_ts.last_ack_ms = millis();
        post_event(VOICE_EV_BEGIN_ACK);
    } else if (strcmp(type, "append_ack") == 0) {
        s_ts.last_ack_ms = millis();
    } else if (strcmp(type, "seal_ack") == 0) {
        s_ts.seal_ack = true;
        const char *sha = doc["sha256"] | "";
        snprintf(s_ts.sha256, sizeof(s_ts.sha256), "%s", sha);
        post_event(VOICE_EV_SEAL_ACK);
    } else if (strcmp(type, "commit_ack") == 0) {
        post_event(VOICE_EV_COMMIT_ACK);
    } else if (strcmp(type, "feedback_ack") == 0) {
        const char *cid = doc["command_id"] | "";
        if (!cid[0] || strcmp(cid, s_fb_command) == 0) clear_feedback_nvs();
    } else if (strcmp(type, "cancel_ack") == 0) {
        clear_pending();
        post_event(VOICE_EV_CANCEL_ACK);
    } else if (strcmp(type, "replay_ready") == 0) {
        const char *sha = doc["sha256"] | "";
        snprintf(s_ts.sha256, sizeof(s_ts.sha256), "%s", sha);
        s_ts.replay = true;
        post_event(VOICE_EV_REPLAY_READY);
    } else if (strcmp(type, "result") == 0) {
        handle_result(doc, false);
    } else if (strcmp(type, "status_snapshot") == 0) {
        if (!(doc["known"] | false)) {
            clear_pending();
            return;
        }
        if (doc["terminal"] | false) handle_result(doc, true);
    } else if (strcmp(type, "error") == 0) {
        const char *code = doc["code"] | "PROTOCOL_ERROR";
        const char *summary = doc["summary"] | "server error";
        Serial.printf("VOICE: server error code=%s summary=%s heap=%u\n", code, summary, ESP.getFreeHeap());
        post_event(VOICE_EV_ERROR, VOICE_RESULT_FAILED, 1, summary);
        if (strcmp(code, "BUSY") == 0 || strcmp(code, "AUTH_FAILED") == 0) {
            s_connected = false;
            s_client.stop();
        }
    } else if (strcmp(type, "pong") == 0) {
        s_ts.last_pong_ms = millis();
    } else if (strcmp(type, "progress") == 0) {
        s_ts.last_pong_ms = millis();
    }
}

static void receive_ws() {
    int avail = s_client.available();
    if (avail <= 0) return;
    if (avail > (int)(sizeof(s_ws_rx) - s_ws_rx_len)) avail = (int)(sizeof(s_ws_rx) - s_ws_rx_len);
    int n = s_client.read(s_ws_rx + s_ws_rx_len, avail);
    if (n <= 0) return;
    s_ws_rx_len += n;
    size_t off = 0;
    while (s_ws_rx_len - off >= 2) {
        uint8_t b0 = s_ws_rx[off], b1 = s_ws_rx[off + 1];
        uint8_t opcode = b0 & 0x0F;
        bool masked = b1 & 0x80;
        uint64_t plen = b1 & 0x7F;
        size_t hlen = 2;
        if (plen == 126) {
            if (s_ws_rx_len - off < 4) break;
            plen = ((uint64_t)s_ws_rx[off + 2] << 8) | s_ws_rx[off + 3];
            hlen = 4;
        } else if (plen == 127) {
            if (s_ws_rx_len - off < 10) break;
            plen = 0;
            for (int i = 0; i < 8; ++i) plen = (plen << 8) | s_ws_rx[off + 2 + i];
            hlen = 10;
        }
        if (masked) hlen += 4;
        if (s_ws_rx_len - off < hlen + plen) break;
        const uint8_t *payload = s_ws_rx + off + hlen;
        if (opcode == 0x9) {
            ws_send_frame(0xA, payload, (size_t)plen);
        } else if (opcode == 0x8) {
            Serial.printf("VOICE: ws close frame heap=%u\n", ESP.getFreeHeap());
            s_connected = false;
            s_client.stop();
            return;
        } else if (opcode == 0x1 || opcode == 0x0) {
            if (s_ws_msg_len + plen > sizeof(s_ws_msg) - 1) { s_ws_msg_len = 0; }
            else {
                memcpy(s_ws_msg + s_ws_msg_len, payload, (size_t)plen);
                s_ws_msg_len += plen;
            }
            bool fin = b0 & 0x80;
            if (fin) {
                s_ws_msg[s_ws_msg_len] = 0;
                handle_text((const char *)s_ws_msg, s_ws_msg_len);
                s_ws_msg_len = 0;
            }
        }
        off += hlen + plen;
    }
    if (off > 0) {
        memmove(s_ws_rx, s_ws_rx + off, s_ws_rx_len - off);
        s_ws_rx_len -= off;
    }
}

static void host_disconnect() {
    if(s_ble_mode)ble_voice_reset();
    ble_voice_audio(false);xQueueReset(s_actions);pcm_ring_reset(s_ring);
    s_connected=s_ready=false;s_client.stop();reset_ws_rx();
    s_ts.hello_done=s_ts.hello_ready=false;
    post_event(VOICE_EV_TRANSPORT_LOST);
}
static void refresh_host_metadata() {
    VoiceHostCatalog stored;vc_hosts_catalog(&stored);
    memcpy(s_hosts.hosts,stored.hosts,sizeof(stored.hosts));s_hosts.preferred=stored.preferred;
    s_hosts.revision=stored.revision;
}
static void publish_host_view() {
    refresh_host_metadata();VoiceHostView v={};v.catalog=s_hosts;v.ble_mode=s_ble_mode;
    v.wifi=WiFi.status()==WL_CONNECTED;v.connected=s_connected;v.ready=s_ready&&!s_hosts.switching;
    v.paused=s_paused;v.network_setup=s_network_setup;
    v.command_busy=vc_controller_busy()||s_pending;
    snprintf(v.ssid,sizeof(v.ssid),"%s",vc_config_peek()->ssid);
    snprintf(v.status,sizeof(v.status),"%s",s_status);
    snprintf(v.message,sizeof(v.message),"%s",s_host_message);
    vc_hosts_added(v.added_id);v.provision_revision=vc_hosts_credentials_revision();
    portENTER_CRITICAL(&s_host_mux);s_host_view=v;portEXIT_CRITICAL(&s_host_mux);
}
static void host_switch_finish(bool success) {
    vh_finish(&s_hosts,success);
    if(success){if(s_ble_mode||vc_hosts_remember(s_hosts.active))s_host_message[0]=0;else snprintf(s_host_message,sizeof(s_host_message),"Connected; last target could not be saved");return;}
    host_disconnect();vc_config_activate(s_hosts.active);s_switch_reset=true;s_retry_at=millis();
    snprintf(s_host_message,sizeof(s_host_message),"Switch failed; previous target kept");
    set_status(s_hosts.active>=0?"RESTORING_PREVIOUS":"NO_COMPUTER");
}
static void stop_network_setup() {
    if(s_setup_server){s_setup_server->stop();delete s_setup_server;s_setup_server=nullptr;}
    s_network_setup=false;WiFi.softAPdisconnect(true);WiFi.disconnect();WiFi.mode(WIFI_STA);s_wifi_force=true;
    vc_config_activate(s_hosts.active);s_retry_at=millis();
}
static void start_network_setup() {
    host_disconnect();s_network_setup=true;s_setup_until=millis()+120000;s_network_saved=false;
    WiFi.mode(WIFI_AP_STA);WiFi.softAP("Desk-Mate-Voice","deskmate");
    s_setup_server=new WebServer(80);
    s_setup_server->on("/",HTTP_GET,[]{
        s_setup_server->send(200,"text/html",
            "<!doctype html><meta name='viewport' content='width=device-width'><title>Desk-Mate Wi-Fi</title>"
            "<h1>Connect Desk-Mate</h1><form method='post' action='/wifi'><label>2.4GHz Wi-Fi name "
            "<input name='ssid' maxlength='32' required></label><br><label>Password "
            "<input name='password' type='password' maxlength='64'></label><br><button>Save Wi-Fi</button></form>");
    });
    s_setup_server->on("/wifi",HTTP_POST,[]{
        String ssid=s_setup_server->arg("ssid"),password=s_setup_server->arg("password");
        if(!vc_hosts_network(ssid.c_str(),password.c_str())){s_setup_server->send(400,"text/plain","Invalid Wi-Fi settings or save failed");return;}
        s_setup_server->send(200,"text/plain","Saved. Desk-Mate will reconnect. Computer authorizations are kept.");s_network_saved=true;
    });
    s_setup_server->begin();set_status("WIFI_SETUP");
}
static void host_request(const VoiceHostRequest &r) {
    if(vc_controller_busy()||s_pending||s_hosts.switching){snprintf(s_host_message,sizeof(s_host_message),"Wait for the current command or connection");return;}
    refresh_host_metadata();int slot=vh_find(&s_hosts,r.id);
    s_host_message[0]=0;
    if(r.op==VH_TRANSPORT_BLE||r.op==VH_TRANSPORT_WIFI){
        if(s_network_setup)stop_network_setup();host_disconnect();
        s_ble_mode=r.op==VH_TRANSPORT_BLE;s_paused=false;ble_voice_enable(s_ble_mode);
        Preferences prefs;if(prefs.begin("va_link",false)){prefs.putBool("ble",s_ble_mode);prefs.end();}
        if(!s_ble_mode){VoiceHostCatalog stored;vc_hosts_catalog(&stored);s_hosts.active=s_hosts.target=stored.active;vc_config_activate(stored.active);s_wifi_force=true;}
        else {WiFi.disconnect();WiFi.mode(WIFI_OFF);ble_voice_reset();}
        s_switch_reset=true;return;
    }
    if(r.op==VH_SETUP_WIFI){if(!s_network_setup)start_network_setup();return;}
    if(r.op==VH_CANCEL_SETUP){if(s_network_setup)stop_network_setup();return;}
    if(r.op==VH_RETRY_WIFI){
        if(s_network_setup)stop_network_setup();host_disconnect();WiFi.disconnect();s_wifi_force=true;s_retry_at=millis();return;
    }
    if(slot<0){snprintf(s_host_message,sizeof(s_host_message),"Computer no longer saved");return;}
    if(r.op==VH_CONNECT&&s_ble_mode){
        BleSnapshot snap;ble_manager_read(&snap);bool found=false;
        for(int i=0;i<snap.count;i++){char bound[37];if(bv_binding_get(snap.hosts[i].address,snap.hosts[i].type,bound)&&!strcmp(bound,r.id)){
            BleRequest req={};req.op=BLE_CMD_CONNECT;strlcpy(req.address,snap.hosts[i].address,sizeof(req.address));req.type=snap.hosts[i].type;
            if(ble_manager_submit(&req)){host_disconnect();s_paused=false;s_switch_reset=true;found=true;}break;
        }}
        if(!found)snprintf(s_host_message,sizeof(s_host_message),"Connect this Mac on page 2; start its assistant");return;
    }
    if(r.op==VH_CONNECT){
        if(s_network_setup)stop_network_setup();
        if(!vh_start(&s_hosts,r.id,false))return;
        host_disconnect();s_paused=false;s_switch_reset=true;
        xQueueReset(s_actions);pcm_ring_reset(s_ring);s_ts.command_id[0]=0;s_ts.sha256[0]=0;
        if(!vc_config_activate(slot)){host_switch_finish(false);return;}
        s_switch_until=millis()+20000;s_retry_at=millis();set_status("SWITCHING");return;
    }
    if(r.op==VH_DISCONNECT){if(slot==s_hosts.active){host_disconnect();s_paused=true;set_status("PAUSED");}return;}
    if(r.op==VH_FORGET&&slot==s_hosts.active){host_disconnect();s_paused=true;s_switch_reset=true;post_event(VOICE_EV_HOST_CHANGED);}
    if(r.op==VH_FORGET)bv_binding_remove_server(r.id);
    if(!vc_hosts_edit(&r)){snprintf(s_host_message,sizeof(s_host_message),"Could not save computer settings");return;}
    if(r.op==VH_FORGET&&slot==s_hosts.active){s_hosts.active=s_hosts.target=-1;vc_config_activate(-1);set_status("NO_COMPUTER");}
    refresh_host_metadata();
}
static bool wifi_connect(const VoiceConfig &cfg) {
    if(WiFi.status()==WL_CONNECTED)return true;
    static uint32_t attempt_at=0;static bool started=false;static char attempted_ssid[33];
    if(!cfg.ssid[0]){set_status("WIFI_NOT_CONFIGURED");return false;}
    if(s_wifi_force||!started||strcmp(attempted_ssid,cfg.ssid)||millis()-attempt_at>=20000){
        s_wifi_force=false;snprintf(attempted_ssid,sizeof(attempted_ssid),"%s",cfg.ssid);
        attempt_at=millis();started=true;WiFi.mode(WIFI_STA);WiFi.setSleep(false);WiFi.begin(cfg.ssid,cfg.password);
    }
    set_status("WIFI_CONNECTING");return false;
}
static void service_session(){
    if(s_ts.hello_done){
        voice_action_t action;while(xQueueReceive(s_actions,&action,0)==pdTRUE){
            if(action.type==VOICE_ACT_FEEDBACK)load_feedback_nvs();
            process_action(action);
        }
        if(s_ts.seal_pending&&pcm_ring_used(s_ring)==0&&!s_ts.seal_sent)send_seal();
        drain_ring();
        if(millis()-s_ts.ping_ms>=5000){
            s_ts.ping_ms=millis();
            if(millis()-s_ts.last_pong_ms>16000)host_disconnect();
            else {JsonDocument ping;ping["v"]=1;ping["type"]="ping";ping["msg_id"]=100;send_doc(ping);}
        }
    }
    if(!s_connected){if(s_ble_mode)ble_voice_reset();s_ready=false;post_event(VOICE_EV_TRANSPORT_LOST);s_retry_at=millis()+1000;}
    if(!s_ts.hello_done&&millis()-s_ts.connect_ms>10000){host_disconnect();if(s_hosts.switching)host_switch_finish(false);}
}
#include "ble_session_impl.h"
static void transport_task(void *arg) {
    (void)arg;vc_hosts_catalog(&s_hosts);vc_config_activate(s_hosts.active);vc_time_apply();
    {Preferences prefs;if(prefs.begin("va_link",true)){s_ble_mode=prefs.getBool("ble",true);prefs.end();}}
    ble_voice_enable(s_ble_mode);
    load_pending();
    if(s_pending){int origin=vh_find(&s_hosts,s_ts.pending.server_id);
        if(origin>=0){s_hosts.active=s_hosts.target=origin;vc_config_activate(origin);}
        else {s_paused=true;set_status("PENDING_HOST_MISSING");}
    }
    uint32_t recovery_since=millis();bool recovery_notice=false;
    MDNS.begin("desk-mate-voice");
    uint32_t credential_revision=vc_hosts_credentials_revision();uint32_t published=0;
    for(;;) {
        if(s_ack_unknown){s_ack_unknown=false;clear_pending();s_host_message[0]=0;}
        if(s_pending&&!recovery_notice&&millis()-recovery_since>30000){
            recovery_notice=true;post_event(VOICE_EV_RESULT,VOICE_RESULT_UNKNOWN,0,"Check original Mac; KEY acknowledges");
            snprintf(s_host_message,sizeof(s_host_message),"Check original Mac; KEY acknowledges");
        }
        VoiceHostRequest request;while(xQueueReceive(s_host_requests,&request,0)==pdTRUE)host_request(request);
        if(credential_revision!=vc_hosts_credentials_revision()&&!s_hosts.switching&&!vc_controller_busy()&&!s_pending){
            credential_revision=vc_hosts_credentials_revision();refresh_host_metadata();
            VoiceHostCatalog stored;vc_hosts_catalog(&stored);
            char added[37];vc_hosts_added(added);
            if(s_hosts.active<0&&stored.active>=0){s_hosts.active=s_hosts.target=stored.active;s_paused=false;}
            if(s_hosts.active>=0&&(!vc_config_peek()->valid||!strcmp(added,s_hosts.hosts[s_hosts.active].id))){
                host_disconnect();vc_config_activate(s_hosts.active);s_switch_reset=true;s_retry_at=millis();
            }
        }
        if(s_network_setup){
            s_setup_server->handleClient();
            if(s_network_saved||(int32_t)(millis()-s_setup_until)>=0)stop_network_setup();
        }else if(s_ble_mode){
            poll_ble_session();if(s_connected)service_session();ble_voice_audio(s_connected&&s_ts.command_active&&!s_ts.seal_sent);
        }else {
            bool network_ready=wifi_connect(*vc_config_peek());
            if(!network_ready&&s_connected)host_disconnect();
            if(s_paused||!vc_config_peek()->valid){if(network_ready)set_status(s_hosts.active>=0?"PAUSED":"NO_COMPUTER");}
            else {
            if(s_hosts.switching&&(int32_t)(millis()-s_switch_until)>=0)host_switch_finish(false);
            if(!s_connected){
                if(network_ready&&(int32_t)(millis()-s_retry_at)>=0){
                    if(connect_ws(*vc_config_peek())){s_ts.last_pong_ms=millis();load_feedback_nvs();send_hello(*vc_config_peek());s_ts.connect_ms=millis();}
                    else {s_retry_at=millis()+3000;if(s_hosts.switching)host_switch_finish(false);}
                }
            }else {
                receive_ws();
                service_session();
            }
        }
        }
        if(millis()-published>=100){published=millis();publish_host_view();}
        static uint32_t stack_log_at=0;
        if(millis()-stack_log_at>=10000){stack_log_at=millis();Serial.printf("VOICE: stack_min=%u heap=%u generation=%lu\n",(unsigned)uxTaskGetStackHighWaterMark(nullptr),ESP.getFreeHeap(),(unsigned long)s_generation);}
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}
void vc_host_read(VoiceHostView *out){if(!out)return;portENTER_CRITICAL(&s_host_mux);*out=s_host_view;portEXIT_CRITICAL(&s_host_mux);}
bool vc_host_submit(const VoiceHostRequest *r){
    if(!r||!s_host_requests||vc_controller_busy()||s_pending)return false;
    return xQueueSend(s_host_requests,r,0)==pdTRUE;
}

bool vc_transport_begin(QueueHandle_t action_queue, QueueHandle_t event_queue, pcm_ring_t *ring) {
    s_host_requests=xQueueCreate(4,sizeof(VoiceHostRequest));
    if(!s_host_requests)return false;
    load_feedback_nvs();
    s_actions = action_queue;
    s_events = event_queue;
    s_ring = ring;
    xTaskCreate(transport_task, "voice_transport", 10240, nullptr, 6, nullptr);
    return true;
}

bool vc_transport_connected() { return s_connected; }
bool vc_transport_ready() { return s_ready; }
bool vc_transport_pending() { return s_pending; }
uint32_t vc_transport_generation() { return s_generation; }
const char *vc_transport_status() { return s_status; }
