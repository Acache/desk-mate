#ifndef DESKMATE_FOCUS_UI_H
#define DESKMATE_FOCUS_UI_H
#include "lvgl.h"
#include "ui_update.h"
#include "focus_model.h"
#include "power_ui.h"
#include "voice_hosts_ui.h"
#include "ble_ui.h"
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <math.h>

LV_FONT_DECLARE(focus_font_120);

typedef bool (*FocusSendMedia)(uint16_t usage);
typedef int (*FocusReadBattery)(void);

/* Plain snapshot of the voice page. Kept Arduino-free so the LVGL simulator
 * can include this header unchanged. */
typedef struct {
    int state;
    int result;
    int code;
    int connected;
    int ready;
    int audio_ready;
    uint32_t recording_ms;
    uint32_t cancel_remaining_ms;
    uint16_t pcm_frames;
    float rms;
    char summary[129];
    char transport[64];
    char stage[16];
    char command_id[37];
} FocusBiliStatus;
typedef void (*FocusReadBili)(FocusBiliStatus *out);
static FocusTimer focus_timer;
static FocusSendMedia focus_send_media;
static FocusReadBattery focus_read_battery;
static FocusReadBili focus_read_bili;
static lv_obj_t *focus_pager, *focus_pages[3], *focus_arcs[2];
static lv_obj_t *focus_state, *focus_minute_digits, *focus_colon, *focus_second_digits;
static lv_obj_t *focus_caption, *focus_control_hint;
static lv_obj_t *focus_wheel_prev[2], *focus_wheel_next[2];
static lv_obj_t *focus_preset_overlay, *focus_preset_values[3];
static lv_obj_t *focus_media_timer;
static lv_obj_t *focus_mac_title, *focus_mac_status, *focus_mac_hint;
static lv_obj_t *focus_mac_mode, *focus_mac_left, *focus_mac_right;
static lv_obj_t *focus_dots[3], *focus_battery;
static lv_obj_t *focus_bili_title, *focus_bili_state, *focus_bili_timer, *focus_bili_hint;
static lv_obj_t *focus_bili_voice_disc,*focus_bili_cancel_track,*focus_bili_cancel_fill,*focus_bili_footer;
static char focus_feedback_origin[37];
static float focus_wave_history[21],focus_wave_level;
static uint32_t focus_wave_at;
static void focus_voice_build(void);
static void focus_voice_visibility(bool open);
#define FOCUS_WAVE_BARS 21
static lv_obj_t *focus_bili_wave[FOCUS_WAVE_BARS];
static lv_obj_t *focus_bili_result, *focus_bili_mac, *focus_bili_dot;
static lv_obj_t *focus_feedback_overlay, *focus_feedback_count, *focus_feedback_title, *focus_feedback_detail;
static lv_obj_t *focus_feedback_good, *focus_feedback_bad, *focus_feedback_badge;
static bool focus_feedback_visible, focus_feedback_selected, focus_feedback_held;
static uint32_t focus_feedback_deadline, focus_feedback_badge_until;
static uint32_t focus_feedback_result_tick;
static uint8_t focus_feedback_touches;
static char focus_feedback_command[37], focus_feedback_last_command[37];
static char focus_feedback_detail_text[41];
typedef void (*FocusFeedbackSink)(const char *, const char *, const char *, int, int);
static FocusFeedbackSink focus_feedback_sink;
static int focus_page;
static bool focus_connected, focus_volume;
static uint32_t focus_feedback_until, focus_dots_until, focus_next_battery_at, focus_hint_until;
static uint32_t focus_last_seconds = UINT32_MAX;
static bool focus_last_running, focus_last_finished;
static const uint32_t FOCUS_INK = 0xF3EFE7, FOCUS_MUTED = 0x858883;
static const uint32_t FOCUS_AMBER = 0xE8B66D, FOCUS_BLUE = 0x8CBCE5;
#define PAGE_FOCUS 0
#define PAGE_MAC 1
#define PAGE_BILI 2
#define PAGE_COUNT 3
static int focus_wheel_field = -1, focus_wheel_applied, focus_preset_index;
static lv_point_t focus_wheel_start;

static void conn_open(void);
static bool conn_is_open(void);
static void conn_tick(void);
static void focus_refresh(void);
static void focus_action(int action);
static void focus_feedback_show(const char *command_id, const char *detail);
static void focus_feedback_hide(const char *rating, const char *dismissed_by);
static void focus_feedback_tick(void);
static void focus_feedback_content_build(void);
#if LVGL_VERSION_MAJOR >= 9
#define FOCUS_ACTIVE_INDEV() lv_indev_active()
#else
#define FOCUS_ACTIVE_INDEV() lv_indev_get_act()
#endif

