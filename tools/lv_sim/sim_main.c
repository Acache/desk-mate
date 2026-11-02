#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <SDL.h>
#include "lvgl.h"
#include "../../firmware/desk-mate-app/focus_ui.h"
static uint32_t sim_tick;
static lv_point_t sim_point;
static bool sim_pressed;
static int media_count;
static uint16_t last_media;
static uint32_t test_clock(void) { return sim_tick; }
static bool sim_media(uint16_t u) { ++media_count; last_media = u; return true; }
static int sim_battery(void) { return 85; }
static BleSnapshot sim_bt;
static BleRequest sim_bt_request;
static int sim_bt_requests;
static void sim_bt_read(BleSnapshot *out) { *out=sim_bt; }
static bool sim_bt_submit(const BleRequest *request) { sim_bt_request=*request; ++sim_bt_requests; return true; }
static FocusBiliStatus sim_bili;
static void sim_read_bili(FocusBiliStatus *out) { *out = sim_bili; }
#if LVGL_VERSION_MAJOR >= 9
static void flush(lv_display_t *d, const lv_area_t *a, uint8_t *p) {
    (void)a; (void)p; lv_display_flush_ready(d);
}
static void read_pointer(lv_indev_t *d, lv_indev_data_t *v) {
#else
#define lv_screen_active lv_scr_act
static void flush(lv_disp_drv_t *d, const lv_area_t *a, lv_color_t *p) {
    (void)a; (void)p; lv_disp_flush_ready(d);
}
static void read_pointer(lv_indev_drv_t *d, lv_indev_data_t *v) {
#endif
    (void)d; v->point = sim_point;
    v->state = sim_pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
}
static void advance(unsigned ms) {
    for (unsigned i = 0; i < ms; i += 5) {
        sim_tick += 5;
#if LVGL_VERSION_MAJOR < 9
        lv_tick_inc(5);
#endif
        lv_timer_handler();
    }
}
static void pointer(int x, int y, bool down, unsigned ms) {
    sim_point.x = x; sim_point.y = y; sim_pressed = down; advance(ms);
}
static void drag(int x0, int x1, int y, int steps) {
    pointer(x0, y, true, 40);
    for (int i = 1; i <= steps; ++i) pointer(x0 + (x1 - x0) * i / steps, y, true, 20);
    pointer(x1, y, true, 160);
    pointer(x1, y, false, 1200);
}
static void drag_xy(int x0, int y0, int x1, int y1, int steps) {
    pointer(x0, y0, true, 40);
    for (int i = 1; i <= steps; ++i)
        pointer(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, true, 20);
    pointer(x1, y1, false, 400);
}
static void expect_page(int page) {
    int x = lv_obj_get_scroll_x(focus_pager);
    if (x != page * 480) fprintf(stderr, "Expected page %d at %d, got %d\n", page, page * 480, x);
    assert(x == page * 480);
    assert(focus_page == page);
}
static void run_tests(void) {
    advance(100); expect_page(0);
    const lv_font_t *timer_font = lv_obj_get_style_text_font(focus_minute_digits, LV_PART_MAIN);
    assert(timer_font && timer_font->line_height >= 88);
    assert(strcmp(lv_label_get_text(focus_state), "FOCUS") == 0);
    assert(strcmp(lv_label_get_text(focus_minute_digits), "25") == 0);
    assert(strcmp(lv_label_get_text(focus_second_digits), "00") == 0);
    assert(strcmp(lv_label_get_text(focus_wheel_prev[0]), "24") == 0);
    assert(strcmp(lv_label_get_text(focus_wheel_next[0]), "26") == 0);
    assert(strcmp(lv_label_get_text(focus_wheel_prev[1]), "59") == 0);
    assert(strcmp(lv_label_get_text(focus_wheel_next[1]), "01") == 0);
    const lv_font_t *shadow_font = lv_obj_get_style_text_font(focus_wheel_prev[0], LV_PART_MAIN);
    assert(shadow_font && shadow_font->line_height >= 45);
    assert(lv_obj_get_y(focus_state) < lv_obj_get_y(focus_minute_digits));
    assert(lv_obj_get_y(focus_minute_digits) < lv_obj_get_y(focus_caption));
    assert(lv_obj_get_y(focus_caption) < lv_obj_get_y(focus_control_hint));
    focus_wheel_snap(0, 1);
    advance(5);
    assert(lv_obj_get_style_y(focus_minute_digits, LV_PART_MAIN) != 196);
    advance(145);
    assert(lv_obj_get_style_y(focus_minute_digits, LV_PART_MAIN) == 196);
    drag(300, 245, 240, 15); expect_page(0);
    drag(420, 40, 240, 25); expect_page(1);
    /* Three-page requirement supersedes the old two-page endstop: the
     * second left swipe is now valid page 2, not a clamped page 1. */
    drag(300, 40, 240, 20); expect_page(2);
    drag(40, 420, 240, 25); expect_page(1);
    drag(100, 380, 240, 20); expect_page(0);
    drag(330, 30, 230, 16); /* Swiping over the timer must not activate it. */
    assert(!focus_timer.running);
    int page = lv_obj_get_scroll_x(focus_pager) / 480;
    assert(page >= 0 && page <= 2); expect_page(page);
    focus_go_page(2, true); advance(1200); expect_page(2);
    sim_bili = (FocusBiliStatus){};
    sim_bili.state = 2; sim_bili.ready = true;
    snprintf(sim_bili.transport, sizeof(sim_bili.transport), "READY");
    focus_bili_refresh();
    assert(strcmp(lv_label_get_text(focus_bili_state), "Ready to talk") == 0);
    assert(strcmp(lv_label_get_text(focus_bili_timer), "Talk") == 0);
    assert(lv_obj_get_style_text_font(focus_bili_timer, LV_PART_MAIN)->line_height < 80);
    sim_bili.state = 4; sim_bili.recording_ms = 1234;
    focus_bili_refresh();
    assert(strcmp(lv_label_get_text(focus_bili_timer), "00:01") == 0);
    assert(lv_obj_get_style_text_font(focus_bili_timer, LV_PART_MAIN)->line_height < 80);
    sim_bili.state = 9; sim_bili.result = 1;
    snprintf(sim_bili.summary, sizeof(sim_bili.summary), "完成");
    focus_bili_refresh();
    assert(strcmp(lv_label_get_text(focus_bili_state), "DONE") == 0);
    sim_bili.result = 3;
    snprintf(sim_bili.summary, sizeof(sim_bili.summary), "没有可用的播放器状态，请先播放视频");
    focus_bili_refresh();
    assert(strcmp(lv_label_get_text(focus_bili_result), "NO PLAYER / PLAY A VIDEO") == 0);
    focus_go_page(0, false); advance(100); expect_page(0);
    focus_go_page(0, false); advance(100);
    focus_action(7);
    drag_xy(160, 340, 160, 268, 10); /* Instruction area is part of minute wheel. */
    assert(focus_timer.duration_ms >= 27u * 60000u && focus_timer.duration_ms <= 28u * 60000u);
    uint32_t after_minutes = focus_timer.duration_ms;
    advance(300);
    drag_xy(320, 250, 320, 202, 8);
    assert(focus_timer.duration_ms > after_minutes && focus_timer.duration_ms <= after_minutes + 2000u);
    assert(!lv_obj_has_flag(focus_wheel_prev[0], LV_OBJ_FLAG_HIDDEN));
    pointer(320, 230, true, 80); pointer(320, 230, false, 100);
    assert(focus_timer.running);
    assert(strcmp(lv_label_get_text(focus_state), "FOCUSING") == 0);
    uint32_t running_duration = focus_timer.duration_ms;
    drag_xy(160, 250, 160, 202, 8); /* Editing is locked while running. */
    assert(focus_timer.duration_ms == running_duration);
    advance(1000);
    assert(focus_timer.remaining_ms < focus_timer.duration_ms);
    focus_go_page(1, true); advance(1200); expect_page(1);
    assert(!lv_obj_has_flag(focus_arcs[1], LV_OBJ_FLAG_HIDDEN));
    unsigned remaining = focus_timer.remaining_ms;
    focus_set_connected(false); advance(500);
    assert(focus_timer.running && focus_timer.remaining_ms < remaining);
    focus_action(0); assert(media_count == 0);
    focus_set_connected(true); focus_action(0);
    assert(media_count == 1 && last_media == 0xCD);
    focus_action(-1); assert(last_media == 0xB6);
    focus_action(1); assert(last_media == 0xB5);
    focus_action(5); focus_action(0); assert(last_media == 0xE2);
    focus_action(-1); assert(last_media == 0xEA);
    focus_action(1); assert(last_media == 0xE9);
    focus_go_page(0, true); advance(70);
    pointer(200, 240, true, 80); pointer(210, 240, false, 1400);
    int x = lv_obj_get_scroll_x(focus_pager);
    assert(x == 0 || x == 480);
    focus_go_page(0, false); advance(100);
    pointer(350, 240, true, 40); pointer(270, 240, true, 40);
    pointer(170, 240, true, 40); pointer(90, 240, false, 1400);
    expect_page(1);
    focus_go_page(0, false); advance(100);
    focus_action(7);
    assert(strcmp(lv_label_get_text(focus_state), "FOCUS") == 0);
    assert(!focus_timer.running && focus_timer.remaining_ms == running_duration);
    focus_toggle(&focus_timer, sim_tick); advance(500);
    focus_toggle(&focus_timer, sim_tick);
    advance(100);
    assert(strcmp(lv_label_get_text(focus_state), "PAUSED") == 0);
    focus_toggle(&focus_timer, sim_tick);
    advance(100);
    focus_action(8);
    assert(focus_timer.running && focus_timer.remaining_ms == running_duration);
    focus_action(9); assert(!lv_obj_has_flag(focus_preset_overlay, LV_OBJ_FLAG_HIDDEN));
    focus_action(10); focus_action(11);
    assert(focus_timer.duration_ms == 15u * 60000u && !focus_timer.running);
    assert(lv_obj_has_flag(focus_preset_overlay, LV_OBJ_FLAG_HIDDEN));
    focus_reset(&focus_timer); focus_timer.remaining_ms = 50;
    focus_timer.running = true; focus_timer.last_ms = sim_tick;
    advance(200);
    assert(focus_timer.finished && !focus_timer.running);
    assert(strcmp(lv_label_get_text(focus_state), "REST") == 0);
    assert(strcmp(lv_label_get_text(focus_minute_digits), "00") == 0);
    assert(strcmp(lv_label_get_text(focus_second_digits), "00") == 0);
    puts("LVGL: snap, timer wheels, edit lock, stop/restart, presets, disconnect, six HID actions PASS");
}
static void screenshot(const char *path) {
    /* Object snapshots omit the display top layer. Include the global power
     * overlay for this offscreen capture, then restore its runtime parent. */
    if (power_overlay) lv_obj_set_parent(power_overlay, lv_screen_active());
#if LVGL_VERSION_MAJOR >= 9
    lv_draw_buf_t *snap = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_ARGB8888);
    assert(snap && snap->data);
    SDL_Surface *s = SDL_CreateRGBSurfaceFrom(snap->data, snap->header.w, snap->header.h,
        32, snap->header.stride, 0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000);
    assert(s); assert(SDL_SaveBMP(s, path) == 0);
    SDL_FreeSurface(s); lv_draw_buf_destroy(snap);
#else
    lv_img_dsc_t *snap = lv_snapshot_take(lv_scr_act(), LV_IMG_CF_TRUE_COLOR);
    assert(snap && snap->data);
#if LV_COLOR_16_SWAP
    uint16_t *pixels = (uint16_t *)snap->data;
    for (unsigned i = 0; i < snap->header.w * snap->header.h; ++i)
        pixels[i] = (pixels[i] >> 8) | (pixels[i] << 8);
#endif
    SDL_Surface *s = SDL_CreateRGBSurfaceFrom((void *)snap->data, snap->header.w, snap->header.h,
        16, snap->header.w * 2, 0xF800, 0x07E0, 0x001F, 0);
    assert(s); assert(SDL_SaveBMP(s, path) == 0);
    SDL_FreeSurface(s); lv_snapshot_free(snap);
#endif
    if (power_overlay) lv_obj_set_parent(power_overlay, lv_layer_top());
    printf("Screenshot: %s\n", path);
}

