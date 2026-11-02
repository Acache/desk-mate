#include "provisioning.h"

#include <ArduinoJson.h>
#include <Preferences.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <ctype.h>
#include <stdarg.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>

#include "voice_contracts.h"
#include "voice_hosts.h"
#include "voice_controller.h"
#include "transport.h"
#include "../media_ble/ble_manager.h"

static const char *NVS_NS = "va_cfg";
static VoiceConfig g_cfg;
static bool g_loaded = false;
static String g_line;
static int g_bundle_remaining = 0;
static int g_bundle_index = 0;
static bool g_setup_mode = false;
static uint32_t g_setup_deadline_ms = 0;
static uint32_t g_last_bundle_rx_ms = 0;
static char *g_bundle = nullptr;
static const int kBundleMax = 16384 + 1;

static bool valid_uuid(const char *s) {
    if (!s || strlen(s) != 36) return false;
    for (int i = 0; i < 36; ++i) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (s[i] != '-') return false;
        } else if (!isxdigit((unsigned char)s[i])) {
            return false;
        }
    }
    return true;
}

static bool valid_token(const char *s) {
    if (!s || strlen(s) != 64) return false;
    for (int i = 0; i < 64; ++i) if (!isxdigit((unsigned char)s[i])) return false;
    return true;
}

static void copy_field(char *dst, size_t cap, const char *src) {
    if (!dst || cap == 0) return;
    if (!src) { dst[0] = 0; return; }
    strncpy(dst, src, cap - 1);
    dst[cap - 1] = 0;
}

void vc_config_load(VoiceConfig *out) {
    memset(&g_cfg, 0, sizeof(g_cfg));
    Preferences prefs;
    if (!prefs.begin(NVS_NS, true)) {
        g_loaded = true;
        if (out) *out = g_cfg;
        return;
    }
    copy_field(g_cfg.device_id, sizeof(g_cfg.device_id), prefs.getString("dev_id", "").c_str());
    copy_field(g_cfg.server_id, sizeof(g_cfg.server_id), prefs.getString("srv_id", "").c_str());
    copy_field(g_cfg.token, sizeof(g_cfg.token), prefs.getString("token", "").c_str());
    copy_field(g_cfg.tls_name, sizeof(g_cfg.tls_name), prefs.getString("tls", "").c_str());
    copy_field(g_cfg.ssid, sizeof(g_cfg.ssid), prefs.getString("ssid", "").c_str());
    copy_field(g_cfg.password, sizeof(g_cfg.password), prefs.getString("pass", "").c_str());
    copy_field(g_cfg.manual_host, sizeof(g_cfg.manual_host), prefs.getString("host", "").c_str());
    copy_field(g_cfg.ca_pem, sizeof(g_cfg.ca_pem), prefs.getString("ca", "").c_str());
    g_cfg.port = prefs.getUShort("port", 8765);
    g_cfg.trusted_time_unix_s = (int64_t)prefs.getLong64("time", 0);
    prefs.end();
    g_cfg.valid = valid_uuid(g_cfg.device_id) && valid_uuid(g_cfg.server_id) &&
                  valid_token(g_cfg.token) && strlen(g_cfg.tls_name) >= 6 &&
                  strstr(g_cfg.ca_pem, "BEGIN CERTIFICATE") != nullptr &&
                  g_cfg.port >= 1 && g_cfg.port <= 65535;
    g_loaded = true;
    if (out) *out = g_cfg;
}

bool vc_config_valid() {
    if (!g_loaded) vc_config_load(&g_cfg);
    return g_cfg.valid;
}
const VoiceConfig *vc_config_peek() {
    if (!g_loaded) vc_config_load(&g_cfg);
    return &g_cfg;
}

void vc_config_set_time(int64_t unix_s) {
    g_cfg.trusted_time_unix_s = unix_s;
}

void vc_time_apply() {
    if (!g_loaded) vc_config_load(&g_cfg);
    if (g_cfg.trusted_time_unix_s <= 1700000000LL) return;
    struct timeval tv;
    tv.tv_sec = (time_t)g_cfg.trusted_time_unix_s;
    tv.tv_usec = 0;
    settimeofday(&tv, nullptr);
}