static lv_obj_t *focus_object(lv_obj_t *parent, int x, int y, int w, int h) {
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o); lv_obj_set_pos(o, x, y); lv_obj_set_size(o, w, h);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE); return o;
}
static lv_obj_t *focus_label(lv_obj_t *p, const char *s, int y,
                             const lv_font_t *font, uint32_t color) {
    lv_obj_t *o = lv_label_create(p);
    ui_label_set_text(o, s); lv_obj_set_style_text_font(o, font, 0);
    lv_obj_set_style_text_color(o, lv_color_hex(color), 0);
    lv_obj_set_style_text_align(o, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(o, 360); lv_obj_set_pos(o, 60, y);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_CLICKABLE); return o;
}
static void focus_button_event(lv_event_t *e) {
    if (lv_event_get_code(e) == LV_EVENT_CLICKED)
        focus_action((int)(intptr_t)lv_event_get_user_data(e));
}
static lv_obj_t *focus_button(lv_obj_t *p, const char *s, int x, int y,
                              int w, int h, uint32_t color, int action) {
    lv_obj_t *b = focus_object(p, x, y, w, h);
    lv_obj_set_style_radius(b, h / 2, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x141719), 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x2A2E30), LV_STATE_PRESSED);
    lv_obj_add_flag(b, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(b, focus_button_event, LV_EVENT_CLICKED, (void *)(intptr_t)action);
    lv_obj_t *l = lv_label_create(b); ui_label_set_text(l, s);
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
    lv_obj_set_style_text_font(l, &lv_font_montserrat_24, 0); lv_obj_center(l);
    lv_obj_clear_flag(l, LV_OBJ_FLAG_CLICKABLE); return l;
}
static lv_obj_t *focus_arc(lv_obj_t *p, int x, int y, int d, int width, bool background) {
    lv_obj_t *a = lv_arc_create(p); lv_obj_remove_style_all(a);
    lv_obj_set_size(a, d, d); lv_obj_set_pos(a, x, y);
    lv_arc_set_rotation(a, 270); lv_arc_set_bg_angles(a, 0, 360);
    lv_arc_set_range(a, 0, 1000); lv_arc_set_value(a, 1000);
    lv_obj_set_style_arc_width(a, width, LV_PART_MAIN);
    lv_obj_set_style_arc_width(a, width, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(a, lv_color_hex(0x24201A), LV_PART_MAIN);
    lv_obj_set_style_arc_color(a, lv_color_hex(FOCUS_AMBER), LV_PART_INDICATOR);
    lv_obj_set_style_arc_opa(a, background ? LV_OPA_COVER : LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(a, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(a, true, LV_PART_INDICATOR);
    lv_obj_clear_flag(a, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE); return a;
}
static void focus_go_page(int page, bool animate) {
    if (page < 0) page = 0; if (page > PAGE_COUNT - 1) page = PAGE_COUNT - 1;
    lv_obj_scroll_to_x(focus_pager, page * 480, animate ? LV_ANIM_ON : LV_ANIM_OFF);
    focus_dots_until = lv_tick_get() + 2000;
}
static void focus_scroll(lv_event_t *e) {
    if (lv_event_get_code(e) == LV_EVENT_SCROLL_END) {
        long x = (long)lv_obj_get_scroll_x(focus_pager);
        int page = (int)((x + 240) / 480);
        if (page < 0) page = 0;
        if (page > PAGE_COUNT - 1) page = PAGE_COUNT - 1;
        focus_page = page;
    }
    focus_dots_until = lv_tick_get() + 1600;
}
static void focus_set_connected(bool connected) {
    if (connected == focus_connected) return;
    focus_connected = connected; focus_feedback_until = 0; focus_refresh();
}
static void focus_anim_y(void *obj, int32_t y) { lv_obj_set_y((lv_obj_t *)obj, y); }
static void focus_snap_object(lv_obj_t *obj, int from_y, int to_y) {
    lv_anim_del(obj, focus_anim_y);
    lv_obj_set_y(obj, from_y);
    lv_anim_t animation; lv_anim_init(&animation);
    lv_anim_set_var(&animation, obj); lv_anim_set_exec_cb(&animation, focus_anim_y);
    lv_anim_set_values(&animation, from_y, to_y); lv_anim_set_time(&animation, 110);
    lv_anim_set_path_cb(&animation, lv_anim_path_ease_out); lv_anim_start(&animation);
}
static void focus_wheel_snap(int field, int direction) {
    int offset = direction > 0 ? 12 : -12;
    focus_snap_object(focus_wheel_prev[field], 125 + offset, 125);
    focus_snap_object(field == 0 ? focus_minute_digits : focus_second_digits, 196 + offset, 196);
    focus_snap_object(focus_wheel_next[field], 295 + offset, 295);
}
static void focus_wheel_hide(void) {
    focus_wheel_field = -1;
    for (int i = 0; i < 2; ++i) {
        lv_obj_set_style_text_opa(focus_wheel_prev[i], LV_OPA_40, 0);
        lv_obj_set_style_text_opa(focus_wheel_next[i], LV_OPA_40, 0);
    }
}
static void focus_wheel_render(void) {
    unsigned total = (focus_timer.remaining_ms + 999) / 1000;
    char text[4];
    int values[2] = {(int)(total / 60), (int)(total % 60)};
    const int limits[2] = {100, 60};
    snprintf(text, sizeof(text), "%02d", values[0]); ui_label_set_text(focus_minute_digits, text);
    snprintf(text, sizeof(text), "%02d", values[1]); ui_label_set_text(focus_second_digits, text);
    for (int i = 0; i < 2; ++i) {
        snprintf(text, sizeof(text), "%02d", (values[i] + limits[i] - 1) % limits[i]);
        ui_label_set_text(focus_wheel_prev[i], text);
        snprintf(text, sizeof(text), "%02d", (values[i] + 1) % limits[i]);
        ui_label_set_text(focus_wheel_next[i], text);
        lv_opa_t opacity = focus_wheel_field == i ? LV_OPA_70 : LV_OPA_40;
        lv_obj_set_style_text_opa(focus_wheel_prev[i], opacity, 0);
        lv_obj_set_style_text_opa(focus_wheel_next[i], opacity, 0);
    }
}
static void focus_wheel_event(lv_event_t *e) {
    lv_event_code_t code = lv_event_get_code(e);
    int field = (int)(intptr_t)lv_event_get_user_data(e);
    lv_indev_t *indev = FOCUS_ACTIVE_INDEV(); lv_point_t point = {0, 0};
    if (indev) lv_indev_get_point(indev, &point);
    if (code == LV_EVENT_PRESSED) {
        focus_wheel_field = field; focus_wheel_start = point; focus_wheel_applied = 0;
        focus_wheel_render();
    } else if (code == LV_EVENT_PRESSING) {
        int dx = point.x - focus_wheel_start.x, dy = point.y - focus_wheel_start.y;
        if (abs(dy) > 10 && abs(dy) > abs(dx) && !focus_timer.running &&
            focus_timer.remaining_ms == focus_timer.duration_ms) {
            int steps = -dy / 24, delta = steps - focus_wheel_applied;
            if (delta) {
                focus_adjust_seconds(&focus_timer, delta * (field == 0 ? 60 : 1));
                focus_wheel_applied = steps; focus_last_seconds = UINT32_MAX;
                focus_refresh(); focus_wheel_snap(field, delta);
            }
        }
    } else if (code == LV_EVENT_RELEASED) {
        int dx = point.x - focus_wheel_start.x, dy = point.y - focus_wheel_start.y;
        if (abs(dx) < 9 && abs(dy) < 9) focus_action(0);
        focus_wheel_hide();
    } else if (code == LV_EVENT_PRESS_LOST) focus_wheel_hide();
}
static void focus_preset_render(void) {
    for (int i = 0; i < 3; ++i) {
        bool selected = i == focus_preset_index;
        lv_obj_set_style_text_color(focus_preset_values[i],
            lv_color_hex(selected ? FOCUS_AMBER : FOCUS_MUTED), 0);
        lv_obj_set_style_text_opa(focus_preset_values[i], selected ? LV_OPA_COVER : LV_OPA_50, 0);
    }
}
static void focus_action(int action) {
    if (bt_is_open() || vh_is_open() || conn_is_open()) return;
    static const int presets[3] = {5, 15, 25};
    uint32_t now = lv_tick_get();
    if (action == 4) {
        if (focus_page == PAGE_FOCUS) focus_go_page(PAGE_MAC, true);
        else if (focus_page == PAGE_MAC) focus_go_page(PAGE_FOCUS, true);
        return;
    }
    /* The Bili page owns its own input; stale legacy actions must not reset
     * the timer or send HID media keys. */
    if (focus_page == PAGE_BILI) return;
    if (action == 7) { focus_reset(&focus_timer); focus_last_seconds = UINT32_MAX; }
    else if (action == 8) { focus_restart(&focus_timer, now); focus_last_seconds = UINT32_MAX; }
    else if (action == 9) {
        unsigned m = focus_timer.duration_ms / 60000;
        focus_preset_index = m == 15 ? 1 : m == 25 ? 2 : 0;
        focus_preset_render(); ui_obj_set_hidden(focus_preset_overlay, false);
    } else if (action == 10) {
        focus_preset_index = (focus_preset_index + 1) % 3; focus_preset_render();
    } else if (action == 11) {
        focus_reset(&focus_timer); focus_set_minutes(&focus_timer, presets[focus_preset_index]);
        ui_obj_set_hidden(focus_preset_overlay, true); focus_last_seconds = UINT32_MAX;
    } else if (action == 5) {
        if (focus_page == 1) focus_volume = !focus_volume;
        focus_feedback_until = 0;
    } else if (focus_page == PAGE_FOCUS) {
        if (action == 0) focus_toggle(&focus_timer, now);
    } else if (focus_page == PAGE_MAC && action >= -1 && action <= 1) {
        uint16_t usage = action == 0 ? (focus_volume ? 0xE2 : 0xCD) :
            action < 0 ? (focus_volume ? 0xEA : 0xB6) : (focus_volume ? 0xE9 : 0xB5);
        bool sent = focus_connected && focus_send_media && focus_send_media(usage);
        const char *label = !sent ? "Not connected" : action == 0 ?
            (focus_volume ? "Mute sent" : "Play / pause sent") : action < 0 ?
            (focus_volume ? "Volume down" : "Previous track") :
            (focus_volume ? "Volume up" : "Next track");
        ui_label_set_text(focus_mac_status, label); focus_feedback_until = now + 1600;
    }
    focus_refresh();
}
/* Keep the overlay shell/badge; allocate feedback controls only while visible. */
static void focus_feedback_send(const char *rating, const char *dismissed_by) {
    if (focus_feedback_sink && focus_feedback_command[0])
        focus_feedback_sink(focus_feedback_command, rating, dismissed_by,
                            (int)focus_feedback_touches, 0);
}
static void focus_feedback_badge_show(void) {
    if (!focus_feedback_badge || !focus_feedback_last_command[0]) return;
    focus_feedback_badge_until = (focus_feedback_result_tick ? focus_feedback_result_tick : lv_tick_get()) + 30000;
    if ((int32_t)(lv_tick_get() - focus_feedback_badge_until) >= 0) {
        focus_feedback_badge_until = 0;
        ui_obj_set_hidden(focus_feedback_badge, true);
        return;
    }
    if (focus_page == PAGE_BILI) {
        ui_obj_set_hidden(focus_feedback_badge, false);
        lv_obj_move_foreground(focus_feedback_badge);
    }
}
static void focus_feedback_hide(const char *rating, const char *dismissed_by) {
    if (!focus_feedback_visible) return;
    if (!focus_feedback_selected) {
        focus_feedback_send(rating ? rating : "unfeedback", dismissed_by ? dismissed_by : "timeout");
    }
    if (focus_feedback_command[0])
        snprintf(focus_feedback_last_command, sizeof(focus_feedback_last_command),
                 "%s", focus_feedback_command);
    focus_feedback_badge_show();  /* even after GOOD/BAD: 30 s review badge */
    focus_feedback_visible = false;
    focus_feedback_selected = false;
    focus_feedback_held = false;
    if (focus_feedback_overlay) {
        ui_obj_set_hidden(focus_feedback_overlay, true);
        lv_obj_clean(focus_feedback_overlay);
        focus_feedback_title=focus_feedback_count=focus_feedback_good=focus_feedback_bad=NULL;
        focus_feedback_detail=NULL;
    }
}
static void focus_feedback_update(void) {
    if (!focus_feedback_visible || !focus_feedback_count) return;
    uint32_t now = lv_tick_get();
    uint32_t remain = 0;
    if (focus_feedback_deadline > now) remain = focus_feedback_deadline - now;
    else remain = 0;
    unsigned sec = (remain + 999u) / 1000u;
    char text[16];
    snprintf(text, sizeof(text), "%us", sec);
    ui_label_set_text(focus_feedback_count, text);
}
static void focus_feedback_tick(void) {
    if (focus_feedback_visible && focus_page != PAGE_BILI) {
        focus_feedback_hide("unfeedback", "page_switch");
    }
    if (focus_feedback_badge && focus_feedback_badge_until) {
        uint32_t now = lv_tick_get();
        if ((int32_t)(now - focus_feedback_badge_until) >= 0) {
            focus_feedback_badge_until = 0;
            ui_obj_set_hidden(focus_feedback_badge, true);
        } else if (focus_page == PAGE_BILI && !focus_feedback_visible) {
            ui_obj_set_hidden(focus_feedback_badge, false);
            lv_obj_move_foreground(focus_feedback_badge);
        } else if (focus_page != PAGE_BILI) {
            ui_obj_set_hidden(focus_feedback_badge, true);
        }
    }
    if (!focus_feedback_visible) return;
    uint32_t now = lv_tick_get();
    if (focus_feedback_selected) {
        if ((int32_t)(now - focus_feedback_deadline) >= 0)
            focus_feedback_hide("good", "selected");
        else
            focus_feedback_update();
        return;
    }
    if (!focus_feedback_held && (int32_t)(now - focus_feedback_deadline) >= 0) {
        focus_feedback_hide("unfeedback", "timeout");
        return;
    }
    focus_feedback_update();
}
static void focus_feedback_show(const char *command_id, const char *detail) {
    if (!focus_feedback_overlay || !command_id || !command_id[0]) return;
    if (focus_feedback_visible) focus_feedback_hide("unfeedback", "replaced");
    if (!focus_feedback_title) focus_feedback_content_build();
    bool new_command = strcmp(focus_feedback_command, command_id) != 0;
    snprintf(focus_feedback_command, sizeof(focus_feedback_command), "%s", command_id);
    if(new_command&&vh_read){VoiceHostView v;vh_read(&v);int i=v.catalog.active;
        snprintf(focus_feedback_origin,37,"%s",i>=0&&i<VH_CAPACITY?v.catalog.hosts[i].id:"");}
    snprintf(focus_feedback_last_command, sizeof(focus_feedback_last_command), "%s", command_id);
    if (new_command || focus_feedback_result_tick == 0)
        focus_feedback_result_tick = lv_tick_get();
    focus_feedback_touches = 0;
    focus_feedback_selected = false;
    focus_feedback_held = false;
    focus_feedback_visible = true;
    focus_feedback_deadline = lv_tick_get() + 5000;
    ui_label_set_text(focus_feedback_title, "WAS THIS CORRECT?");
    snprintf(focus_feedback_detail_text, sizeof(focus_feedback_detail_text), "%s",
             detail ? detail : "");
    if (focus_feedback_detail) ui_label_set_text(focus_feedback_detail, focus_feedback_detail_text);
    ui_label_set_text(focus_feedback_count, "5s");
    lv_obj_clear_state(focus_feedback_good, LV_STATE_CHECKED | LV_STATE_DISABLED);
    lv_obj_clear_state(focus_feedback_bad, LV_STATE_CHECKED | LV_STATE_DISABLED);
    ui_obj_set_hidden(focus_feedback_overlay, false);
    lv_obj_move_foreground(focus_feedback_overlay);
    ui_obj_set_hidden(focus_feedback_badge, true);
}
static void focus_feedback_select(bool good) {
    if (!focus_feedback_visible || focus_feedback_selected) return;
    focus_feedback_selected = true;
    focus_feedback_held = false;
    focus_feedback_deadline = lv_tick_get() + 2000;
    focus_feedback_send(good ? "good" : "bad", "selected");
    ui_label_set_text(focus_feedback_title, "RECORDED");
    ui_label_set_text(focus_feedback_count, "2s");
    lv_obj_add_state(good ? focus_feedback_good : focus_feedback_bad, LV_STATE_CHECKED);
}
static void focus_feedback_button_event(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    focus_feedback_select((int)(intptr_t)lv_event_get_user_data(e) != 0);
}
static void focus_feedback_overlay_event(lv_event_t *e) {
    lv_event_code_t code = lv_event_get_code(e);
    if (focus_feedback_visible == false || focus_feedback_selected) return;
    if (code == LV_EVENT_PRESSED) {
        if (focus_feedback_touches >= 3) return; /* hard 3-touch cap */
        focus_feedback_touches++;
        focus_feedback_held = true;
        focus_feedback_deadline = lv_tick_get() + 5000;
        focus_feedback_update();
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        if (focus_feedback_held) {
            focus_feedback_held = false;
            focus_feedback_deadline = lv_tick_get() + 5000;
            focus_feedback_update();
        }
    }
}
static void focus_feedback_badge_event(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;
    if (focus_feedback_last_command[0] && !focus_feedback_visible)
        focus_feedback_show(focus_feedback_last_command, focus_feedback_detail_text);
}
static void focus_feedback_content_build(void) {
    focus_feedback_title = focus_label(focus_feedback_overlay, "WAS THIS CORRECT?", 112,
                                       &lv_font_montserrat_24, FOCUS_INK);
    lv_obj_set_width(focus_feedback_title, 420); lv_obj_set_x(focus_feedback_title, 30);
    focus_feedback_detail = focus_label(focus_feedback_overlay, "", 158,
                                        &lv_font_montserrat_20, FOCUS_MUTED);
    lv_obj_set_width(focus_feedback_detail, 420); lv_obj_set_x(focus_feedback_detail, 30);
    lv_label_set_long_mode(focus_feedback_detail, LV_LABEL_LONG_DOT);
    focus_feedback_count = focus_label(focus_feedback_overlay, "5s", 24,
                                       &lv_font_montserrat_24, FOCUS_AMBER);
    lv_obj_set_width(focus_feedback_count, 80); lv_obj_set_x(focus_feedback_count, 380);
    lv_obj_set_style_text_align(focus_feedback_count, LV_TEXT_ALIGN_RIGHT, 0);
    focus_feedback_good = focus_object(focus_feedback_overlay, 42, 230, 180, 96);
    lv_obj_set_style_radius(focus_feedback_good, 22, 0);
    lv_obj_set_style_bg_color(focus_feedback_good, lv_color_hex(0x1D332A), 0);
    lv_obj_set_style_bg_opa(focus_feedback_good, LV_OPA_COVER, 0);
    lv_obj_add_flag(focus_feedback_good, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(focus_feedback_good, focus_feedback_button_event, LV_EVENT_CLICKED, (void *)1);
    lv_obj_t *gl = lv_label_create(focus_feedback_good);
    ui_label_set_text(gl, "GOOD"); lv_obj_set_style_text_font(gl, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(gl, lv_color_hex(0x9FE3B6), 0); lv_obj_center(gl);
    lv_obj_clear_flag(gl, LV_OBJ_FLAG_CLICKABLE);
    focus_feedback_bad = focus_object(focus_feedback_overlay, 258, 230, 180, 96);
    lv_obj_set_style_radius(focus_feedback_bad, 22, 0);
    lv_obj_set_style_bg_color(focus_feedback_bad, lv_color_hex(0x3A2322), 0);
    lv_obj_set_style_bg_opa(focus_feedback_bad, LV_OPA_COVER, 0);
    lv_obj_add_flag(focus_feedback_bad, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(focus_feedback_bad, focus_feedback_button_event, LV_EVENT_CLICKED, (void *)0);
    lv_obj_t *bl = lv_label_create(focus_feedback_bad);
    ui_label_set_text(bl, "BAD"); lv_obj_set_style_text_font(bl, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(bl, lv_color_hex(0xE7A6A0), 0); lv_obj_center(bl);
    lv_obj_clear_flag(bl, LV_OBJ_FLAG_CLICKABLE);
}
static void focus_feedback_build(lv_obj_t *screen) {
    focus_feedback_overlay = focus_object(screen, 0, 0, 480, 480);
    lv_obj_set_style_bg_color(focus_feedback_overlay, lv_color_hex(0x080A0B), 0);
    lv_obj_set_style_bg_opa(focus_feedback_overlay, LV_OPA_80, 0);
    lv_obj_add_flag(focus_feedback_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(focus_feedback_overlay, focus_feedback_overlay_event, LV_EVENT_ALL, NULL);
    ui_obj_set_hidden(focus_feedback_overlay, true);
    focus_feedback_badge = focus_object(screen, 330, 430, 138, 34);
    lv_obj_set_style_radius(focus_feedback_badge, 17, 0);
    lv_obj_set_style_bg_color(focus_feedback_badge, lv_color_hex(0x24201A), 0);
    lv_obj_set_style_bg_opa(focus_feedback_badge, LV_OPA_COVER, 0);
    lv_obj_add_flag(focus_feedback_badge, LV_OBJ_FLAG_CLICKABLE);
    ui_obj_set_hidden(focus_feedback_badge, true);
    lv_obj_add_event_cb(focus_feedback_badge, focus_feedback_badge_event, LV_EVENT_CLICKED, NULL);
    lv_obj_t *bd = lv_label_create(focus_feedback_badge);
    ui_label_set_text(bd, "FEEDBACK?"); lv_obj_set_style_text_font(bd, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(bd, lv_color_hex(FOCUS_AMBER), 0); lv_obj_center(bd);
    lv_obj_clear_flag(bd, LV_OBJ_FLAG_CLICKABLE);
}

static void focus_bili_wave_build(void) {
    lv_obj_t *p = focus_pages[PAGE_BILI];
    for (int i = 0; i < FOCUS_WAVE_BARS; ++i) {
        lv_obj_t *b = focus_object(p, 97 + i * 14, 336, 5, 14);
        lv_obj_set_style_radius(b, 3, 0);
        /* Low-saturation amber/orange; center bars are brighter than edges. */
        lv_obj_set_style_bg_color(b, lv_color_hex(i % 2 ? 0xD3B58D : 0xC4A276), 0);
        int dist = i < (FOCUS_WAVE_BARS / 2) ? (FOCUS_WAVE_BARS / 2 - i)
                                            : (i - FOCUS_WAVE_BARS / 2);
        int alpha = 200 - dist * 16;
        if (alpha < 30) alpha = 30;
        lv_obj_set_style_bg_opa(b, (lv_opa_t)alpha, 0);
        lv_obj_clear_flag(b, LV_OBJ_FLAG_CLICKABLE);
        ui_obj_set_hidden(b, true);
        lv_obj_move_background(b);
        focus_bili_wave[i] = b;
    }
}

static void focus_bili_wave_update(const FocusBiliStatus *st) {
    bool show = st && st->state == 4 && focus_page == PAGE_BILI; /* RECORDING */
    if (show && !focus_bili_wave[0]) focus_bili_wave_build();
    if (!show && focus_bili_wave[0]) {
        for (int i=0; i<FOCUS_WAVE_BARS; ++i) {
            BT_DELETE(focus_bili_wave[i]); focus_bili_wave[i]=NULL;
        }
    }
    uint32_t now = lv_tick_get();
    float level = st ? st->rms : 0.0f;
    if (level < 0.0f) level = 0.0f;
    if (level > 1.0f) level = 1.0f;
    level *= 30.0f; /* microphone RMS is intentionally small */
    if (level > 1.0f) level = 1.0f;
    for (int i = 0; i < FOCUS_WAVE_BARS; ++i) {
        lv_obj_t *b = focus_bili_wave[i];
        if (!b) continue;
        if (!show) {
            ui_obj_set_hidden(b, true);
            continue;
        }
        ui_obj_set_hidden(b, false);
        if(i==0&&now-focus_wave_at>=45){
            focus_wave_at=now;
            float target=level<0.04f?0.0f:sqrtf(level);
            focus_wave_level+=(target-focus_wave_level)*(target>focus_wave_level?0.8f:0.25f);
            memmove(focus_wave_history,focus_wave_history+1,sizeof(float)*(FOCUS_WAVE_BARS-1));
            focus_wave_history[FOCUS_WAVE_BARS-1]=focus_wave_level;
        }
        int h=4+(int)(focus_wave_history[i]*68.0f);
        lv_obj_set_height(b,h);lv_obj_set_y(b,290-h/2);
    }
}

static const char *focus_bili_detail(const FocusBiliStatus *st) {
    const char *detail = "";
    if (st->result == 1) {
        if (strstr(st->summary, "ALREADY PLAYING")) detail = "ALREADY PLAYING";
        else if (strstr(st->summary, "ALREADY PAUSED")) detail = "ALREADY PAUSED";
        else detail = "DONE";
    }
    else if (st->result == 2) detail = "PARTIAL";
    else if (st->result == 4) detail = "CANCELED";
    else if (st->result == 5) detail = "UNKNOWN / CHECK MAC";
    else if (st->summary[0]) {
        // Current hosts send a short ASCII label in `detail` (stored as the
        // summary); show it verbatim. Only older hosts send Chinese text, which
        // Montserrat cannot render, so fall back to keyword mapping.
        bool summary_ascii = true;
        for (const unsigned char *c = (const unsigned char *)st->summary; *c; ++c)
            if (*c >= 128) { summary_ascii = false; break; }
        if (summary_ascii) {
            detail = st->summary;
        } else if (strstr(st->summary, "播放器") || strstr(st->summary, "播放视频"))
            detail = "NO PLAYER / PLAY A VIDEO";
        else if (strstr(st->summary, "语音") || strstr(st->summary, "听"))
            detail = "NO SPEECH / TRY AGAIN";
        else if (strstr(st->summary, "搜索") || strstr(st->summary, "过期"))
            detail = "SEARCH EXPIRED / SEARCH AGAIN";
        else if (strstr(st->summary, "未知") || strstr(st->summary, "检查"))
            detail = "UNKNOWN / CHECK MAC";
        else if (strstr(st->summary, "收藏") || strstr(st->summary, "点赞") || strstr(st->summary, "弹幕") || strstr(st->summary, "R2"))
            detail = "UNSUPPORTED / R1";
        else if (strstr(st->summary, "取消"))
            detail = "CANCELED";
        else
            detail = "FAILED / SEE MAC";
    }
    return detail;
}

static void focus_bili_refresh(void) {
    if (!focus_read_bili || !focus_bili_state) return;
    FocusBiliStatus st;
    focus_read_bili(&st);
    /* The FSM RESULT state only lasts two seconds; the overlay intentionally
     * outlives it for five seconds. Hide only when a genuinely new command
     * arrives, not when RESULT decays to READY. */
    if (focus_feedback_visible && st.command_id[0] &&
        strcmp(st.command_id, focus_feedback_command) != 0) {
        focus_feedback_hide("unfeedback", "next_ptt");
    }
    /* A badge belongs to the last completed command. Starting a new command
     * clears it; the next result creates a new badge. */
    if (st.command_id[0] && focus_feedback_last_command[0] &&
        strcmp(st.command_id, focus_feedback_last_command) != 0 &&
        st.state >= 3 && st.state <= 8) {
        focus_feedback_badge_until = 0;
        if (focus_feedback_badge) ui_obj_set_hidden(focus_feedback_badge, true);
    }
    /* Feedback is valuable on FAILED/UNKNOWN too: it lets the analysis model
     * distinguish "intent recognized but execution failed" from "wrong intent".
     * CANCELED is deliberately excluded. */
    if (st.state == 9 && st.result != 0 && st.result != 4 && st.command_id[0] &&
        strcmp(focus_feedback_command, st.command_id) != 0 &&
        strcmp(focus_feedback_last_command, st.command_id) != 0) {
        if(vh_read){VoiceHostView v;vh_read(&v);int i=v.catalog.active;
            snprintf(focus_feedback_origin,37,"%s",i>=0&&i<VH_CAPACITY?v.catalog.hosts[i].id:"");}
        snprintf(focus_feedback_last_command, sizeof(focus_feedback_last_command), "%s", st.command_id);
        focus_feedback_result_tick = lv_tick_get();
        if (focus_page == PAGE_BILI) focus_feedback_show(st.command_id, focus_bili_detail(&st));
        else focus_feedback_badge_show();
    }
    focus_bili_wave_update(&st);
    const char *state_text = "OFFLINE";
    const char *hint = "VOICE SERVICE OFFLINE\nOLD PAGES STILL WORK";
    char timer[24] = "--";
    switch (st.state) {
    case 1: state_text = "CONNECTING"; hint = "LOOKING FOR THE MAC"; break;
    case 2: state_text = "Ready to talk"; hint = "HOLD KEY / RELEASE TO SEND"; break;
    case 3: state_text = "LISTEN"; hint = "HOLD THE KEY"; break;
    case 4: state_text = "LISTENING"; hint = "RELEASE KEY TO SEND"; break;
    case 5: state_text = "Ready to send"; hint = "PRESS KEY AGAIN TO CANCEL"; break;
    case 6: state_text = "SENDING"; hint = "MAC IS THINKING"; break;
    case 7:
        state_text = "THINKING";
        if (!strcmp(st.stage, "transcribing")) state_text = "TRANSCRIBE";
        else if (!strcmp(st.stage, "planning")) state_text = "PLANNING";
        else if (!strcmp(st.stage, "executing")) state_text = "EXECUTING";
        hint = "MAC IS WORKING";
        break;
    case 8: state_text = "CHECKING"; hint = "ASKING THE MAC"; break;
    case 9:
        state_text = st.result == 1 ? "DONE" : st.result == 2 ? "PARTIAL" :
                     st.result == 3 ? "FAILED" : st.result == 4 ? "CANCELED" : "UNKNOWN";
        hint = "RESULT";
        break;
    case 10: state_text = "RELEASE"; hint = "RELEASE THE KEY"; break;
    case 11: state_text = "REPLAY"; hint = "B AGAIN TO CONFIRM"; break;
    case 12: state_text = "REPLAY"; hint = "B AGAIN TO CONFIRM"; break;
    case 13: state_text = "UNKNOWN"; hint = "CHECK MAC\nKEY OR B: OK"; break;
    default: break;
    }
    if (st.state == 4) {
        unsigned ms = st.recording_ms;
        snprintf(timer,sizeof(timer),"%02u:%02u",ms/60000,(ms/1000)%60);
    } else if (st.state == 5) {
        unsigned ms = st.cancel_remaining_ms;
        snprintf(timer,sizeof(timer),"%u.%us",ms/1000,(ms%1000)/100);
    } else if (st.state == 2) {
        snprintf(timer, sizeof(timer), "Talk");
    } else if (st.state == 4) {
        snprintf(timer, sizeof(timer), "REC");
    }
    // Montserrat 48 contains digits, '.', ':' and letters; focus_font_120
    // deliberately only has digits/':' and produced boxes for "01.2"/"TALK".
    if (lv_obj_get_style_text_font(focus_bili_timer, LV_PART_MAIN) != &lv_font_montserrat_48)
        lv_obj_set_style_text_font(focus_bili_timer, &lv_font_montserrat_48, 0);
    /* Do not use transform_zoom here: LVGL 8 allocates a temporary draw layer
     * for scaled text, which is unsafe at the ~10 KB heap seen while recording. */
    if(focus_bili_voice_disc){
        if(st.state==2)ui_obj_set_hidden(focus_bili_voice_disc, false);
        else ui_obj_set_hidden(focus_bili_voice_disc, true);
    }
    lv_obj_set_y(focus_bili_timer,st.state==4?198:st.state==2?244:224);
    ui_label_set_text(focus_bili_state, state_text);
    ui_label_set_text(focus_bili_timer, timer);
    ui_label_set_text(focus_bili_hint, hint);
    if(focus_bili_footer)ui_label_set_text(focus_bili_footer,st.state>=3&&st.state<=8?"TARGET LOCKED / PWR CANCEL":"BOOT x2 REPLAY");
    const char *detail = focus_bili_detail(&st);
    if(st.state==9&&st.command_id[0]&&strcmp(vh_recent_id,st.command_id)){
        snprintf(vh_recent_id,37,"%s",st.command_id);snprintf(vh_recent_result,sizeof(vh_recent_result),"%s",detail);
        if(vh_read){VoiceHostView v;vh_read(&v);int i=v.catalog.active;if(i>=0&&i<VH_CAPACITY)vh_recent_host=v.catalog.hosts[i];}
    }
    char recent[80];snprintf(recent,sizeof(recent),"LAST: %s >",vh_recent_result);
    ui_label_set_text(focus_bili_result,st.state==2&&vh_recent_id[0]?recent:detail);
    if(focus_bili_cancel_track){
        if(st.state==5){ui_obj_set_hidden(focus_bili_cancel_track, false);unsigned ms=st.cancel_remaining_ms>840?840:st.cancel_remaining_ms;lv_obj_set_width(focus_bili_cancel_fill,(int)(280*ms/840));}
        else ui_obj_set_hidden(focus_bili_cancel_track, true);
    }
    VoiceHostView hosts;memset(&hosts,0,sizeof(hosts));
    const char *name="YOUR COMPUTER";
    if(vh_read){vh_read(&hosts);int i=hosts.catalog.active;if(i>=0&&i<VH_CAPACITY&&hosts.catalog.hosts[i].present)name=hosts.catalog.hosts[i].name;}
    char mac[96];snprintf(mac,sizeof(mac),"%s / %s",name,st.ready?"Ready":st.connected?"Connecting":"Offline");
    ui_label_set_text(focus_bili_mac,mac);
    bool busy=st.state>=3&&st.state<=8;
    if(focus_bili_dot){if(busy)lv_obj_clear_state(focus_bili_dot,LV_STATE_PRESSED);}

}

static void focus_refresh(void) {
    uint32_t now = lv_tick_get();
    if (!focus_next_battery_at || (int32_t)(now - focus_next_battery_at) >= 0) {
        int percent = focus_read_battery ? focus_read_battery() : -1; char battery[8];
        if (percent >= 0 && percent <= 100) snprintf(battery, sizeof(battery), "%d%%", percent);
        else snprintf(battery, sizeof(battery), "--%%");
        ui_label_set_text(focus_battery, battery); focus_next_battery_at = now + 15000;
    }
    focus_update(&focus_timer, now);
    unsigned seconds = (focus_timer.remaining_ms + 999) / 1000;
    bool changed = seconds != focus_last_seconds || focus_last_running != focus_timer.running ||
                   focus_last_finished != focus_timer.finished;
    if (changed) {
        focus_wheel_render();
        ui_label_set_text(focus_state, focus_timer.finished ? "REST" :
            focus_timer.running ? "FOCUSING" :
            focus_timer.remaining_ms != focus_timer.duration_ms ? "PAUSED" : "FOCUS");
        ui_label_set_text(focus_caption, focus_timer.finished ? "KEY TO RESET" :
            focus_timer.running ? "STAY WITH ONE THING" :
            focus_timer.remaining_ms != focus_timer.duration_ms ? "PWR TO CONTINUE" : "ROLL MINUTES  /  SECONDS");
        focus_last_seconds = seconds; focus_last_running = focus_timer.running;
        focus_last_finished = focus_timer.finished;
        int progress = focus_timer.duration_ms ?
            (int)((uint64_t)focus_timer.remaining_ms * 1000 / focus_timer.duration_ms) : 0;
        lv_arc_set_value(focus_arcs[0], progress);
        if (focus_arcs[1]) lv_arc_set_value(focus_arcs[1], progress);
        if (focus_arcs[1]) {
            if (focus_timer.remaining_ms != focus_timer.duration_ms || focus_timer.running || focus_timer.finished)
                ui_obj_set_hidden(focus_arcs[1], false);
            else ui_obj_set_hidden(focus_arcs[1], true);
        }
    }
    if (focus_hint_until && (int32_t)(focus_hint_until - now) <= 0) {
        focus_hint_until = 0; lv_obj_set_style_text_opa(focus_control_hint, LV_OPA_TRANSP, 0);
    }
    if (!bt_is_open()) {
    if(focus_media_timer){char text[32]="";if(focus_timer.running)snprintf(text,sizeof(text),"FOCUS  %02u:%02u",seconds/60,seconds%60);ui_label_set_text(focus_media_timer,text);}
    if (bt_read) {
        BleSnapshot v; bt_read(&v);
        const char *name = v.current >= 0 && v.current < v.count ? v.hosts[v.current].name :
            v.selected >= 0 && v.selected < v.count ? v.hosts[v.selected].name : "Your devices.";
        ui_label_set_text(focus_mac_title, name);
        ui_label_set_text(focus_mac_hint, v.count ? "Connections: pull down above" : "Pull down to pair a device");
        if (!focus_feedback_until) ui_label_set_text(focus_mac_status, ble_state_label(v.state));
    } else {
        ui_label_set_text(focus_mac_title, focus_connected ? "Ready." : "Your devices.");
        ui_label_set_text(focus_mac_hint, "Connections: pull down above");
    }
    if (!focus_feedback_until || (int32_t)(now - focus_feedback_until) >= 0) {
        focus_feedback_until = 0;
        if (!bt_read) ui_label_set_text(focus_mac_status, focus_connected ? "BLUETOOTH CONNECTED" : "WAITING FOR CONNECTION");
    }
    ui_label_set_text(focus_mac_mode, focus_volume ? "VOLUME  /  switch" : "MUSIC  /  switch");
    if (ui_label_set_text(focus_mac_left, focus_volume ? "-" : LV_SYMBOL_PREV))
        lv_obj_center(focus_mac_left);
    if (ui_label_set_text(focus_mac_right, focus_volume ? "+" : LV_SYMBOL_NEXT))
        lv_obj_center(focus_mac_right);
    }
    for (int i = 0; i < PAGE_COUNT; ++i) {
        if (!focus_dots[i]) continue;
        ui_obj_set_bg_color(focus_dots[i], lv_color_hex(i == focus_page ? FOCUS_INK : 0x484B4D));
        ui_obj_set_opa(focus_dots[i], (int32_t)(focus_dots_until - now) > 0 ? LV_OPA_COVER : LV_OPA_TRANSP);
    }
    focus_bili_refresh();
    focus_feedback_tick();
    bt_tick();
    vh_tick();
    conn_tick();
}
static void focus_ui_set_bili_source(FocusReadBili reader) { focus_read_bili = reader; }
static void focus_ui_set_feedback_sink(FocusFeedbackSink sink) { focus_feedback_sink = sink; }
static void focus_tick(lv_timer_t *t) { (void)t; focus_refresh(); }
static void focus_connections_event(lv_event_t *e) { if(lv_event_get_code(e)==LV_EVENT_CLICKED)conn_open(); }
static void focus_media_build(void) {
    focus_last_seconds=UINT32_MAX;
    lv_obj_t *p = focus_pages[PAGE_MAC];
    focus_arcs[1] = NULL;
    focus_label(p,"BLUETOOTH",74,&lv_font_montserrat_16,FOCUS_MUTED);
    focus_media_timer=focus_label(p,"",120,&lv_font_montserrat_14,FOCUS_AMBER);
    focus_mac_title = focus_label(p, "Your devices.", 154, &lv_font_montserrat_48, FOCUS_INK);
    lv_label_set_long_mode(focus_mac_title, LV_LABEL_LONG_DOT);
    lv_obj_clear_flag(focus_mac_title, LV_OBJ_FLAG_CLICKABLE);
    focus_mac_status = focus_label(p, "", 222, &lv_font_montserrat_14, FOCUS_BLUE);
    focus_mac_left = focus_button(p, LV_SYMBOL_PREV, 48, 251, 108, 108, FOCUS_BLUE, -1);
    focus_button(p, LV_SYMBOL_PLAY " / " LV_SYMBOL_PAUSE, 169, 245, 142, 120, FOCUS_BLUE, 0);
    focus_mac_right = focus_button(p, LV_SYMBOL_NEXT, 324, 251, 108, 108, FOCUS_BLUE, 1);
    focus_mac_mode = focus_button(p, "MUSIC  /  switch", 100, 368, 280, 56, FOCUS_MUTED, 5);
    focus_mac_hint = focus_label(p, "", 434, &lv_font_montserrat_14, FOCUS_MUTED);
}
static void focus_media_visibility(bool open) {
    if(open) {
        lv_obj_clean(focus_pages[PAGE_MAC]);
        focus_arcs[1] = NULL;
        focus_mac_title=focus_mac_status=focus_mac_hint=focus_media_timer=NULL;
        focus_mac_mode=focus_mac_left=focus_mac_right=NULL;
    } else {
        focus_media_build();
        focus_feedback_until=0;
        focus_refresh(); /* Populate current peer before the first return frame. */
    }
}
static void focus_voice_history(lv_event_t *e){if(lv_event_get_code(e)==LV_EVENT_CLICKED&&vh_recent_id[0]&&vh_open())vh_draw(VH_UI_HISTORY);}
static void focus_voice_build(void) {
    lv_obj_t *p=focus_pages[PAGE_BILI];
    focus_bili_title=focus_label(p,"VOICE / BILIBILI",59,&lv_font_montserrat_16,FOCUS_MUTED);
    focus_bili_dot=focus_object(p,72,84,336,50);
    lv_obj_clear_flag(focus_bili_dot,LV_OBJ_FLAG_CLICKABLE);
    focus_bili_mac=lv_label_create(focus_bili_dot);lv_obj_set_width(focus_bili_mac,310);lv_label_set_long_mode(focus_bili_mac,LV_LABEL_LONG_DOT);ui_label_set_text(focus_bili_mac,"YOUR COMPUTER / READY");
    lv_obj_set_style_text_font(focus_bili_mac,&lv_font_montserrat_20,0);lv_obj_set_style_text_color(focus_bili_mac,lv_color_hex(FOCUS_BLUE),0);lv_obj_set_style_text_align(focus_bili_mac,LV_TEXT_ALIGN_CENTER,0);lv_obj_center(focus_bili_mac);lv_obj_clear_flag(focus_bili_mac,LV_OBJ_FLAG_CLICKABLE);
    focus_bili_state=focus_label(p,"OFFLINE",158,&lv_font_montserrat_28,FOCUS_INK);
    focus_bili_voice_disc=focus_object(p,170,199,140,140);lv_obj_set_style_radius(focus_bili_voice_disc,70,0);
    lv_obj_set_style_bg_color(focus_bili_voice_disc,lv_color_hex(0x151B20),0);lv_obj_set_style_bg_opa(focus_bili_voice_disc,LV_OPA_COVER,0);
    lv_obj_set_style_border_width(focus_bili_voice_disc,1,0);lv_obj_set_style_border_color(focus_bili_voice_disc,lv_color_hex(FOCUS_BLUE),0);lv_obj_clear_flag(focus_bili_voice_disc,LV_OBJ_FLAG_CLICKABLE);
    focus_bili_timer=focus_label(p,"TALK",224,&lv_font_montserrat_48,FOCUS_BLUE);
    focus_bili_cancel_track=focus_object(p,100,310,280,4);lv_obj_set_style_bg_color(focus_bili_cancel_track,lv_color_hex(0x33373B),0);lv_obj_set_style_bg_opa(focus_bili_cancel_track,LV_OPA_COVER,0);
    focus_bili_cancel_fill=focus_object(focus_bili_cancel_track,0,0,280,4);lv_obj_set_style_bg_color(focus_bili_cancel_fill,lv_color_hex(FOCUS_AMBER),0);lv_obj_set_style_bg_opa(focus_bili_cancel_fill,LV_OPA_COVER,0);ui_obj_set_hidden(focus_bili_cancel_track, true);
    focus_bili_hint=focus_label(p,"CONNECT YOUR COMPUTER",350,&lv_font_montserrat_16,FOCUS_MUTED);
    focus_bili_result=focus_label(p,"",394,&lv_font_montserrat_20,FOCUS_BLUE);lv_label_set_long_mode(focus_bili_result,LV_LABEL_LONG_DOT);lv_obj_add_flag(focus_bili_result,LV_OBJ_FLAG_CLICKABLE);lv_obj_add_event_cb(focus_bili_result,focus_voice_history,LV_EVENT_CLICKED,NULL);
    focus_bili_footer=focus_label(p,"BOOT x2 REPLAY",420,&lv_font_montserrat_16,FOCUS_MUTED);
}
static void focus_voice_visibility(bool open) {
    if(open){
        if(focus_feedback_visible)focus_feedback_hide("unfeedback","host_manager");
        focus_feedback_badge_until=0;if(focus_feedback_badge)ui_obj_set_hidden(focus_feedback_badge, true);
        lv_obj_clean(focus_pages[PAGE_BILI]);
        focus_bili_state=focus_bili_timer=focus_bili_hint=focus_bili_mac=focus_bili_dot=focus_bili_result=focus_bili_title=focus_bili_voice_disc=focus_bili_cancel_track=focus_bili_cancel_fill=focus_bili_footer=NULL;
        memset(focus_bili_wave,0,sizeof(focus_bili_wave));memset(focus_wave_history,0,sizeof(focus_wave_history));focus_wave_level=0;
    }else {focus_voice_build();focus_bili_refresh();}
}
#include "connections_ui.h"
static void focus_ui_build(lv_obj_t *screen, FocusSendMedia send_media, FocusReadBattery read_battery) {
    power_ui_init();
    bt_screen = screen;
    focus_timer = focus_timer_init(); focus_send_media = send_media; focus_read_battery = read_battery;
    focus_page = 0; focus_volume = false; focus_connected = false; focus_next_battery_at = 0;
    lv_obj_remove_style_all(screen); lv_obj_set_style_bg_color(screen, lv_color_hex(0), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0); lv_obj_clear_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    focus_pager = focus_object(screen, 0, 0, 480, 480);
    lv_obj_add_flag(focus_pager, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_SCROLL_ONE |
        LV_OBJ_FLAG_SCROLL_MOMENTUM | LV_OBJ_FLAG_SCROLL_ELASTIC);
    bt_pager = focus_pager;
    lv_obj_set_scroll_dir(focus_pager, LV_DIR_HOR); lv_obj_set_scroll_snap_x(focus_pager, LV_SCROLL_SNAP_CENTER);
    lv_obj_set_scrollbar_mode(focus_pager, LV_SCROLLBAR_MODE_OFF); lv_obj_set_style_anim_time(focus_pager, 280, 0);
    lv_obj_add_event_cb(focus_pager, focus_scroll, LV_EVENT_SCROLL, NULL);
    lv_obj_add_event_cb(focus_pager, focus_scroll, LV_EVENT_SCROLL_END, NULL);
    for (int i = 0; i < PAGE_COUNT; ++i) {
        focus_pages[i] = focus_object(focus_pager, i * 480, 0, 480, 480);
        lv_obj_add_flag(focus_pages[i], LV_OBJ_FLAG_SNAPPABLE);
        lv_obj_set_style_bg_color(focus_pages[i], lv_color_hex(0), 0);
        lv_obj_set_style_bg_opa(focus_pages[i], LV_OPA_COVER, 0);
    }
    lv_obj_t *p = focus_pages[0];
    focus_arcs[0] = focus_arc(p, 32, 48, 416, 6, true);
    focus_state = focus_label(p, "FOCUS", 79, &lv_font_montserrat_24, FOCUS_AMBER);
    focus_minute_digits = focus_label(p, "25", 196, &focus_font_120, FOCUS_INK);
    lv_obj_set_width(focus_minute_digits, 164); lv_obj_set_x(focus_minute_digits, 55);
    focus_colon = focus_label(p, ":", 196, &focus_font_120, FOCUS_INK);
    lv_obj_set_width(focus_colon, 42); lv_obj_set_x(focus_colon, 219);
    focus_second_digits = focus_label(p, "00", 196, &focus_font_120, FOCUS_INK);
    lv_obj_set_width(focus_second_digits, 164); lv_obj_set_x(focus_second_digits, 261);
    focus_caption = focus_label(p, "", 348, &lv_font_montserrat_16, FOCUS_MUTED);
    for (int i = 0; i < 2; ++i) {
        int x = i == 0 ? 72 : 278;
        focus_wheel_prev[i] = focus_label(p, "00", 125, &lv_font_montserrat_48, FOCUS_MUTED);
        focus_wheel_next[i] = focus_label(p, "00", 295, &lv_font_montserrat_48, FOCUS_MUTED);
        lv_obj_set_width(focus_wheel_prev[i], 130); lv_obj_set_x(focus_wheel_prev[i], x);
        lv_obj_set_width(focus_wheel_next[i], 130); lv_obj_set_x(focus_wheel_next[i], x);
        lv_obj_set_style_text_opa(focus_wheel_prev[i], LV_OPA_40, 0);
        lv_obj_set_style_text_opa(focus_wheel_next[i], LV_OPA_40, 0);
    }
    focus_control_hint = focus_label(p, "PWR START  /  BOOT STOP  /  KEY RESET", 398, &lv_font_montserrat_14, FOCUS_MUTED);
    lv_obj_t *minute_zone = focus_object(p, 34, 105, 206, 283);
    lv_obj_t *second_zone = focus_object(p, 240, 105, 206, 283);
    lv_obj_add_flag(minute_zone, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_EVENT_BUBBLE);
    lv_obj_add_flag(second_zone, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_EVENT_BUBBLE);
    lv_obj_add_event_cb(minute_zone, focus_wheel_event, LV_EVENT_ALL, (void *)(intptr_t)0);
    lv_obj_add_event_cb(second_zone, focus_wheel_event, LV_EVENT_ALL, (void *)(intptr_t)1);
    focus_preset_overlay = focus_object(p, 68, 116, 344, 252);
    lv_obj_set_style_radius(focus_preset_overlay, 32, 0); lv_obj_set_style_bg_color(focus_preset_overlay, lv_color_hex(0x090A0B), 0);
    lv_obj_set_style_bg_opa(focus_preset_overlay, LV_OPA_COVER, 0); lv_obj_set_style_border_width(focus_preset_overlay, 1, 0);
    lv_obj_set_style_border_color(focus_preset_overlay, lv_color_hex(0x2C2821), 0);
    lv_obj_t *title = focus_label(focus_preset_overlay, "RELEASE TO SET", 25, &lv_font_montserrat_14, FOCUS_MUTED);
    lv_obj_set_width(title, 300); lv_obj_set_x(title, 22);
    const char *values[3] = {"05", "15", "25"};
    for (int i = 0; i < 3; ++i) {
        focus_preset_values[i] = focus_label(focus_preset_overlay, values[i], 94, &lv_font_montserrat_48, FOCUS_MUTED);
        lv_obj_set_width(focus_preset_values[i], 92); lv_obj_set_x(focus_preset_values[i], 24 + i * 102);
    }
    lv_obj_t *preset_hint = focus_label(focus_preset_overlay, "KEEP HOLDING TO ADVANCE", 205, &lv_font_montserrat_14, FOCUS_MUTED);
    lv_obj_set_width(preset_hint, 300); lv_obj_set_x(preset_hint, 22); ui_obj_set_hidden(focus_preset_overlay, true);
    focus_media_build();
    bt_visibility = focus_media_visibility;
    focus_voice_build();
    vh_parent=focus_pages[PAGE_BILI];vh_pager=focus_pager;vh_visibility=focus_voice_visibility;
    focus_feedback_build(screen);
    for (int i = 0; i < PAGE_COUNT; ++i) {
        focus_dots[i] = focus_object(screen, 216 + i * 20, 466, 7, 7);
        lv_obj_set_style_radius(focus_dots[i], 4, 0);
        lv_obj_set_style_bg_color(focus_dots[i], lv_color_hex(0x484B4D), 0);
        lv_obj_set_style_bg_opa(focus_dots[i], LV_OPA_COVER, 0);
        lv_obj_clear_flag(focus_dots[i], LV_OBJ_FLAG_CLICKABLE);
    }
    focus_battery = focus_label(screen, "--%", 20, &lv_font_montserrat_16, FOCUS_MUTED);
    lv_obj_set_width(focus_battery, 64); lv_obj_set_x(focus_battery, 396);
    lv_obj_set_style_text_align(focus_battery, LV_TEXT_ALIGN_RIGHT, 0); lv_obj_move_foreground(focus_battery);
    focus_dots_until = lv_tick_get() + 3000; focus_hint_until = lv_tick_get() + 5000;
    lv_obj_update_layout(screen); focus_refresh(); conn_init(); lv_timer_create(focus_tick, 50, NULL);
}
#endif
