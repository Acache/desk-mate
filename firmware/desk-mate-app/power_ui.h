#ifndef DESKMATE_POWER_UI_H
#define DESKMATE_POWER_UI_H
#include "lvgl.h"
#include "ui_update.h"
#include "power_hold.h"
#include <stdio.h>

static PowerHold power_hold;
static unsigned power_last_seconds;
static bool power_last_down;
static lv_obj_t *power_overlay, *power_ring, *power_seconds, *power_hint, *power_instruction;

static lv_obj_t *power_label(lv_obj_t *parent, const char *text, int y,
                              const lv_font_t *font, uint32_t color) {
    lv_obj_t *label = lv_label_create(parent);
    ui_label_set_text(label, text);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(label, 280); lv_obj_set_pos(label, 0, y);
    return label;
}
static void power_ui_create(void) {
    power_last_seconds = UINT32_MAX;
    power_last_down = !power_hold.down;
    power_overlay = lv_obj_create(lv_layer_top());
    lv_obj_remove_style_all(power_overlay);
    lv_obj_set_size(power_overlay, 480, 480);
    lv_obj_set_style_bg_color(power_overlay, lv_color_hex(0), 0);
    lv_obj_set_style_bg_opa(power_overlay, LV_OPA_60, 0);
    lv_obj_clear_flag(power_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(power_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_t *card = lv_obj_create(power_overlay);
    lv_obj_remove_style_all(card);
    lv_obj_set_pos(card, 100, 108); lv_obj_set_size(card, 280, 264);
    lv_obj_set_style_bg_color(card, lv_color_hex(0x191C20), 0);
    lv_obj_set_style_bg_opa(card, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(card, 28, 0);
    lv_obj_set_style_border_color(card, lv_color_hex(0x393D40), 0);
    lv_obj_set_style_border_width(card, 1, 0);
    lv_obj_clear_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    power_label(card, "POWER", 20, &lv_font_montserrat_14, 0x858883);
    power_ring = lv_arc_create(card);
    lv_obj_set_size(power_ring, 132, 132); lv_obj_set_pos(power_ring, 74, 52);
    lv_arc_set_rotation(power_ring, 270);
    lv_arc_set_bg_angles(power_ring, 0, 360);
    lv_arc_set_range(power_ring, 0, POWER_HOLD_PROGRESS_MS);
    lv_obj_remove_style(power_ring, NULL, LV_PART_KNOB);
    lv_obj_set_style_arc_width(power_ring, 7, LV_PART_MAIN);
    lv_obj_set_style_arc_width(power_ring, 7, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(power_ring, lv_color_hex(0x33373B), LV_PART_MAIN);
    lv_obj_set_style_arc_color(power_ring, lv_color_hex(0xE8B66D), LV_PART_INDICATOR);
    lv_obj_clear_flag(power_ring, LV_OBJ_FLAG_CLICKABLE);
    power_seconds = power_label(card, "0s", 90, &lv_font_montserrat_48, 0xF3EFE7);
    power_hint = power_label(card, "HOLD 3s TO POWER OFF", 204, &lv_font_montserrat_14, 0xF3EFE7);
    power_instruction = power_label(card, "RELEASE TO CANCEL", 232, &lv_font_montserrat_14, 0x858883);
}
static void power_ui_render(void) {
    if (!power_hold_visible(&power_hold)) {
        if (power_overlay) { lv_obj_del(power_overlay); power_overlay = power_ring = power_seconds = power_hint = power_instruction = NULL; }
        return;
    }
    if (!power_overlay) power_ui_create();
    lv_obj_move_foreground(power_overlay);
    ui_obj_set_opa(power_overlay, power_hold.opacity);
    lv_arc_set_value(power_ring, (int)power_hold.progress_ms);
    /* Keep the arc at 20ms resolution; only the label snaps to seconds. */
    unsigned seconds = power_hold.progress_ms / 1000;
    if (seconds != power_last_seconds) {
        char text[12];
        snprintf(text, sizeof(text), "%us", seconds);
        ui_label_set_text(power_seconds, text);
        power_last_seconds = seconds;
    }
    if (power_hold.down != power_last_down) {
        ui_label_set_text(power_hint, power_hold.down ? "HOLD 3s TO POWER OFF" : "POWER OFF CANCELLED");
        ui_label_set_text(power_instruction, power_hold.down ? "RELEASE TO CANCEL" : "HOLD PWR TO TRY AGAIN");
        power_last_down = power_hold.down;
    }
}
static bool power_ui_input(uint8_t events) {
    bool tap = power_hold_input(&power_hold, lv_tick_get(), events);
    power_ui_render();
    return tap;
}
static void power_ui_tick(lv_timer_t *timer) {
    (void)timer;
    power_hold_tick(&power_hold, lv_tick_get());
    power_ui_render();
}
static void power_ui_init(void) {
    lv_timer_create(power_ui_tick, 20, NULL);
}
#endif