static bool save_config(JsonDocument &doc) {
    JsonObject wifi = doc["wifi"].as<JsonObject>();
    JsonObject server = doc["server"].as<JsonObject>();
    if (wifi.isNull() || server.isNull()) return false;
    const char *ssid = wifi["ssid"] | "";
    const char *password = wifi["password"] | "";
    const char *manual = server["manual_host"] | "";
    const char *ca = server["ca_pem"] | "";
    const char *dev_id = doc["device_id"] | "";
    const char *srv_id = doc["server_id"] | "";
    const char *token = doc["device_token"] | "";
    const char *tls = server["tls_name"] | "";
    uint16_t port = (uint16_t)(server["port"] | 8765);
    int64_t trusted = (int64_t)(doc["trusted_time_unix_s"] | 0);
    if (!valid_uuid(dev_id) || !valid_uuid(srv_id) || !valid_token(token)) return false;
    if (strlen(tls) < 6 || strlen(tls) >= sizeof(g_cfg.tls_name)) return false;
    if (strlen(ca) < 64 || strlen(ca) >= sizeof(g_cfg.ca_pem)) return false;
    if (strstr(ca, "BEGIN CERTIFICATE") == nullptr) return false;
    if (strlen(ssid) >= sizeof(g_cfg.ssid) || strlen(password) >= sizeof(g_cfg.password)) return false;
    if (strlen(manual) >= sizeof(g_cfg.manual_host)) return false;
    if (port == 0) return false;
    if (trusted < 1700000000LL) return false;

    if (vc_controller_busy() || vc_transport_pending() || !vc_controller_management(true)) return false;
    VoiceConfig *candidate=(VoiceConfig *)calloc(1,sizeof(VoiceConfig));
    if(!candidate){vc_controller_management(false);return false;}
    copy_field(candidate->device_id,sizeof(candidate->device_id),dev_id);
    copy_field(candidate->server_id,sizeof(candidate->server_id),srv_id);
    copy_field(candidate->token,sizeof(candidate->token),token);
    copy_field(candidate->tls_name,sizeof(candidate->tls_name),tls);
    copy_field(candidate->manual_host,sizeof(candidate->manual_host),manual);
    copy_field(candidate->ca_pem,sizeof(candidate->ca_pem),ca);
    copy_field(candidate->ssid,sizeof(candidate->ssid),ssid);
    copy_field(candidate->password,sizeof(candidate->password),password);
    candidate->port=port;candidate->trusted_time_unix_s=trusted;candidate->valid=true;
    bool ok=vc_hosts_save(candidate,server["display_name"] | "");
    free(candidate);vc_controller_management(false);return ok;
}

static void send_line(const char *fmt, ...) {
    char buf[192];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    Serial.println(buf);
    Serial.flush();
}

static void release_bundle() {
    if (g_bundle) { free(g_bundle); g_bundle = nullptr; }
    g_bundle_remaining = 0;g_bundle_index=0;
}

static void handle_bundle() {
    if (!g_bundle) { send_line("VA_ERR BUNDLE"); return; }
    Serial.printf("VA: parsing bundle len=%u heap=%u\n", (unsigned)strlen(g_bundle), ESP.getFreeHeap());
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, g_bundle, strlen(g_bundle));
    if (err) { send_line("VA_ERR JSON"); release_bundle(); return; }
    if ((int)(doc["schema_version"] | 0) != 1) { send_line("VA_ERR SCHEMA"); release_bundle(); return; }
    if (!save_config(doc)) { send_line("VA_ERR CONFIG"); release_bundle(); return; }
    Serial.println("VA: config saved");
    vc_config_set_time((int64_t)(doc["trusted_time_unix_s"] | 0));
    vc_time_apply();
    send_line("VA_OK %s %s", doc["device_id"].as<const char *>(), doc["server_id"].as<const char *>());
    Serial.printf("VA: paired device=%s server=%s tls=%s\n", g_cfg.device_id, g_cfg.server_id, g_cfg.tls_name);
    release_bundle();
}

