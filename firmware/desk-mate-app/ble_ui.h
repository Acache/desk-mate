#ifndef DESKMATE_BLE_UI_H
#define DESKMATE_BLE_UI_H
#include "lvgl.h"
#include "ui_update.h"
#include "src/media_ble/ble_contracts.h"
#include <string.h>
#include <stdio.h>
/* No Arduino/NimBLE dependency: snapshots in, identity-bound requests out. */
static BleReadSnapshot bt_read;
static BleSubmitRequest bt_submit;
static void (*bt_visibility)(bool open);
static void (*bt_closed)(void);
static BleSnapshot bt_view;
static lv_obj_t *bt_screen,*bt_pager,*bt_panel,*bt_status,*bt_footer,*bt_rows[8],*bt_row_labels[8];
static lv_obj_t *bt_name_input,*bt_keyboard,*bt_detail_title;
static lv_obj_t *bt_count;
static BleHost bt_host;
static BleHost bt_list_hosts[BLE_HOST_LIMIT];
static int bt_screen_mode,bt_row_count,bt_selected,bt_known_count;
static int bt_list_page;
static int32_t bt_return_scroll_x, bt_return_scroll_y;
static uint32_t bt_ui_generation, bt_pair_generation,bt_pair_window;
static bool bt_wait_pair,bt_name_after_pair;
static char bt_ui_error[64];
enum { BT_LIST=1,BT_DETAIL,BT_PAIR,BT_FORGET,BT_ADD_CONFIRM,BT_RENAME,BT_PAIRED,BT_NAME_CHOICES };
enum { BT_BACK=100,BT_ENABLE,BT_AUTO,BT_ADD,BT_CONNECT,BT_DISCONNECT,BT_RENAME_ACTION,BT_FORGET_ACTION,BT_CONFIRM_FORGET,BT_CONFIRM_ADD,BT_SAVE_NAME,BT_NAMES,BT_DONE,BT_NAME_MAC,BT_NAME_WINDOWS,BT_NAME_IPHONE,BT_NAME_ANDROID,BT_NAME_HARMONY,BT_PREV_PAGE,BT_NEXT_PAGE };
#if LVGL_VERSION_MAJOR >= 9
#define BT_DELETE lv_obj_delete
#define BT_SEND_EVENT lv_obj_send_event
#define BT_MATRIX_SELECTED lv_buttonmatrix_get_selected_button
#define BT_MATRIX_TEXT lv_buttonmatrix_get_button_text
#define BT_MATRIX_SET lv_buttonmatrix_set_selected_button
#else
#define BT_DELETE lv_obj_del
#define BT_SEND_EVENT lv_event_send
#define BT_MATRIX_SELECTED lv_btnmatrix_get_selected_btn
#define BT_MATRIX_TEXT lv_btnmatrix_get_btn_text
#define BT_MATRIX_SET lv_btnmatrix_set_selected_btn
#endif
static void bt_draw(int mode);
static bool bt_is_open(void) { return bt_panel!=NULL; }
static uint32_t bt_input_generation(void) { return bt_ui_generation; }
static void bt_refresh_snapshot(void) { if(bt_read) bt_read(&bt_view); }
static int bt_page_count(void) { return bt_view.count?(bt_view.count+1)/2:1; }
static int bt_host_index(void) {
    for(int i=0;i<bt_view.count;i++) if(bt_host.type==bt_view.hosts[i].type&&!strcmp(bt_host.address,bt_view.hosts[i].address)) return i;
    return -1;
}
static bool bt_send(BleOperation op,bool value,const char *name) {
    BleRequest r; memset(&r,0,sizeof(r)); r.op=op; r.value=value; r.type=bt_host.type;
    snprintf(r.address,sizeof(r.address),"%s",bt_host.address);
    if(name) snprintf(r.name,sizeof(r.name),"%s",name);
    bool ok=bt_submit&&bt_submit(&r);
    snprintf(bt_ui_error,sizeof(bt_ui_error),"%s",ok?"":"Bluetooth busy; please try again");
    return ok;
}
static lv_obj_t *bt_text(const char *text,int x,int y,int w,const lv_font_t *font,uint32_t color) {
    lv_obj_t *o=lv_label_create(bt_panel); ui_label_set_text(o,text);
    lv_obj_set_pos(o,x,y); lv_obj_set_width(o,w); lv_obj_set_style_text_font(o,font,0);
    lv_obj_set_style_text_color(o,lv_color_hex(color),0); lv_obj_set_style_text_align(o,LV_TEXT_ALIGN_CENTER,0);
    lv_label_set_long_mode(o,LV_LABEL_LONG_WRAP); return o;
}
static void bt_close(void) {
    if(!bt_panel) return;
    if(bt_screen_mode==BT_PAIR&&!(bt_view.state==BLE_READY&&bt_view.generation!=bt_pair_generation)) bt_send(BLE_CMD_CANCEL,false,NULL);
    BT_DELETE(bt_panel); bt_panel=NULL;
    if(bt_visibility) bt_visibility(false); bt_name_input=bt_keyboard=NULL; bt_ui_generation++;
    /* Rebuilding the media view can readjust the pager's layout/snap. Finish
     * that work and restore its saved position before exposing another frame. */
    lv_obj_update_layout(bt_pager);
    lv_obj_scroll_to(bt_pager, bt_return_scroll_x, bt_return_scroll_y, LV_ANIM_OFF);
    lv_obj_add_flag(bt_pager,LV_OBJ_FLAG_SCROLLABLE);
    if(bt_closed) bt_closed();
}
static void bt_back(void) {
    if(bt_screen_mode==BT_LIST) bt_close();
    else if(bt_screen_mode==BT_PAIR) {
        if(!(bt_view.state==BLE_READY&&bt_view.generation!=bt_pair_generation)) bt_send(BLE_CMD_CANCEL,false,NULL);
        bt_draw(BT_LIST);
    }
    else if(bt_screen_mode==BT_RENAME&&bt_name_after_pair) bt_draw(BT_NAME_CHOICES);
    else if(bt_screen_mode==BT_PAIRED||bt_screen_mode==BT_NAME_CHOICES) { bt_name_after_pair=false; bt_draw(BT_LIST); }
    else if(bt_screen_mode==BT_FORGET||bt_screen_mode==BT_RENAME) bt_draw(BT_DETAIL);
    else bt_draw(BT_LIST);
}
static void bt_activate(int action) {
    bt_refresh_snapshot();
    if(action>=0&&action<BLE_HOST_LIMIT) {
        if(action<bt_known_count) {
            bt_host=bt_list_hosts[action];
            if(bt_host_index()>=0) bt_draw(BT_DETAIL);
            else { bt_draw(BT_LIST); snprintf(bt_ui_error,sizeof(bt_ui_error),"Device removed; choose again"); }
        }
        return;
    }
    switch(action) {
    case BT_PREV_PAGE:
        if(bt_list_page>0) { bt_list_page--; bt_draw(BT_LIST); }
        break;
    case BT_NEXT_PAGE:
        if(bt_list_page+1<bt_page_count()) { bt_list_page++; bt_draw(BT_LIST); }
        break;
    case BT_BACK: bt_back(); break;
    case BT_ENABLE: bt_send(BLE_CMD_ENABLE,!bt_view.enabled,NULL); break;
    case BT_AUTO: bt_send(BLE_CMD_AUTO,!bt_view.auto_reconnect,NULL); break;
    case BT_ADD:
        if(!bt_view.enabled) snprintf(bt_ui_error,sizeof(bt_ui_error),"Resume Bluetooth before adding a host");
        else if(bt_view.count>=bt_view.capacity) snprintf(bt_ui_error,sizeof(bt_ui_error),"All slots used. Open a host to forget it.");
        else if(bt_view.handle!=BLE_NO_HANDLE) bt_draw(BT_ADD_CONFIRM);
        else if(bt_send(BLE_CMD_PAIR,false,NULL)) bt_draw(BT_PAIR);
        break;
    case BT_CONFIRM_ADD: if(bt_send(BLE_CMD_PAIR,false,NULL)) bt_draw(BT_PAIR); break;
    case BT_CONNECT: bt_send(BLE_CMD_CONNECT,false,NULL); break;
    case BT_DISCONNECT:
        if(bt_host_index()==bt_view.current||bt_host_index()==bt_view.selected) bt_send(BLE_CMD_DISCONNECT,false,NULL);
        else snprintf(bt_ui_error,sizeof(bt_ui_error),"This device is not connected");
        break;
    case BT_RENAME_ACTION: bt_draw(BT_RENAME); break;
    case BT_NAMES: bt_draw(BT_NAME_CHOICES); break;
    case BT_DONE: bt_name_after_pair=false; bt_draw(BT_LIST); break;
    case BT_NAME_MAC: case BT_NAME_WINDOWS: case BT_NAME_IPHONE: case BT_NAME_ANDROID: case BT_NAME_HARMONY: {
        static const char *names[]={"Mac","Windows PC","iPhone","Android phone","Harmony phone"};
        if(bt_send(BLE_CMD_RENAME,false,names[action-BT_NAME_MAC])) { bt_name_after_pair=false; bt_draw(BT_LIST); }
        break;
    }
    case BT_FORGET_ACTION: bt_draw(BT_FORGET); break;
    case BT_CONFIRM_FORGET: if(bt_send(BLE_CMD_FORGET,false,NULL)) bt_draw(BT_LIST); break;
    case BT_SAVE_NAME:
        if(bt_name_input) {
            const char *name=lv_textarea_get_text(bt_name_input);
            if(!name[0]) snprintf(bt_ui_error,sizeof(bt_ui_error),"Enter a name first");
            else if(bt_send(BLE_CMD_RENAME,false,name)) { bool paired=bt_name_after_pair; bt_name_after_pair=false; bt_draw(paired?BT_LIST:BT_DETAIL); }
        }
        break;
    }
}
static void bt_click(lv_event_t *e) {
    if(lv_event_get_code(e)==LV_EVENT_CLICKED) bt_activate((int)(intptr_t)lv_event_get_user_data(e));
}
static void bt_keyboard_event(lv_event_t *e) {
    if(lv_event_get_code(e)==LV_EVENT_READY) bt_activate(BT_SAVE_NAME);
    else if(lv_event_get_code(e)==LV_EVENT_CANCEL) bt_back();
}
static void bt_button_at(const char *text,int x,int y,int w,int h,int action,uint32_t color) {
    if(bt_row_count>=8) return;
    int n=bt_row_count++;
    lv_obj_t *b=lv_obj_create(bt_panel); lv_obj_remove_style_all(b);
    lv_obj_set_pos(b,x,y); lv_obj_set_size(b,w,h);
    lv_obj_clear_flag(b,LV_OBJ_FLAG_SCROLLABLE|LV_OBJ_FLAG_SCROLL_ON_FOCUS);
    lv_obj_set_style_radius(b,16,0); lv_obj_set_style_bg_color(b,lv_color_hex(0x151B20),0);
    lv_obj_set_style_bg_opa(b,LV_OPA_COVER,0); lv_obj_set_style_bg_color(b,lv_color_hex(0x2A3946),LV_STATE_PRESSED);
    lv_obj_add_flag(b,LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(b,bt_click,LV_EVENT_CLICKED,(void *)(intptr_t)action);
    lv_obj_t *l=lv_label_create(b); ui_label_set_text(l,text); lv_obj_set_width(l,w-24);
    lv_label_set_long_mode(l,LV_LABEL_LONG_DOT); lv_obj_set_style_text_align(l,LV_TEXT_ALIGN_CENTER,0);
    lv_obj_set_style_text_font(l,&lv_font_montserrat_16,0); lv_obj_set_style_text_color(l,lv_color_hex(color),0);
    lv_obj_center(l); lv_obj_clear_flag(l,LV_OBJ_FLAG_CLICKABLE);
    bt_rows[n]=b; bt_row_labels[n]=l;
}
static void bt_button(const char *text,int y,int action,uint32_t color) {
    bt_button_at(text,68,y,344,46,action,color);
}
static void bt_disable_last(void) {
    lv_obj_add_state(bt_rows[bt_row_count-1],LV_STATE_DISABLED);
    lv_obj_set_style_opa(bt_rows[bt_row_count-1],LV_OPA_40,LV_STATE_DISABLED);
}
static void bt_highlight(void) {
    for(int n=0;n<bt_row_count;n++) {
        lv_obj_set_style_border_width(bt_rows[n],n==bt_selected?1:0,0);
        lv_obj_set_style_border_color(bt_rows[n],lv_color_hex(0x8CBCE5),0);
    }
}
static void bt_draw(int mode) {
    if (mode == BT_PAIR) { bt_pair_generation=bt_view.generation; bt_pair_window=bt_view.window_id; bt_wait_pair=true; bt_name_after_pair=false; }
    bt_ui_generation++; bt_screen_mode=mode; bt_selected=0; bt_row_count=0;
    bt_name_input=bt_keyboard=bt_detail_title=NULL; bt_status=bt_footer=bt_count=NULL;
    bt_ui_error[0]=0; bt_refresh_snapshot(); bt_known_count=bt_view.count;
    lv_obj_clean(bt_panel);
    bt_text(mode==BT_LIST?"BLUETOOTH":mode==BT_DETAIL?"YOUR DEVICE":mode==BT_PAIR?"ADD A DEVICE":mode==BT_RENAME||mode==BT_NAME_CHOICES?"DEVICE NAME":mode==BT_PAIRED?"DEVICE ADDED":"CONFIRM",80,48,320,&lv_font_montserrat_24,0xF3EFE7);
    if(mode==BT_LIST) {
        if(bt_list_page>=bt_page_count()) bt_list_page=bt_page_count()-1;
        memcpy(bt_list_hosts,bt_view.hosts,sizeof(bt_list_hosts));
        bt_count=bt_text("",64,80,352,&lv_font_montserrat_14,0x8CBCE5);
        bt_button_at("",68,104,344,40,BT_ENABLE,0xF3EFE7);
        bt_button_at("",68,150,344,40,BT_AUTO,0xF3EFE7);
        for(int n=0;n<2;n++) {
            int i=bt_list_page*2+n;
            if(i<bt_view.count) bt_button(bt_view.hosts[i].name,200+n*50,i,0x8CBCE5);
        }
        if(bt_page_count()>1) {
            bt_button_at("Prev",78,306,154,42,BT_PREV_PAGE,0x8CBCE5);
            if(!bt_list_page) bt_disable_last();
            bt_button_at("Next",248,306,154,42,BT_NEXT_PAGE,0x8CBCE5);
            if(bt_list_page+1==bt_page_count()) bt_disable_last();
        }
        if(!bt_view.count) bt_text("No saved devices\nAdd your first computer or phone",76,212,328,&lv_font_montserrat_16,0x858883);
        bt_button(bt_view.count>=bt_view.capacity?"All 8 slots used":"+ Add device",356,BT_ADD,0x8CBCE5);
        bt_footer=bt_text("",82,407,316,&lv_font_montserrat_14,0x858883);
        lv_obj_set_height(bt_footer,20); lv_label_set_long_mode(bt_footer,LV_LABEL_LONG_DOT);
    } else if(mode==BT_DETAIL) {
        int i=bt_host_index(); if(i>=0) bt_host=bt_view.hosts[i];
        bt_detail_title=bt_text(bt_host.name,70,90,340,&lv_font_montserrat_24,0xF3EFE7);
        bt_text(bt_host.address,80,124,320,&lv_font_montserrat_14,0x858883);
        bt_status=bt_text("",70,151,340,&lv_font_montserrat_14,0x8CBCE5);
        bt_button("Connect / retry",188,BT_CONNECT,0x8CBCE5);
        bt_button("Disconnect",240,BT_DISCONNECT,0xF3EFE7);
        bt_button("Device name",292,BT_NAMES,0xF3EFE7);
        bt_button("Forget this device",344,BT_FORGET_ACTION,0xE8B66D);
        bt_footer=bt_text("",82,401,316,&lv_font_montserrat_14,0x858883);
    } else if(mode==BT_PAIR) {
        bt_text("Desk-Mate",65,114,350,&lv_font_montserrat_48,0xF3EFE7);
        bt_status=bt_text("",70,187,340,&lv_font_montserrat_24,0x8CBCE5);
        bt_text("Open Bluetooth settings on your\ncomputer or phone. Select Desk-Mate.",70,246,340,&lv_font_montserrat_16,0x858883);
        bt_button("Cancel pairing",349,BT_BACK,0xF3EFE7);
        bt_footer=bt_text("",82,404,316,&lv_font_montserrat_14,0x858883);
    } else if(mode==BT_FORGET||mode==BT_ADD_CONFIRM) {
        bt_text(mode==BT_FORGET?bt_host.name:"Switch to a new device?",72,128,336,&lv_font_montserrat_24,0xF3EFE7);
        bt_text(mode==BT_FORGET?"Remove this saved pairing.\nAlso forget Desk-Mate on the host\nbefore pairing it again.":"The current host will disconnect.\nYou can then pair another device.",72,195,336,&lv_font_montserrat_16,0x858883);
        bt_button("Cancel",302,BT_BACK,0xF3EFE7);
        bt_button(mode==BT_FORGET?"Forget device":"Start pairing",358,mode==BT_FORGET?BT_CONFIRM_FORGET:BT_CONFIRM_ADD,0xE8B66D);
    } else if(mode==BT_PAIRED) {
        bt_detail_title=bt_text(bt_host.name,65,125,350,&lv_font_montserrat_24,0xF3EFE7);
        bt_text("Saved to your devices.\nReady for media control.",70,185,340,&lv_font_montserrat_16,0x8CBCE5);
        bt_button("Done / device list",290,BT_DONE,0x8CBCE5);
        bt_button("Change device name",348,BT_NAMES,0xF3EFE7);
    } else if(mode==BT_NAME_CHOICES) {
        bt_text("Choose an easy name for switching",65,86,350,&lv_font_montserrat_14,0x858883);
        bt_button("Mac",120,BT_NAME_MAC,0x8CBCE5);
        bt_button("Windows PC",170,BT_NAME_WINDOWS,0x8CBCE5);
        bt_button("iPhone",220,BT_NAME_IPHONE,0x8CBCE5);
        bt_button("Android phone",270,BT_NAME_ANDROID,0x8CBCE5);
        bt_button("Harmony phone",320,BT_NAME_HARMONY,0x8CBCE5);
        bt_button("Custom name...",370,BT_RENAME_ACTION,0xF3EFE7);
    } else if(mode==BT_RENAME) {
        bt_name_input=lv_textarea_create(bt_panel); lv_obj_set_pos(bt_name_input,82,94); lv_obj_set_size(bt_name_input,316,48);
        lv_obj_set_style_text_font(bt_name_input,&lv_font_montserrat_16,0);
        lv_obj_set_style_text_color(bt_name_input,lv_color_hex(0xF3EFE7),0);
        lv_obj_set_style_bg_color(bt_name_input,lv_color_hex(0x151B20),0); lv_obj_set_style_bg_opa(bt_name_input,LV_OPA_COVER,0);
        lv_obj_set_style_border_width(bt_name_input,1,0); lv_obj_set_style_border_color(bt_name_input,lv_color_hex(0x8CBCE5),0);
        lv_obj_set_style_pad_all(bt_name_input,10,0); lv_obj_set_style_border_width(bt_name_input,2,LV_PART_CURSOR);
        lv_textarea_set_one_line(bt_name_input,true); lv_textarea_set_max_length(bt_name_input,BLE_NAME_BYTES-1);
        lv_textarea_set_accepted_chars(bt_name_input,"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -_.'");
        lv_textarea_set_text(bt_name_input,bt_host.name);
        bt_keyboard=lv_keyboard_create(bt_panel); lv_obj_set_align(bt_keyboard,LV_ALIGN_TOP_LEFT); lv_obj_set_pos(bt_keyboard,48,172); lv_obj_set_size(bt_keyboard,384,196);
        lv_obj_set_style_text_font(bt_keyboard,&lv_font_montserrat_14,LV_PART_ITEMS);
        lv_obj_set_style_bg_opa(bt_keyboard,LV_OPA_TRANSP,0); lv_obj_set_style_pad_all(bt_keyboard,2,0);
        lv_obj_set_style_pad_row(bt_keyboard,6,0); lv_obj_set_style_pad_column(bt_keyboard,4,0);
        lv_obj_set_style_bg_color(bt_keyboard,lv_color_hex(0x151B20),LV_PART_ITEMS);
        lv_obj_set_style_bg_opa(bt_keyboard,LV_OPA_COVER,LV_PART_ITEMS);
        lv_obj_set_style_text_color(bt_keyboard,lv_color_hex(0xF3EFE7),LV_PART_ITEMS);
        lv_obj_set_style_radius(bt_keyboard,6,LV_PART_ITEMS);
        lv_obj_set_style_bg_color(bt_keyboard,lv_color_hex(0x36536B),LV_PART_ITEMS|LV_STATE_PRESSED);
        lv_keyboard_set_textarea(bt_keyboard,bt_name_input);
        lv_obj_add_event_cb(bt_keyboard,bt_keyboard_event,LV_EVENT_ALL,NULL);
        bt_button("Save name",385,BT_SAVE_NAME,0x8CBCE5);
        bt_footer=bt_text("",82,147,316,&lv_font_montserrat_14,0xE8B66D);
    }
    // Always reachable touch back control; physical long BOOT also returns.
    lv_obj_t *back=lv_obj_create(bt_panel); lv_obj_remove_style_all(back);
    lv_obj_set_pos(back,184,434); lv_obj_set_size(back,112,44); lv_obj_add_flag(back,LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(back,bt_click,LV_EVENT_CLICKED,(void *)(intptr_t)BT_BACK);
    lv_obj_t *l=lv_label_create(back); ui_label_set_text(l,"Back"); lv_obj_set_style_text_color(l,lv_color_hex(0x858883),0);
    lv_obj_set_style_text_font(l,&lv_font_montserrat_16,0); lv_obj_center(l); lv_obj_clear_flag(l,LV_OBJ_FLAG_CLICKABLE);
    bt_highlight();
}
static void bt_open(void) {
    if(bt_panel) return;
    bt_return_scroll_x = lv_obj_get_scroll_x(bt_pager);
    bt_return_scroll_y = lv_obj_get_scroll_y(bt_pager);
    // Cancel any in-flight pager animation while the manager owns the input.
    lv_obj_scroll_to(bt_pager, bt_return_scroll_x, bt_return_scroll_y, LV_ANIM_OFF);
    bt_panel=lv_obj_create(bt_screen); lv_obj_remove_style_all(bt_panel);
    lv_obj_set_size(bt_panel,480,480); lv_obj_set_pos(bt_panel,0,0);
    lv_obj_set_style_bg_color(bt_panel,lv_color_hex(0x05080A),0); lv_obj_set_style_bg_opa(bt_panel,LV_OPA_COVER,0);
    lv_obj_clear_flag(bt_panel,LV_OBJ_FLAG_SCROLLABLE); lv_obj_clear_flag(bt_pager,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(bt_panel,LV_OBJ_FLAG_CLICKABLE);
    if(bt_visibility) bt_visibility(true);
    bt_list_page=0; bt_draw(BT_LIST);
}
static void bt_open_event(lv_event_t *e) { if(lv_event_get_code(e)==LV_EVENT_CLICKED) bt_open(); }
static void bt_tick(void) {
    if(!bt_panel) return;
    bt_refresh_snapshot();
    if(bt_wait_pair&&bt_view.window_id!=bt_pair_window) bt_wait_pair=false;
    if(bt_screen_mode==BT_PAIR&&!bt_wait_pair&&bt_view.state==BLE_READY&&bt_view.generation!=bt_pair_generation&&!bt_view.name_pending) {
        if(bt_view.current>=0) {
            bt_host=bt_view.hosts[bt_view.current]; bt_name_after_pair=true;
            bt_draw(bt_host.name_kind==BLE_NAME_DEFAULT?BT_NAME_CHOICES:BT_PAIRED);
        }
    } else if(bt_screen_mode==BT_LIST) {
        bool changed=bt_known_count!=bt_view.count;
        for(int i=0;!changed&&i<bt_view.count;i++)
            changed=bt_list_hosts[i].type!=bt_view.hosts[i].type||strcmp(bt_list_hosts[i].address,bt_view.hosts[i].address);
        if(changed) bt_draw(BT_LIST);
    }
    char line[96];
    if(bt_status) {
        if(bt_screen_mode==BT_PAIR) {
            if(bt_wait_pair) snprintf(line,sizeof(line),"Starting new pairing...");
            else if(bt_view.state==BLE_CONNECTING) snprintf(line,sizeof(line),"Securing connection...");
            else if(bt_view.state==BLE_READY&&bt_view.name_pending) snprintf(line,sizeof(line),"Reading device name...");
            else if(bt_view.state==BLE_PAIRING&&bt_view.advertising) snprintf(line,sizeof(line),"%lus remaining",(unsigned long)((bt_view.remaining_ms+999)/1000));
            else snprintf(line,sizeof(line),"%s",bt_view.state==BLE_PAIRING?"Starting discovery...":"Pairing closed");
        } else if(bt_screen_mode==BT_DETAIL&&bt_host_index()<0) snprintf(line,sizeof(line),"No longer paired");
        else if(bt_screen_mode==BT_DETAIL && bt_host_index()!=bt_view.current && bt_host_index()!=bt_view.selected) snprintf(line,sizeof(line),"Paired / not connected");
        else snprintf(line,sizeof(line),"%s",ble_state_label(bt_view.state));
        ui_label_set_text(bt_status,line);
    }
    if(bt_screen_mode==BT_LIST) {
        snprintf(line,sizeof(line),"Saved %u / %u  |  Page %d / %d",bt_view.count,bt_view.capacity,bt_list_page+1,bt_page_count());
        ui_label_set_text(bt_count,line);
        snprintf(line,sizeof(line),"Bluetooth  %s",bt_view.enabled?"ON":"PAUSED"); ui_label_set_text(bt_row_labels[0],line);
        snprintf(line,sizeof(line),"Auto reconnect  %s",bt_view.auto_reconnect?"ON":"OFF"); ui_label_set_text(bt_row_labels[1],line);
        for(int n=0;n<2;n++) {
            int i=bt_list_page*2+n;
            if(i>=bt_view.count) break;
            snprintf(line,sizeof(line),"%s  /  %s",bt_view.hosts[i].name,
                i==bt_view.current&&bt_view.state==BLE_READY?"Connected":i==bt_view.selected&&(bt_view.state==BLE_WAITING||bt_view.state==BLE_BACKGROUND||bt_view.state==BLE_CONNECTING)?"Selected":"Paired");
            ui_label_set_text(bt_row_labels[2+n],line);
        }
    }
    if(bt_detail_title) {
        int i=bt_host_index();
        if(i>=0) ui_label_set_text(bt_detail_title,bt_view.hosts[i].name);
    }
    if(bt_footer) ui_label_set_text(bt_footer,bt_ui_error[0]?bt_ui_error:bt_view.message);
}
/* Physical navigation acts on the same buttons as touch. Long BOOT is Back. */
static void bt_key(int key,bool held) {
    if(!bt_panel) return;
    if(held) { if(key==9) bt_back(); return; }
    if(bt_keyboard&&key!=0) {
        uint32_t selected=BT_MATRIX_SELECTED(bt_keyboard);
        uint32_t count=0; while(BT_MATRIX_TEXT(bt_keyboard,count)) count++;
        if(count) { if(selected>=count) selected=0; else selected=(selected+count+(key==9?-1:1))%count; BT_MATRIX_SET(bt_keyboard,selected); }
        return;
    }
    if(bt_keyboard&&key==0) {
        BT_SEND_EVENT(bt_keyboard,LV_EVENT_VALUE_CHANGED,NULL); return;
    }
    if(!bt_row_count) return;
    if(key==9||key==10) {
        for(int n=0;n<bt_row_count;n++) {
            bt_selected=(bt_selected+bt_row_count+(key==9?-1:1))%bt_row_count;
            if(!lv_obj_has_state(bt_rows[bt_selected],LV_STATE_DISABLED)) break;
        }
    }
    else {
        // Event user data carries the action, avoiding a second routing table.
        BT_SEND_EVENT(bt_rows[bt_selected],LV_EVENT_CLICKED,NULL); return;
    }
    bt_highlight();
}
static void bt_bind(lv_obj_t *screen,lv_obj_t *pager,BleReadSnapshot read,BleSubmitRequest submit) {
    bt_screen=screen; bt_pager=pager; bt_read=read; bt_submit=submit;
}
#endif