static void power_tests(void);
static VoiceHostView sim_hosts;
static VoiceHostRequest sim_host_request;
static int sim_host_requests;
static bool sim_host_gate_open;
static void sim_host_read(VoiceHostView *v){*v=sim_hosts;}
static bool sim_host_submit(const VoiceHostRequest *r){if(sim_hosts.command_busy)return false;sim_host_request=*r;sim_host_requests++;return true;}
static bool sim_host_gate(bool open){sim_host_gate_open=open;return !open||!sim_hosts.command_busy;}
static void sim_host_setup(void){
 memset(&sim_hosts,0,sizeof(sim_hosts));sim_hosts.catalog=vh_init();sim_hosts.catalog.active=sim_hosts.catalog.target=sim_hosts.catalog.preferred=0;
 sim_hosts.ble_mode=true;sim_hosts.wifi=sim_hosts.ready=sim_hosts.connected=true;strcpy(sim_hosts.ssid,"Home Wi-Fi");strcpy(sim_hosts.status,"READY");
 const char *names[]={"My MacBook Pro","Study Mac mini","Office iMac","Living Room Mac","Mac Studio"};
 for(int i=0;i<5;i++){sim_hosts.catalog.hosts[i].present=true;snprintf(sim_hosts.catalog.hosts[i].id,37,"server-%d",i);snprintf(sim_hosts.catalog.hosts[i].name,VH_NAME_BYTES,"%s",names[i]);}
 sim_hosts.catalog.revision=1;vh_read=sim_host_read;vh_submit=sim_host_submit;vh_gate=sim_host_gate;
}
static void voice_host_ui_tests(void){
 focus_go_page(PAGE_BILI,false);advance(150);sim_host_setup();
 sim_bili=(FocusBiliStatus){0};sim_bili.state=9;sim_bili.result=1;
 strcpy(sim_bili.command_id,"aaaaaaaa-1111-4111-8111-111111111111");focus_bili_refresh();assert(!strcmp(focus_feedback_origin,"server-0"));focus_feedback_hide("unfeedback","test");
 sim_hosts.catalog.active=1;strcpy(sim_bili.command_id,"bbbbbbbb-1111-4111-8111-111111111111");focus_bili_refresh();assert(!strcmp(focus_feedback_origin,"server-1"));focus_feedback_hide("unfeedback","test");
 sim_hosts.catalog.active=0;sim_bili=(FocusBiliStatus){0};sim_bili.state=2;sim_bili.ready=1;advance(100);
 sim_hosts.command_busy=true;assert(!vh_open());sim_hosts.command_busy=false;
 sim_bili.state=4;sim_bili.rms=.02f;sim_bili.recording_ms=3000;advance(600);
 assert(lv_obj_get_height(focus_bili_wave[20])>4);assert(!strcmp(lv_label_get_text(focus_bili_timer),"00:03"));
 sim_bili.rms=0;advance(3000);for(int i=0;i<FOCUS_WAVE_BARS;i++)assert(lv_obj_get_height(focus_bili_wave[i])<=5);
 sim_bili.state=2;advance(100);assert(!focus_bili_wave[0]);
 assert(vh_open()&&sim_host_gate_open&&!focus_bili_state);assert(!lv_obj_has_flag(focus_pager,LV_OBJ_FLAG_SCROLLABLE));
 assert(vh_row_count<=8);vh_activate(VH_UI_NEXT_PAGE);assert(vh_page==1);
 vh_activate(3);assert(vh_mode==VH_UI_DETAIL);int submitted=sim_host_requests;
 vh_activate(VH_UI_DO_CONNECT);assert(vh_mode==VH_UI_SWITCH&&sim_host_requests==submitted);
 vh_key(0,false);assert(vh_mode==VH_UI_DETAIL); // default keep current
 vh_activate(VH_UI_DO_CONNECT);vh_activate(VH_UI_DO_CONNECT);
 assert(sim_host_request.op==VH_CONNECT&&!strcmp(sim_host_request.id,"server-3"));
 vh_activate(VH_UI_SETTINGS_ACTION);vh_activate(VH_UI_REMOVE_ACTION);vh_key(0,false);
 assert(vh_mode==VH_UI_SETTINGS&&sim_host_request.op==VH_CONNECT); // default keep
 vh_activate(VH_UI_RENAME_ACTION);lv_textarea_set_text(vh_input,"");
 for(uint32_t key=0;VH_KEY_TEXT(vh_keyboard,key);key++)if(!strcmp(VH_KEY_TEXT(vh_keyboard,key),"a")){VH_KEY_SET(vh_keyboard,key);vh_key(0,false);break;}
 assert(!strcmp(lv_textarea_get_text(vh_input),"a"));lv_textarea_set_text(vh_input,"Desk Mac");
#if LVGL_VERSION_MAJOR < 9
 lv_mem_monitor_t memory;lv_mem_monitor(&memory);printf("Voice rename heap: %u bytes\n",(unsigned)(memory.total_size-memory.free_size));assert(memory.total_size-memory.free_size<32768);
#endif
 // Global power feedback must fit above the largest voice management view.
 power_tests();
 vh_activate(VH_UI_SAVE_NAME);assert(sim_host_request.op==VH_RENAME&&!strcmp(sim_host_request.name,"Desk Mac"));
 // A saved identity disappearing while selected cannot retarget a reused slot.
 strcpy(sim_hosts.catalog.hosts[3].id,"replacement");sim_hosts.catalog.revision++;vh_tick();assert(vh_mode==VH_UI_LIST);
 vh_activate(VH_UI_ADD_ACTION);assert(vh_mode!=VH_UI_ADD);
 sim_hosts.catalog.hosts[4].present=false;sim_hosts.catalog.revision++;vh_tick();vh_activate(VH_UI_ADD_ACTION);
 sim_hosts.catalog.hosts[4].present=true;strcpy(sim_hosts.added_id,"server-4");sim_hosts.catalog.revision++;vh_tick();assert(vh_mode==VH_UI_ADDED);
 vh_activate(VH_UI_NETWORK_ACTION);assert(vh_mode==VH_UI_NETWORK&&vh_row_count<=8);
 vh_activate(VH_UI_USE_WIFI);assert(sim_host_request.op==VH_TRANSPORT_WIFI);
 vh_activate(VH_UI_USE_BLE);assert(sim_host_request.op==VH_TRANSPORT_BLE);
 vh_close();assert(!sim_host_gate_open&&focus_bili_state);assert(lv_obj_has_flag(focus_pager,LV_OBJ_FLAG_SCROLLABLE));
 vh_read=NULL;vh_submit=NULL;vh_gate=NULL;focus_go_page(PAGE_FOCUS,false);advance(100);
 puts("Voice manager UI: identities, confirmation, input ownership, waveform, keyboard, power overlay PASS");
}
static int fb_count;
static char fb_last_rating[24];
static char fb_last_cmd[40];
static void sim_feedback(const char *command_id, const char *rating,
                         const char *dismissed_by, int touch_count, int countdown) {
    (void)dismissed_by; (void)touch_count; (void)countdown;
    fb_count++;
    snprintf(fb_last_rating, sizeof(fb_last_rating), "%s", rating);
    snprintf(fb_last_cmd, sizeof(fb_last_cmd), "%s", command_id);
}