static void provisioning_task(void *arg) {
    (void)arg;
    for (;;) {
        vc_provisioning_poll();
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

void vc_provisioning_begin() {
    if (!g_loaded) vc_config_load(&g_cfg);
    vc_hosts_init();
    // Single serial reader. The UI loop can be delayed by LVGL rendering, so
    // bundle bytes must be drained by a dedicated high-priority task.
    xTaskCreate(provisioning_task, "voice_provision", 4096, nullptr, 5, nullptr);
}

void vc_provisioning_poll() {
    // USB CDC line-oriented setup: "VA_SETUP", then "VA_BUNDLE <len>" and
    // exactly <len> bytes of UTF-8 JSON. No secret is echoed.
    while (Serial.available() > 0) {
        int c = Serial.read();
        if (c < 0) break;
        if (g_bundle_remaining > 0) {
            g_last_bundle_rx_ms = millis();

            if (!g_bundle) { g_bundle_remaining = 0; g_setup_mode = false; continue; }
            g_bundle[g_bundle_index++] = (char)c;
            g_bundle[g_bundle_index] = 0;
            g_bundle_remaining--;
            if (g_bundle_remaining == 0) {
                handle_bundle();
                g_setup_mode = false;
                g_bundle_index = 0;
            }
            continue;
        }
        if (c == '\n' || c == '\r') {
            if (g_line.length() > 0) {
#ifdef DESKMATE_BLE_DIAGNOSTICS
                if (g_line == "ble pair" || g_line == "ble cancel" || g_line == "ble disconnect" || g_line.startsWith("ble connect ")) {
                    BleRequest request={};
                    request.op=g_line=="ble pair"?BLE_CMD_PAIR:g_line=="ble cancel"?BLE_CMD_CANCEL:BLE_CMD_DISCONNECT;
                    if(g_line.startsWith("ble connect ")) {
                        BleSnapshot view; ble_manager_read(&view);
                        int index=g_line.substring(12).toInt();
                        if(index<0||index>=view.count) { send_line("BLE: invalid host"); g_line=""; continue; }
                        request.op=BLE_CMD_CONNECT; request.type=view.hosts[index].type;
                        snprintf(request.address,sizeof(request.address),"%s",view.hosts[index].address);
                    }
                    send_line("BLE: bench queued=%d",ble_manager_submit(&request));
                } else
#endif
                if (g_line == "VA_BLE_CONNECT_SELECTED") {
                    BleSnapshot view;ble_manager_read(&view);int index=view.selected;
                    if(index<0||index>=view.count||vc_controller_busy()||vc_transport_pending())send_line("VA_BLE_BUSY");
                    else {BleRequest request={};request.op=BLE_CMD_CONNECT;request.type=view.hosts[index].type;
                        snprintf(request.address,sizeof(request.address),"%s",view.hosts[index].address);
                        send_line("VA_BLE_QUEUED %d",ble_manager_submit(&request));}
                    g_line="";continue;
                }
                if (g_line == "VA_SETUP") {
                    release_bundle();
                    g_setup_mode = true;
                    g_setup_deadline_ms = millis() + 120000;
                    g_line = "";
                    send_line("VA_SETUP_READY");
                    continue;
                }
                if (g_setup_mode && g_line.startsWith("VA_BUNDLE ")) {
                    int len = g_line.substring(10).toInt();
                    if (len > 0 && len <= kBundleMax - 1) {
                        release_bundle();
                        if (!g_bundle) g_bundle = (char *)malloc((size_t)len + 1);
                        if (!g_bundle) { send_line("VA_ERR NOMEM"); g_line = ""; continue; }
                        g_bundle_remaining = len;
                        g_bundle[0] = 0;
                        g_last_bundle_rx_ms = millis();
                        g_line = "";
                        Serial.printf("VA: bundle %d bytes\n", len);
                        continue;
                    }
                    send_line("VA_ERR LENGTH");
                }
            }
            g_line = "";
        } else if (g_line.length() < 200) {
            g_line += (char)c;
        }
    }
    if (g_bundle_remaining > 0 && (millis() - g_last_bundle_rx_ms) > 5000) {
        Serial.printf("VA: bundle incomplete remaining=%d got=%u\n", g_bundle_remaining,
                      (unsigned)(g_bundle ? strlen(g_bundle) : 0));
        send_line("VA_ERR BUNDLE_TIMEOUT");
        release_bundle();
    }
    if (g_setup_mode && (int32_t)(millis() - g_setup_deadline_ms) > 0) {
        g_setup_mode = false;
        release_bundle();
    }
}

bool vc_config_clear_voice() {
    Preferences prefs;
    if (!prefs.begin(NVS_NS, false)) return false;
    bool ok = prefs.clear();
    prefs.end();
    memset(&g_cfg, 0, sizeof(g_cfg));
    g_loaded = true;
    return ok;
}

bool vc_config_activate(int slot) {
    if(slot<0){g_cfg.valid=false;vc_hosts_read_network(g_cfg.ssid,g_cfg.password);return true;}
    VoiceConfig *next=(VoiceConfig *)malloc(sizeof(VoiceConfig));if(!next)return false;
    bool ok=vc_hosts_load(slot,next);if(ok)g_cfg=*next;free(next);return ok;
}
