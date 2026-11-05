#ifndef DESKMATE_UI_UPDATE_H
#define DESKMATE_UI_UPDATE_H
#include "lvgl.h"
#include <stdbool.h>
#include <string.h>

/* LVGL 8 invalidates/reallocates labels even when their text is unchanged.
 * Compare the object's own text so rebuilt views need no external cache. */
static inline bool ui_label_set_text(lv_obj_t *label, const char *text) {
    if (text && strcmp(lv_label_get_text(label), text) == 0) return false;
    lv_label_set_text(label, text);
    return true;
}

/* These helpers are for runtime MAIN/default styles, not state-specific styles. */
static inline void ui_obj_set_opa(lv_obj_t *obj, lv_opa_t opacity) {
    if (lv_obj_get_style_opa(obj, LV_PART_MAIN) != opacity)
        lv_obj_set_style_opa(obj, opacity, 0);
}

static inline void ui_obj_set_hidden(lv_obj_t *obj, bool hidden) {
    if (lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN) == hidden) return;
    if (hidden) lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_clear_flag(obj, LV_OBJ_FLAG_HIDDEN);
}

static inline void ui_obj_set_bg_color(lv_obj_t *obj, lv_color_t color) {
    lv_color_t current = lv_obj_get_style_bg_color(obj, LV_PART_MAIN);
#if LVGL_VERSION_MAJOR >= 9
    bool equal = lv_color_eq(current, color);
#else
    bool equal = current.full == color.full;
#endif
    if (!equal) lv_obj_set_style_bg_color(obj, color, 0);
}
#endif