static void feedback_tests(void) {
    puts("feedback: overlay state-machine tests start");
    focus_ui_set_feedback_sink(sim_feedback);
    focus_go_page(PAGE_BILI, false); advance(200);
    focus_feedback_show("11111111-1111-4111-8111-111111111111");
    assert(!lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    assert(strcmp(lv_label_get_text(focus_feedback_count), "5s") == 0);
    BT_SEND_EVENT(focus_feedback_overlay, LV_EVENT_PRESSED, NULL);
    advance(3000);
    assert(!lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    BT_SEND_EVENT(focus_feedback_overlay, LV_EVENT_RELEASED, NULL);
    advance(1000);
    assert(!lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    advance(5000);
    assert(lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    assert(strcmp(fb_last_rating, "unfeedback") == 0);
    assert(fb_count == 1);

    focus_feedback_show("22222222-2222-4222-8222-222222222222");
    focus_feedback_select(true);
    assert(strcmp(fb_last_rating, "good") == 0);
    advance(2100);
    assert(lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    assert(fb_count == 2);
    /* Even after GOOD/BAD, a 30 s review badge remains. */
    assert(!lv_obj_has_flag(focus_feedback_badge, LV_OBJ_FLAG_HIDDEN));

    focus_feedback_show("33333333-3333-4333-8333-333333333333");
    focus_feedback_hide("unfeedback", "next_ptt");
    assert(lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    assert(!lv_obj_has_flag(focus_feedback_badge, LV_OBJ_FLAG_HIDDEN));
    assert(fb_count == 3);
    BT_SEND_EVENT(focus_feedback_badge, LV_EVENT_CLICKED, NULL);
    assert(!lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    focus_feedback_hide("unfeedback", "timeout");
    assert(fb_count == 4);

    focus_feedback_show("33333333-3333-4333-8333-333333333333");
    for (int i = 0; i < 3; ++i) {
        BT_SEND_EVENT(focus_feedback_overlay, LV_EVENT_PRESSED, NULL);
        BT_SEND_EVENT(focus_feedback_overlay, LV_EVENT_RELEASED, NULL);
    }
    /* The fourth touch must not reset the countdown. */
    BT_SEND_EVENT(focus_feedback_overlay, LV_EVENT_PRESSED, NULL);
    BT_SEND_EVENT(focus_feedback_overlay, LV_EVENT_RELEASED, NULL);
    advance(6000);
    assert(lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    /* RESULT (9) decaying to READY (2) must NOT close the 5 s overlay. */
    sim_bili.state = 9; sim_bili.result = 1;
    snprintf(sim_bili.command_id, sizeof(sim_bili.command_id), "%s",
             "55555555-5555-4555-8555-555555555555");
    focus_refresh();
    assert(!lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    sim_bili.state = 2; focus_refresh();
    assert(!lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    /* FAILED also opens the feedback window. */
    sim_bili.state = 9; sim_bili.result = 3;
    snprintf(sim_bili.command_id, sizeof(sim_bili.command_id), "%s",
             "77777777-7777-4777-8777-777777777777");
    focus_refresh();
    assert(!lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    /* A genuinely new command ID closes the old feedback and badge. */
    snprintf(sim_bili.command_id, sizeof(sim_bili.command_id), "%s",
             "66666666-6666-4666-8666-666666666666");
    sim_bili.state = 3; focus_refresh();
    assert(lv_obj_has_flag(focus_feedback_overlay, LV_OBJ_FLAG_HIDDEN));
    focus_feedback_hide("unfeedback", "timeout");
    focus_feedback_hide("unfeedback", "timeout");
    sim_bili = (FocusBiliStatus){};
    lv_obj_scroll_to_x(focus_pager, 0, LV_ANIM_OFF);
    focus_page = PAGE_FOCUS;
    advance(200);
    puts("feedback: overlay state-machine tests PASS");
}
static unsigned bt_peak_memory;
static void check_bt_memory(const char *stage) {
#if LVGL_VERSION_MAJOR < 9
    lv_mem_monitor_t memory; lv_mem_monitor(&memory);
    unsigned used=memory.total_size-memory.free_size;
    if(used>bt_peak_memory) { bt_peak_memory=used; printf("BLE UI heap peak %u bytes (%s)\n",used,stage); }
    assert(used < 32u*1024u);
#else
    (void)stage;
#endif
}
static void power_tests(void) {
    power_ui_input(POWER_KEY_DOWN);
    advance(900); assert(!power_overlay);
    advance(200); assert(power_overlay);
    assert(lv_obj_get_parent(power_overlay) == lv_layer_top());
    assert(!strcmp(lv_label_get_text(power_seconds), "0s"));
    assert(lv_obj_get_style_text_font(power_seconds, LV_PART_MAIN)->line_height >= 48);
    int initial_arc = lv_arc_get_value(power_ring);
    advance(40);
    assert(lv_arc_get_value(power_ring) > initial_arc);
    assert(!strcmp(lv_label_get_text(power_seconds), "0s"));
    advance(1360);
    assert(lv_arc_get_value(power_ring) >= 1480 && lv_arc_get_value(power_ring) <= 1500);
    assert(!strcmp(lv_label_get_text(power_seconds), "1s"));
    assert(!power_ui_input(POWER_KEY_UP | POWER_KEY_SHORT));
    assert(!strcmp(lv_label_get_text(power_hint), "POWER OFF CANCELLED"));
    check_bt_memory("power overlay");
    int before_media = media_count;
    int before_bt = sim_bt_requests;
    pointer(240, 340, true, 30); pointer(240, 340, false, 30);
    assert(media_count == before_media && sim_bt_requests == before_bt);
    advance(440);
    assert(lv_arc_get_value(power_ring) >= 720 && lv_arc_get_value(power_ring) <= 780);
    advance(500); power_ui_input(0); assert(lv_arc_get_value(power_ring) == 0);
    advance(500);
    assert(power_overlay && lv_obj_get_style_opa(power_overlay, 0) < 150);
    advance(550); assert(!power_overlay);
    /* Repeated cancellation and new presses do not retain stale animations. */
    for (int i = 0; i < 3; ++i) {
        power_ui_input(POWER_KEY_DOWN); advance(2200); assert(power_overlay);
        power_ui_input(POWER_KEY_UP); advance(200);
        power_ui_input(POWER_KEY_DOWN); assert(!power_overlay);
        advance(4020); assert(lv_arc_get_value(power_ring) == 3000);
        assert(!strcmp(lv_label_get_text(power_seconds), "3s"));
        power_ui_input(POWER_KEY_UP); advance(3050); assert(!power_overlay);
    }
    puts("power UI: hold, touch isolation, rewind, fade, re-press PASS");
}
static void bluetooth_tests(void) {
    memset(&sim_bt,0,sizeof(sim_bt)); sim_bt.enabled=true; sim_bt.auto_reconnect=true;
    sim_bt.capacity=8; sim_bt.count=8; sim_bt.current=0; sim_bt.selected=0;
    sim_bt.handle=1; sim_bt.generation=1; sim_bt.state=BLE_READY;
    const char *names[]={"My Mac", "Windows PC", "My Phone", "Tablet", "Office Mac", "Linux PC", "Spare Phone", "Living Room PC"};
    for(int i=0;i<8;i++) {
        snprintf(sim_bt.hosts[i].name,BLE_NAME_BYTES,"%s",names[i]);
        snprintf(sim_bt.hosts[i].address,18,"aa:00:00:00:00:0%d",i+1);
    }
    bt_bind(lv_screen_active(),focus_pager,sim_bt_read,sim_bt_submit);
    focus_go_page(PAGE_MAC,false); advance(100);
    bt_open(); advance(100); bt_tick();
    assert(strstr(lv_label_get_text(bt_count),"8 / 8"));
    assert(bt_list_page==0&&bt_page_count()==4);
    assert(lv_obj_has_state(bt_rows[4],LV_STATE_DISABLED));
    for(int page=0;page<4;page++) {
        advance(100); bt_tick();
        assert(bt_list_page==page);
        assert(strstr(lv_label_get_text(bt_row_labels[2]),names[page*2]));
        assert(strstr(lv_label_get_text(bt_row_labels[3]),names[page*2+1]));
        if(page<3) bt_activate(BT_NEXT_PAGE);
    }
    assert(lv_obj_has_state(bt_rows[5],LV_STATE_DISABLED));
    bt_selected=3; bt_key(0,false); assert(bt_screen_mode==BT_DETAIL);
    bt_activate(BT_CONNECT); assert(!strcmp(sim_bt_request.address,sim_bt.hosts[7].address));
    bt_back(); assert(bt_list_page==3);
    sim_bt.count=7; bt_tick(); advance(100); assert(bt_list_page==3&&bt_row_count==6);
    bt_activate(BT_PREV_PAGE); assert(bt_list_page==2);
    bt_activate(BT_NEXT_PAGE); assert(bt_list_page==3);
    sim_bt.count=6; bt_tick(); assert(bt_list_page==2&&bt_page_count()==3);
    sim_bt.count=8; bt_tick(); bt_activate(BT_PREV_PAGE); bt_activate(BT_PREV_PAGE);
    assert(bt_list_page==0);
    bt_selected=3; bt_key(10,false); assert(bt_selected==5); // skip disabled Prev
    bt_key(0,false); assert(bt_list_page==1); // physical Next
    advance(100);
    pointer(150,326,true,80); pointer(150,326,false,100); assert(bt_list_page==0);
    for(int i=0;i<3;i++) { pointer(330,326,true,80); pointer(330,326,false,100); }
    assert(bt_list_page==3);
    pointer(240,272,true,80); pointer(240,272,false,100); assert(bt_screen_mode==BT_DETAIL);
    assert(!strcmp(bt_host.address,sim_bt.hosts[7].address));
    bt_back();
    // A row retains its drawn identity if a radio snapshot reorders the array before tap.
    BleHost swap=sim_bt.hosts[6]; sim_bt.hosts[6]=sim_bt.hosts[7]; sim_bt.hosts[7]=swap;
    bt_activate(7); bt_activate(BT_CONNECT);
    assert(!strcmp(sim_bt_request.address,sim_bt.hosts[6].address));
    swap=sim_bt.hosts[6]; sim_bt.hosts[6]=sim_bt.hosts[7]; sim_bt.hosts[7]=swap;
    bt_close();
    int before_media=media_count;
    for(int n=0;n<20;n++) {
        bt_open(); bt_tick(); assert(bt_is_open()); check_bt_memory("list");
        assert(!lv_obj_has_flag(focus_pager,LV_OBJ_FLAG_SCROLLABLE));
        focus_action(0); assert(media_count==before_media);
        bt_activate(2); assert(bt_screen_mode==BT_DETAIL); check_bt_memory("detail");
        bt_activate(BT_CONNECT); assert(sim_bt_request.op==BLE_CMD_CONNECT);
        assert(!strcmp(sim_bt_request.address,sim_bt.hosts[2].address));
        bt_activate(BT_FORGET_ACTION); assert(bt_screen_mode==BT_FORGET); check_bt_memory("confirmation");
        int submitted=sim_bt_requests;
        bt_key(0,false); assert(bt_screen_mode==BT_DETAIL && sim_bt_requests==submitted); // default Cancel
        bt_activate(BT_RENAME_ACTION); assert(bt_screen_mode==BT_RENAME); check_bt_memory("keyboard");
        if (n == 0) power_tests();
        lv_textarea_set_text(bt_name_input,"Desk Phone"); bt_activate(BT_SAVE_NAME);
        assert(sim_bt_request.op==BLE_CMD_RENAME && !strcmp(sim_bt_request.name,"Desk Phone"));
#if LVGL_VERSION_MAJOR < 9
        lv_mem_monitor_t memory; lv_mem_monitor(&memory);
        assert(memory.total_size-memory.free_size < 32u*1024u);
#endif
        bt_back(); bt_close(); assert(!bt_is_open());
        assert(lv_obj_has_flag(focus_pager,LV_OBJ_FLAG_SCROLLABLE));
    }
    bt_open(); int full_requests=sim_bt_requests;
    bt_activate(BT_ADD); assert(bt_screen_mode==BT_LIST&&sim_bt_requests==full_requests); // capacity full
    advance(100); bt_tick(); assert(strstr(lv_label_get_text(bt_footer),"All slots used"));
    sim_bt.count=2; sim_bt.handle=BLE_NO_HANDLE; sim_bt.state=BLE_IDLE; bt_tick();
    bt_activate(BT_ADD); assert(bt_screen_mode==BT_PAIR && sim_bt_request.op==BLE_CMD_PAIR);
    bt_tick(); assert(bt_screen_mode==BT_PAIR); // stale prior snapshot cannot complete pairing
    sim_bt.state=BLE_PAIRING; sim_bt.window_id++; sim_bt.advertising=true; sim_bt.remaining_ms=120000; bt_tick();
    assert(strstr(lv_label_get_text(bt_status),"120s"));
    bt_back(); assert(sim_bt_request.op==BLE_CMD_CANCEL);
    bt_activate(BT_ENABLE); assert(sim_bt_request.op==BLE_CMD_ENABLE && !sim_bt_request.value);
    bt_activate(BT_AUTO); assert(sim_bt_request.op==BLE_CMD_AUTO && !sim_bt_request.value);
    bt_close();
    // Exercise the real pointer event path while a clicked button rebuilds its parent view.
    sim_bt.count=2; sim_bt.handle=1; sim_bt.current=sim_bt.selected=0; sim_bt.state=BLE_READY;
    focus_go_page(PAGE_MAC,false); advance(100);
    pointer(240,94,true,80); pointer(240,94,false,100);
    assert(bt_is_open()); bt_tick();
    pointer(240,380,true,80); pointer(240,380,false,100);
    assert(bt_screen_mode==BT_ADD_CONFIRM);
    pointer(240,380,true,80); pointer(240,380,false,100);
    assert(bt_screen_mode==BT_PAIR && sim_bt_request.op==BLE_CMD_PAIR);
    sim_bt.state=BLE_PAIRING; sim_bt.current=-1; sim_bt.handle=BLE_NO_HANDLE;
    sim_bt.window_id++; sim_bt.advertising=true; sim_bt.remaining_ms=120000;
    focus_set_connected(false); advance(1000);
    assert(bt_screen_mode==BT_PAIR && !strcmp(lv_label_get_text(bt_status),"120s remaining"));
    // A new bond appears in the list immediately; naming is part of completion.
    sim_bt.count=3; sim_bt.current=sim_bt.selected=2; sim_bt.generation++;
    sim_bt.state=BLE_READY; sim_bt.handle=2;
    snprintf(sim_bt.hosts[2].name,BLE_NAME_BYTES,"Device 0003"); sim_bt.hosts[2].name_kind=BLE_NAME_DEFAULT;
    bt_tick(); assert(bt_screen_mode==BT_NAME_CHOICES); check_bt_memory("new device name");
    bt_activate(BT_NAME_IPHONE);
    assert(sim_bt_request.op==BLE_CMD_RENAME && !strcmp(sim_bt_request.name,"iPhone"));
    assert(bt_screen_mode==BT_LIST && bt_known_count==3);
    snprintf(sim_bt.hosts[2].name,BLE_NAME_BYTES,"iPhone"); advance(300); bt_tick();
    bt_activate(BT_NEXT_PAGE); advance(100); bt_tick();
    assert(strstr(lv_label_get_text(bt_row_labels[2]),"iPhone"));
    // Back during optional name lookup keeps the successful connection.
    sim_bt.count=2; sim_bt.state=BLE_IDLE; sim_bt.handle=BLE_NO_HANDLE;
    bt_tick(); bt_activate(BT_ADD);
    sim_bt.window_id++; sim_bt.count=3; sim_bt.current=sim_bt.selected=2;
    sim_bt.state=BLE_READY; sim_bt.handle=2; sim_bt.generation++; sim_bt.name_pending=true;
    bt_tick(); assert(bt_screen_mode==BT_PAIR);
    int before_back=sim_bt_requests; bt_back();
    assert(bt_screen_mode==BT_LIST&&sim_bt_requests==before_back);
    // An actual peer name is presented and saved without forcing a preset.
    sim_bt.count=2; sim_bt.state=BLE_IDLE; sim_bt.handle=BLE_NO_HANDLE; sim_bt.name_pending=false;
    bt_tick(); bt_activate(BT_ADD);
    sim_bt.window_id++; sim_bt.count=3; sim_bt.current=sim_bt.selected=2;
    sim_bt.state=BLE_READY; sim_bt.handle=2; sim_bt.generation++;
    sim_bt.hosts[2].name_kind=BLE_NAME_REMOTE;
    snprintf(sim_bt.hosts[2].name,BLE_NAME_BYTES,"Actual Phone");
    bt_tick(); assert(bt_screen_mode==BT_PAIRED); bt_activate(BT_DONE);
    assert(bt_screen_mode==BT_LIST&&bt_known_count==3);
    bt_close(); bt_read=NULL; bt_submit=NULL;
    puts("Bluetooth UI: identity-bound actions, confirmation, rename, capacity, modal isolation, lifecycle PASS");
}
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    bool test = argc > 1 && strcmp(argv[1], "--test") == 0;
    bool capture = argc > 1 && strcmp(argv[1], "--screenshot") == 0;
    lv_init();
#if LVGL_VERSION_MAJOR >= 9
    if (test || capture) {
        lv_tick_set_cb(test_clock);
        lv_display_t *d = lv_display_create(480, 480);
        static uint8_t buffer[480 * 40 * 4];
        lv_display_set_buffers(d, buffer, NULL, sizeof(buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
        lv_display_set_flush_cb(d, flush);
        lv_indev_t *indev = lv_indev_create();
        lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
        lv_indev_set_read_cb(indev, read_pointer);
    } else {
        lv_sdl_window_create(480, 480);
        lv_sdl_window_set_title(lv_display_get_default(), "Desk-Mate / Focus + Mac");
        lv_sdl_mouse_create();
    }
#else
    if (!test && !capture) { fprintf(stderr, "Use LVGL 9 simulator for interactive mode.\n"); return 2; }
    static lv_color_t pixels[480 * 10];
    static lv_disp_draw_buf_t buf;
    static lv_disp_drv_t display;
    static lv_indev_drv_t input;
    lv_disp_draw_buf_init(&buf, pixels, NULL, 480 * 10);
    lv_disp_drv_init(&display);
    display.hor_res = display.ver_res = 480;
    display.draw_buf = &buf; display.flush_cb = flush;
    lv_disp_drv_register(&display);
    lv_indev_drv_init(&input); input.type = LV_INDEV_TYPE_POINTER; input.read_cb = read_pointer;
    lv_indev_drv_register(&input);
#endif
    focus_ui_build(lv_screen_active(), sim_media, sim_battery);
    focus_ui_set_bili_source(sim_read_bili);
#if LVGL_VERSION_MAJOR < 9
    lv_mem_monitor_t memory;
    lv_mem_monitor(&memory);
    printf("Hardware view LVGL heap: %u bytes\n", (unsigned)(memory.total_size - memory.free_size));
    assert(memory.total_size - memory.free_size < 48u * 1024u);
#endif
    assert(strcmp(lv_label_get_text(focus_battery), "85%") == 0);
    if (test) { run_tests(); feedback_tests(); power_tests(); bluetooth_tests(); voice_host_ui_tests(); return 0; }
    if (capture) {
        int page = argc > 2 ? atoi(argv[2]) : 0;
        if (argc > 4 && strcmp(argv[4], "connected") == 0) {
            focus_set_connected(true); focus_toggle(&focus_timer, sim_tick); advance(1000);
        }
        focus_go_page(page, false); advance(300);
        if (page == PAGE_BILI && !(argc>4&&!strncmp(argv[4],"voice",5))) {
            focus_feedback_show("44444444-4444-4444-8444-444444444444");
            advance(100);
        }
        if (argc > 4 && !strncmp(argv[4], "bluetooth", 9)) {
            memset(&sim_bt,0,sizeof(sim_bt)); sim_bt.enabled=true; sim_bt.auto_reconnect=true;
            sim_bt.capacity=8; sim_bt.count=8; sim_bt.selected=sim_bt.current=0;
            sim_bt.handle=1; sim_bt.generation=1; sim_bt.state=BLE_READY;
            const char *names[]={"My Mac", "Windows PC", "My Phone", "Tablet", "Office Mac", "Linux PC", "Spare Phone", "Living Room PC"};
            for(int i=0;i<8;i++) { snprintf(sim_bt.hosts[i].name,BLE_NAME_BYTES,"%s",names[i]); snprintf(sim_bt.hosts[i].address,18,"aa:00:00:00:00:0%d",i+1); }
            bt_bind(lv_screen_active(),focus_pager,sim_bt_read,sim_bt_submit);
            focus_refresh();
            if(strcmp(argv[4],"bluetooth-media")) {
                bt_open();
                if(!strcmp(argv[4],"bluetooth-last")) { bt_list_page=3; bt_draw(BT_LIST); }
                if(!strcmp(argv[4],"bluetooth-detail")) bt_activate(1);
                if(!strcmp(argv[4],"bluetooth-rename")) { bt_activate(1); bt_activate(BT_RENAME_ACTION); }
                if(!strcmp(argv[4],"bluetooth-pair")) { sim_bt.count=2; sim_bt.handle=BLE_NO_HANDLE; sim_bt.state=BLE_PAIRING; sim_bt.advertising=true; sim_bt.remaining_ms=120000; bt_draw(BT_PAIR); sim_bt.window_id++; }
                if(!strcmp(argv[4],"bluetooth-names")) { bt_host=sim_bt.hosts[0]; bt_name_after_pair=true; bt_draw(BT_NAME_CHOICES); }
                if(!strcmp(argv[4],"bluetooth-added")) { bt_host=sim_bt.hosts[2]; bt_name_after_pair=true; bt_draw(BT_PAIRED); }
                bt_tick(); advance(100);
            }
        }
        if(argc>4&&!strncmp(argv[4],"voice",5)){
            sim_host_setup();sim_bili=(FocusBiliStatus){0};sim_bili.state=2;sim_bili.ready=1;sim_bili.connected=1;advance(100);
            if(!strcmp(argv[4],"voice-recording")){sim_bili.state=4;sim_bili.recording_ms=3000;sim_bili.rms=.015f;advance(700);}
            else if(!strcmp(argv[4],"voice-cancel")){sim_bili.state=5;sim_bili.cancel_remaining_ms=700;advance(100);}
            else if(strcmp(argv[4],"voice-home")){
                vh_open();if(!strcmp(argv[4],"voice-detail"))vh_activate(1);
                if(!strcmp(argv[4],"voice-rename")){vh_activate(1);vh_activate(VH_UI_SETTINGS_ACTION);vh_activate(VH_UI_RENAME_ACTION);}
                if(!strcmp(argv[4],"voice-network"))vh_activate(VH_UI_NETWORK_ACTION);
            }
        }
        if (argc > 4 && !strncmp(argv[4], "power", 5)) {
            power_ui_input(POWER_KEY_DOWN); advance(2800);
            if (!strcmp(argv[4], "power-cancel")) { power_ui_input(POWER_KEY_UP); advance(400); }
        }
        screenshot(argc > 3 ? argv[3] : "/private/tmp/deskmate.bmp");
        return 0;
    }
    while (1) { lv_timer_handler(); SDL_Delay(5); }
}
