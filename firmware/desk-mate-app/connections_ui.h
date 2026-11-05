#ifndef DESKMATE_CONNECTIONS_UI_H
#define DESKMATE_CONNECTIONS_UI_H
/* Included after the page definitions; reuses their trusted connection models. */
static lv_obj_t *conn_panel, *conn_status[3], *conn_hint, *conn_entry;
static lv_obj_t *conn_vh_parent;
static bool conn_active, conn_gate_held;
static bool conn_is_open(void) { return conn_active; }
static int conn_selected;
static int32_t conn_x, conn_y;
static uint32_t conn_generation;
static lv_point_t conn_start;
static bool conn_dragging, conn_settling, conn_opening;
static int conn_drag_origin;
static void conn_anim_y(void *obj,int32_t y){lv_obj_set_y((lv_obj_t *)obj,y);}
static void conn_draw(void);
static void conn_close(void) {
    if (conn_panel) { lv_anim_del(conn_panel,conn_anim_y); BT_DELETE(conn_panel); conn_panel = NULL; }
    conn_active = false; conn_dragging=conn_settling=false; conn_generation++;
    if(conn_gate_held && vh_gate)vh_gate(false); conn_gate_held=false;
    lv_obj_update_layout(focus_pager);
    lv_obj_scroll_to(focus_pager, conn_x, conn_y, LV_ANIM_OFF);
    lv_obj_add_flag(focus_pager, LV_OBJ_FLAG_SCROLLABLE);
}
static void conn_child_closed(void) {
    if (!conn_active) return;
    vh_parent = conn_vh_parent;
    lv_obj_update_layout(focus_pager);
    lv_obj_scroll_to(focus_pager, conn_x, conn_y, LV_ANIM_OFF);
    conn_draw();
}
static void conn_activate(int action) {
    if(!conn_panel || conn_dragging || conn_settling)return;
    if (action == 3) { conn_close(); return; }
    VoiceHostView view = {0}; if (vh_read) vh_read(&view);
    if (view.command_busy || view.catalog.switching || power_hold_visible(&power_hold)) {
        ui_label_set_text(conn_hint, "Voice busy. Try again when finished."); return;
    }
    BT_DELETE(conn_panel); conn_panel = NULL; conn_generation++;
    if (action == 0) bt_open();
    else {
        vh_parent = bt_screen;
        if (vh_open()) {
            vh_root_mode = action == 1 ? VH_UI_NETWORK : VH_UI_TRANSPORT;
            vh_draw(vh_root_mode);
        } else { vh_parent = conn_vh_parent; conn_draw(); }
    }
}
static void conn_click(lv_event_t *e) {
    if (lv_event_get_code(e) == LV_EVENT_CLICKED)
        conn_activate((int)(intptr_t)lv_event_get_user_data(e));
}
static void conn_pointer(lv_point_t *point) {
    lv_indev_t *indev = FOCUS_ACTIVE_INDEV();
    point->x = point->y = 0;
    if (indev) lv_indev_get_point(indev, point);
}
static void conn_settle_done(lv_anim_t *a) {
    (void)a; conn_settling=false;
    if(conn_panel && lv_obj_get_style_y(conn_panel,0)<=-480)conn_close();
}
static void conn_settle(void) {
    if(!conn_panel)return;
    conn_dragging=false;conn_settling=true;
    int y=lv_obj_get_style_y(conn_panel,0);
    int target=conn_opening ? (y> -240?0:-480) : (y< -240?-480:0);
    lv_anim_t a;lv_anim_init(&a);lv_anim_set_var(&a,conn_panel);
    lv_anim_set_exec_cb(&a,conn_anim_y);lv_anim_set_values(&a,y,target);
    lv_anim_set_time(&a,160);lv_anim_set_path_cb(&a,lv_anim_path_ease_out);
#if LVGL_VERSION_MAJOR >= 9
    lv_anim_set_completed_cb(&a,conn_settle_done);
#else
    lv_anim_set_ready_cb(&a,conn_settle_done);
#endif
    lv_anim_start(&a);
}
static void conn_swipe(lv_event_t *e) {
    if(conn_settling)return;
    lv_event_code_t code=lv_event_get_code(e);
    if(code==LV_EVENT_PRESSED){conn_pointer(&conn_start);conn_opening=false;conn_drag_origin=conn_panel?lv_obj_get_style_y(conn_panel,0):0;}
    if(code==LV_EVENT_PRESSING && conn_panel){
        lv_point_t p;conn_pointer(&p);int dy=p.y-conn_start.y;
        if(abs(dy)>6 && abs(dy)>abs(p.x-conn_start.x))conn_dragging=true;
        if(conn_dragging){int y=conn_drag_origin+dy;if(y>0)y=0;if(y< -480)y=-480;lv_obj_set_y(conn_panel,y);}
    }
    if((code==LV_EVENT_RELEASED || code==LV_EVENT_PRESS_LOST)&&conn_dragging)conn_settle();
}
static void conn_arrow(lv_obj_t *parent,int y,bool up){
#if LVGL_VERSION_MAJOR >= 9
    static const lv_point_precise_t down[]={{0,0},{20,7},{40,0}}, upward[]={{0,7},{20,0},{40,7}};
#else
    static const lv_point_t down[]={{0,0},{20,7},{40,0}}, upward[]={{0,7},{20,0},{40,7}};
#endif
    lv_obj_t *line=lv_line_create(parent);lv_line_set_points(line,up?upward:down,3);
    lv_obj_set_pos(line,(lv_obj_get_style_width(parent,0)-40)/2,y);
    lv_obj_set_style_line_width(line,3,0);lv_obj_set_style_line_rounded(line,true,0);
    lv_obj_set_style_line_color(line,lv_color_hex(FOCUS_MUTED),0);
    lv_obj_clear_flag(line,LV_OBJ_FLAG_CLICKABLE);
}
static void conn_tick(void) {
    if (!conn_panel) return;
    BleSnapshot ble; VoiceHostView voice;
    memset(&ble,0,sizeof(ble)); memset(&voice,0,sizeof(voice));
    if (bt_read) bt_read(&ble); if (vh_read) vh_read(&voice);
    const char *peer = ble.current >= 0 && ble.current < ble.count ? ble.hosts[ble.current].name : "No device connected";
    char line[96]; snprintf(line, sizeof(line), "%s / %s", peer, ble_state_label(ble.state));
    ui_label_set_text(conn_status[0], line);
    bool ascii = true; for (const unsigned char *c=(const unsigned char *)voice.ssid; *c; ++c) if (*c>=128) ascii=false;
    ui_label_set_text(conn_status[1], voice.wifi ? (ascii && voice.ssid[0] ? voice.ssid : "Wi-Fi connected") : "Not connected");
    snprintf(line, sizeof(line), "%s / %s", voice.ble_mode ? "Bluetooth" : "Wi-Fi", voice.ready ? "Voice ready" : "Voice not ready");
    ui_label_set_text(conn_status[2], line);
}
static void conn_draw(void) {
    conn_panel = focus_object(bt_screen, 0, 0, 480, 480);
    lv_obj_set_style_bg_color(conn_panel, lv_color_hex(0x05080A), 0);
    lv_obj_set_style_bg_opa(conn_panel, LV_OPA_COVER, 0);
    lv_obj_add_flag(conn_panel, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(focus_pager, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(conn_panel, conn_swipe, LV_EVENT_ALL, NULL);
    focus_label(conn_panel, "CONNECTIONS", 48, &lv_font_montserrat_24, FOCUS_INK);
    const char *titles[] = {"BLUETOOTH DEVICES  >", "WI-FI NETWORK  >", "VOICE CONNECTION  >"};
    for (int i=0; i<3; ++i) {
        int y = i<3 ? 106+i*90 : 392;
        lv_obj_t *button = focus_object(conn_panel, 60, y, 360, i<3?78:42);
        lv_obj_set_style_bg_color(button, lv_color_hex(0x151B20), 0);
        lv_obj_set_style_bg_opa(button, LV_OPA_COVER, 0);
        lv_obj_set_style_radius(button, 16, 0);
        lv_obj_add_flag(button, LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_EVENT_BUBBLE);
        lv_obj_add_event_cb(button, conn_click, LV_EVENT_CLICKED, (void *)(intptr_t)i);
        lv_obj_t *label=lv_label_create(button); ui_label_set_text(label, titles[i]);
        lv_obj_set_style_text_font(label, &lv_font_montserrat_16, 0);
        lv_obj_set_style_text_color(label, lv_color_hex(FOCUS_BLUE), 0);
        lv_obj_set_pos(label, 18, i<3?12:10);
        if (i<3) {
            conn_status[i]=lv_label_create(button); lv_obj_set_pos(conn_status[i],18,42);
            lv_obj_set_width(conn_status[i],324); lv_label_set_long_mode(conn_status[i],LV_LABEL_LONG_DOT);
            lv_obj_set_style_text_font(conn_status[i],&lv_font_montserrat_14,0);
            lv_obj_set_style_text_color(conn_status[i],lv_color_hex(FOCUS_MUTED),0);
        }
    }
    conn_arrow(conn_panel,450,true);
    conn_hint = focus_label(conn_panel, "", 412, &lv_font_montserrat_14, FOCUS_MUTED);
    conn_selected=0; conn_generation++; conn_tick();
}
static void conn_open(void) {
    if (conn_active || bt_is_open() || vh_is_open() || power_hold_visible(&power_hold)) return;
    conn_x=lv_obj_get_scroll_x(focus_pager); conn_y=lv_obj_get_scroll_y(focus_pager);
    lv_obj_scroll_to(focus_pager,conn_x,conn_y,LV_ANIM_OFF);
    if(vh_gate && !vh_gate(true))return;
    conn_gate_held=vh_gate!=NULL;
    conn_vh_parent=vh_parent; conn_active=true; conn_draw();
}
static void conn_entry_event(lv_event_t *e) {
    lv_event_code_t code=lv_event_get_code(e);
    if(code==LV_EVENT_PRESSED){conn_pointer(&conn_start);conn_dragging=false;}
    if(code==LV_EVENT_PRESSING && !conn_settling){
        lv_point_t p;conn_pointer(&p);int dy=p.y-conn_start.y;
        if(dy>6 && dy>abs(p.x-conn_start.x) && !conn_active){conn_open();if(conn_panel){conn_dragging=true;conn_opening=true;lv_obj_set_y(conn_panel,-480);}}
        if(conn_dragging&&conn_panel){int y=-480+dy;if(y>0)y=0;if(y< -480)y=-480;lv_obj_set_y(conn_panel,y);}
    }
    if((code==LV_EVENT_RELEASED||code==LV_EVENT_PRESS_LOST)&&conn_dragging)conn_settle();
}
static void conn_key(int pin, bool held) {
    if (!conn_panel) return;
    if (held && pin==9) { conn_close(); return; }
    if (held) return;
    if (pin==0) { conn_activate(conn_selected); return; }
    conn_selected=(conn_selected+(pin==9?2:1))%3;
    const char *names[]={"Selected: Bluetooth","Selected: Wi-Fi","Selected: Voice connection"};
    ui_label_set_text(conn_hint,names[conn_selected]);
}
static void conn_init(void) {
    bt_closed=conn_child_closed; vh_closed=conn_child_closed;
    conn_entry=focus_object(bt_screen,150,0,180,40);
    lv_obj_add_flag(conn_entry,LV_OBJ_FLAG_CLICKABLE);
    conn_arrow(conn_entry,12,false);
    lv_obj_add_event_cb(conn_entry,conn_entry_event,LV_EVENT_ALL,NULL);
}
#endif
