#ifndef DESKMATE_VOICE_HOSTS_UI_H
#define DESKMATE_VOICE_HOSTS_UI_H
#include "lvgl.h"
#include "src/bili_voice/voice_host_model.h"
#include <stdio.h>
#include <string.h>
static VoiceHostRead vh_read;
static VoiceHostSubmit vh_submit;
static VoiceHostGate vh_gate;
static void (*vh_visibility)(bool);
static lv_obj_t *vh_parent,*vh_pager,*vh_panel,*vh_rows[8],*vh_input,*vh_keyboard,*vh_status;
static VoiceHostView vh_view;
static VoiceHost vh_selected_host;
static int vh_mode,vh_row_count,vh_selected_row,vh_page,vh_actions[8];
static uint32_t vh_generation,vh_added_revision;
static char vh_error[64],vh_added_before[37],vh_recent_id[37],vh_recent_result[64];
static VoiceHost vh_recent_host;
enum { VH_UI_LIST=1,VH_UI_DETAIL,VH_UI_SWITCH,VH_UI_SETTINGS,VH_UI_REMOVE,VH_UI_RENAME,VH_UI_ADD,VH_UI_NETWORK,VH_UI_DIAGNOSE,VH_UI_SETUP,VH_UI_ADDED,VH_UI_HISTORY };
enum { VH_UI_BACK=100,VH_UI_NEXT_PAGE,VH_UI_DO_CONNECT,VH_UI_SETTINGS_ACTION,VH_UI_REMOVE_ACTION,VH_UI_CONFIRM_REMOVE,VH_UI_RENAME_ACTION,VH_UI_SAVE_NAME,VH_UI_DEFAULT_ACTION,VH_UI_DISCONNECT_ACTION,VH_UI_ADD_ACTION,VH_UI_NETWORK_ACTION,VH_UI_DIAGNOSE_ACTION,VH_UI_RETRY_NETWORK,VH_UI_SETUP_ACTION,VH_UI_CANCEL_SETUP,VH_UI_CONNECT_ADDED,VH_UI_USE_BLE,VH_UI_USE_WIFI };
static void vh_draw(int mode);
static bool vh_is_open(void){return vh_panel!=NULL;}
static void vh_snapshot(void){if(vh_read)vh_read(&vh_view);}
static int vh_selected_slot(void){return vh_find(&vh_view.catalog,vh_selected_host.id);}
static bool vh_send(VoiceHostOperation op,const char *name){
 VoiceHostRequest r;memset(&r,0,sizeof(r));r.op=op;
 snprintf(r.id,sizeof(r.id),"%s",vh_selected_host.id);if(name)snprintf(r.name,sizeof(r.name),"%s",name);
 bool ok=vh_submit&&vh_submit(&r);snprintf(vh_error,sizeof(vh_error),"%s",ok?"":"Busy; wait and retry");return ok;
}
static void vh_close(void){
 if(!vh_panel)return;
 vh_send(VH_CANCEL_SETUP,NULL);
 lv_obj_del(vh_panel);vh_panel=NULL;vh_input=vh_keyboard=NULL;vh_generation++;
 if(vh_visibility)vh_visibility(false);
 if(vh_gate)vh_gate(false);
 lv_obj_add_flag(vh_pager,LV_OBJ_FLAG_SCROLLABLE);
}
static void vh_back(void){
 if(vh_mode==VH_UI_LIST)vh_close();
 else if(vh_mode==VH_UI_SETUP){vh_send(VH_CANCEL_SETUP,NULL);vh_draw(VH_UI_NETWORK);}
 else if(vh_mode==VH_UI_SETTINGS||vh_mode==VH_UI_SWITCH||vh_mode==VH_UI_DIAGNOSE)vh_draw(VH_UI_DETAIL);
 else if(vh_mode==VH_UI_RENAME||vh_mode==VH_UI_REMOVE)vh_draw(VH_UI_SETTINGS);
 else vh_draw(VH_UI_LIST);
}
static void vh_activate(int action){
 vh_snapshot();vh_error[0]=0;
 if(action>=0&&action<VH_CAPACITY){if(vh_view.catalog.hosts[action].present){vh_selected_host=vh_view.catalog.hosts[action];vh_draw(VH_UI_DETAIL);}return;}
 switch(action){
 case VH_UI_BACK:vh_back();break;
 case VH_UI_NEXT_PAGE:vh_page=(vh_page+1)%((vh_count(&vh_view.catalog)+1)/2);vh_draw(VH_UI_LIST);break;
 case VH_UI_DO_CONNECT:
  if(vh_mode!=VH_UI_SWITCH&&vh_mode!=VH_UI_ADDED&&vh_selected_slot()!=vh_view.catalog.active)vh_draw(VH_UI_SWITCH);
  else if(vh_send(VH_CONNECT,NULL))vh_draw(VH_UI_DETAIL);
  break;
 case VH_UI_SETTINGS_ACTION:vh_draw(VH_UI_SETTINGS);break;
 case VH_UI_REMOVE_ACTION:vh_draw(VH_UI_REMOVE);break;
 case VH_UI_CONFIRM_REMOVE:if(vh_send(VH_FORGET,NULL))vh_draw(VH_UI_LIST);break;
 case VH_UI_RENAME_ACTION:vh_draw(VH_UI_RENAME);break;
 case VH_UI_SAVE_NAME:if(vh_input&&lv_textarea_get_text(vh_input)[0]&&vh_send(VH_RENAME,lv_textarea_get_text(vh_input)))vh_draw(VH_UI_SETTINGS);break;
 case VH_UI_DEFAULT_ACTION:vh_send(VH_DEFAULT,NULL);break;
 case VH_UI_DISCONNECT_ACTION:vh_send(VH_DISCONNECT,NULL);break;
 case VH_UI_ADD_ACTION:if(vh_count(&vh_view.catalog)>=VH_CAPACITY){snprintf(vh_error,sizeof(vh_error),"Capacity full; remove a computer");break;}snprintf(vh_added_before,37,"%s",vh_view.added_id);vh_added_revision=vh_view.provision_revision;vh_draw(VH_UI_ADD);break;
 case VH_UI_NETWORK_ACTION:vh_draw(VH_UI_NETWORK);break;
 case VH_UI_DIAGNOSE_ACTION:vh_draw(VH_UI_DIAGNOSE);break;
 case VH_UI_USE_BLE:vh_send(VH_TRANSPORT_BLE,NULL);vh_draw(VH_UI_NETWORK);break;
 case VH_UI_USE_WIFI:vh_send(VH_TRANSPORT_WIFI,NULL);vh_draw(VH_UI_NETWORK);break;
 case VH_UI_RETRY_NETWORK:vh_send(VH_RETRY_WIFI,NULL);break;
 case VH_UI_SETUP_ACTION:if(vh_send(VH_SETUP_WIFI,NULL))vh_draw(VH_UI_SETUP);break;
 case VH_UI_CANCEL_SETUP:vh_send(VH_CANCEL_SETUP,NULL);vh_draw(VH_UI_NETWORK);break;
 }
}
static void vh_click(lv_event_t *e){if(lv_event_get_code(e)==LV_EVENT_CLICKED)vh_activate((int)(intptr_t)lv_event_get_user_data(e));}
static lv_obj_t *vh_text(const char *text,int y,const lv_font_t *font,uint32_t color){
 lv_obj_t *l=lv_label_create(vh_panel);lv_label_set_text(l,text);lv_obj_set_pos(l,64,y);lv_obj_set_width(l,352);
 lv_obj_set_style_text_font(l,font,0);lv_obj_set_style_text_color(l,lv_color_hex(color),0);
 lv_obj_set_style_text_align(l,LV_TEXT_ALIGN_CENTER,0);lv_label_set_long_mode(l,LV_LABEL_LONG_WRAP);return l;
}
static void vh_button(const char *text,int y,int action,bool primary){
 int n=vh_row_count++;if(n>=8)return;
 lv_obj_t *b=lv_obj_create(vh_panel);lv_obj_remove_style_all(b);lv_obj_set_pos(b,68,y);lv_obj_set_size(b,344,46);
 lv_obj_clear_flag(b,LV_OBJ_FLAG_SCROLLABLE|LV_OBJ_FLAG_SCROLL_ON_FOCUS);lv_obj_add_flag(b,LV_OBJ_FLAG_CLICKABLE);
 lv_obj_set_style_radius(b,16,0);lv_obj_set_style_bg_color(b,lv_color_hex(primary?0x8CBCE5:0x151B20),0);lv_obj_set_style_bg_opa(b,LV_OPA_COVER,0);
 lv_obj_add_event_cb(b,vh_click,LV_EVENT_CLICKED,(void *)(intptr_t)action);
 lv_obj_t *l=lv_label_create(b);lv_label_set_text(l,text);lv_obj_set_width(l,316);lv_label_set_long_mode(l,LV_LABEL_LONG_DOT);
 lv_obj_set_style_text_font(l,&lv_font_montserrat_20,0);lv_obj_set_style_text_color(l,lv_color_hex(primary?0x050606:0xF3EFE7),0);
 lv_obj_set_style_text_align(l,LV_TEXT_ALIGN_CENTER,0);lv_obj_center(l);lv_obj_clear_flag(l,LV_OBJ_FLAG_CLICKABLE);vh_rows[n]=b;vh_actions[n]=action;
}
static void vh_highlight(void){for(int n=0;n<vh_row_count;n++){lv_obj_set_style_border_width(vh_rows[n],n==vh_selected_row?2:0,0);lv_obj_set_style_border_color(vh_rows[n],lv_color_hex(0xE8B66D),0);}}
static void vh_keyboard_event(lv_event_t *e){if(lv_event_get_code(e)==LV_EVENT_READY)vh_activate(VH_UI_SAVE_NAME);else if(lv_event_get_code(e)==LV_EVENT_CANCEL)vh_back();}
static void vh_draw(int mode){
 vh_snapshot();vh_mode=mode;vh_generation++;vh_row_count=0;vh_selected_row=0;vh_input=vh_keyboard=NULL;
 lv_obj_clean(vh_panel);vh_button("< BACK",54,VH_UI_BACK,false);
 int slot=vh_selected_slot();char text[128];
 if(mode==VH_UI_LIST){
  int count=vh_count(&vh_view.catalog);snprintf(text,sizeof(text),"VOICE COMPUTERS  %d / 5",count);vh_text(text,110,&lv_font_montserrat_20,0x8CBCE5);
  int per=count>3?2:3;if(vh_page*per>=count)vh_page=0;int shown=0,ordinal=0;for(int i=0;i<VH_CAPACITY;i++)if(vh_view.catalog.hosts[i].present){
   if(ordinal++/per!=vh_page)continue;
   snprintf(text,sizeof(text),"%s%s%s",i==vh_view.catalog.active?"> ":"",vh_view.catalog.hosts[i].name,i==vh_view.catalog.preferred?" *":"");vh_button(text,150+shown++*53,i,false);
  }
  if(!count)vh_text("No computers saved",185,&lv_font_montserrat_24,0xA0A6A5);
  if(count>3)vh_button("MORE COMPUTERS >",260,VH_UI_NEXT_PAGE,false);
  vh_button("+ ADD COMPUTER",count>3?313:310,VH_UI_ADD_ACTION,true);
  vh_button("WI-FI / NETWORK",count>3?366:363,VH_UI_NETWORK_ACTION,false);
 }else if(mode==VH_UI_DETAIL||mode==VH_UI_ADDED){
  vh_text(slot>=0?vh_view.catalog.hosts[slot].name:"Computer removed",126,&lv_font_montserrat_28,0xF3EFE7);
  const char *state=slot==vh_view.catalog.active?(vh_view.ready?"ACTIVE / VOICE READY":vh_view.paused?"PAUSED":"ACTIVE / NOT READY"):"SAVED / NOT CONNECTED";
  vh_text(state,173,&lv_font_montserrat_16,0x8CBCE5);vh_text("TRUSTED  /  BILIBILI",204,&lv_font_montserrat_16,0xA0A6A5);
  vh_button("CONNECT / SWITCH",258,VH_UI_DO_CONNECT,true);vh_button("MANAGE COMPUTER",311,VH_UI_SETTINGS_ACTION,false);vh_button("CONNECTION CHECK",364,VH_UI_DIAGNOSE_ACTION,false);
 }else if(mode==VH_UI_SWITCH){
  vh_text("SWITCH TO",126,&lv_font_montserrat_20,0xA0A6A5);vh_text(vh_selected_host.name,163,&lv_font_montserrat_28,0xF3EFE7);
  vh_text("Switch only when voice is ready.\nPrevious target kept on failure.",220,&lv_font_montserrat_16,0xA0A6A5);
  vh_button("KEEP CURRENT",300,VH_UI_BACK,true);vh_button("CONNECT",355,VH_UI_DO_CONNECT,false);vh_selected_row=1;
 }else if(mode==VH_UI_SETTINGS){
  vh_text(vh_selected_host.name,117,&lv_font_montserrat_24,0xF3EFE7);vh_button("RENAME",170,VH_UI_RENAME_ACTION,false);
  vh_button(slot==vh_view.catalog.preferred?"STARTUP DEFAULT":"USE AT STARTUP",223,VH_UI_DEFAULT_ACTION,false);
  vh_button("DISCONNECT",276,VH_UI_DISCONNECT_ACTION,false);vh_button("REMOVE COMPUTER",329,VH_UI_REMOVE_ACTION,false);
 }else if(mode==VH_UI_REMOVE){
  vh_text("REMOVE COMPUTER?",130,&lv_font_montserrat_24,0xF3EFE7);vh_text(vh_selected_host.name,177,&lv_font_montserrat_20,0x8CBCE5);
  vh_text("Re-authorization will be required.\nMac applications are kept.",221,&lv_font_montserrat_16,0xA0A6A5);
  vh_button("KEEP COMPUTER",300,VH_UI_BACK,true);vh_button("REMOVE",355,VH_UI_CONFIRM_REMOVE,false);vh_selected_row=1;
 }else if(mode==VH_UI_RENAME){
  vh_text("DISPLAY NAME",111,&lv_font_montserrat_20,0x8CBCE5);
  vh_input=lv_textarea_create(vh_panel);lv_obj_set_pos(vh_input,72,150);lv_obj_set_size(vh_input,336,48);lv_textarea_set_one_line(vh_input,true);lv_textarea_set_max_length(vh_input,32);
  lv_textarea_set_accepted_chars(vh_input," ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.");lv_textarea_set_text(vh_input,slot>=0?vh_view.catalog.hosts[slot].name:"");
  lv_obj_set_style_text_font(vh_input,&lv_font_montserrat_20,0);
  lv_obj_set_style_text_color(vh_input,lv_color_hex(0xF3EFE7),0);lv_obj_set_style_bg_color(vh_input,lv_color_hex(0x151B20),0);lv_obj_set_style_bg_opa(vh_input,LV_OPA_COVER,0);
  lv_obj_set_style_border_width(vh_input,1,0);lv_obj_set_style_border_color(vh_input,lv_color_hex(0x8CBCE5),0);lv_obj_set_style_pad_all(vh_input,10,0);lv_obj_set_style_border_width(vh_input,2,LV_PART_CURSOR);
  vh_keyboard=lv_keyboard_create(vh_panel);lv_obj_set_align(vh_keyboard,LV_ALIGN_TOP_LEFT);lv_obj_set_pos(vh_keyboard,50,215);lv_obj_set_size(vh_keyboard,380,174);lv_keyboard_set_textarea(vh_keyboard,vh_input);lv_obj_set_style_text_font(vh_keyboard,&lv_font_montserrat_16,LV_PART_ITEMS);
  lv_obj_set_style_bg_opa(vh_keyboard,LV_OPA_TRANSP,0);lv_obj_set_style_pad_all(vh_keyboard,2,0);lv_obj_set_style_pad_row(vh_keyboard,6,0);lv_obj_set_style_pad_column(vh_keyboard,4,0);
  lv_obj_set_style_bg_color(vh_keyboard,lv_color_hex(0x151B20),LV_PART_ITEMS);lv_obj_set_style_bg_opa(vh_keyboard,LV_OPA_COVER,LV_PART_ITEMS);lv_obj_set_style_text_color(vh_keyboard,lv_color_hex(0xF3EFE7),LV_PART_ITEMS);lv_obj_set_style_radius(vh_keyboard,6,LV_PART_ITEMS);lv_obj_set_style_bg_color(vh_keyboard,lv_color_hex(0x36536B),LV_PART_ITEMS|LV_STATE_PRESSED);
  lv_obj_add_event_cb(vh_keyboard,vh_keyboard_event,LV_EVENT_ALL,NULL);vh_button("SAVE NAME",400,VH_UI_SAVE_NAME,true);lv_obj_set_x(vh_rows[vh_row_count-1],100);lv_obj_set_width(vh_rows[vh_row_count-1],280);
 }else if(mode==VH_UI_ADD){
  vh_text("ADD A MAC",124,&lv_font_montserrat_28,0xF3EFE7);
  vh_text("1. Open the Mac assistant\n2. Connect Desk-Mate via USB\n3. Run the pair command on Mac",185,&lv_font_montserrat_16,0xA0A6A5);
  vh_text("Existing computers are kept.\nWaiting for USB authorization...",290,&lv_font_montserrat_16,0x8CBCE5);
 }else if(mode==VH_UI_NETWORK){
  bool ascii=true;for(const unsigned char *c=(const unsigned char *)vh_view.ssid;*c;c++)if(*c>=128)ascii=false;
  vh_text(vh_view.ble_mode?"BLUETOOTH VOICE":"WI-FI VOICE",122,&lv_font_montserrat_24,0xF3EFE7);
  vh_text(vh_view.ble_mode?"Uses the Mac selected on page 2.\nStart the Mac assistant to talk.":(vh_view.wifi?(ascii?vh_view.ssid:"WI-FI CONNECTED"):"WI-FI NOT CONNECTED"),175,&lv_font_montserrat_16,0xA0A6A5);
  vh_button("USE BLUETOOTH",244,VH_UI_USE_BLE,vh_view.ble_mode);vh_button("USE WI-FI",298,VH_UI_USE_WIFI,!vh_view.ble_mode);
  vh_button("CHANGE WI-FI",352,VH_UI_SETUP_ACTION,false);
 }else if(mode==VH_UI_SETUP){
  vh_text("USE YOUR PHONE",125,&lv_font_montserrat_24,0xF3EFE7);
  vh_text("Join: Desk-Mate-Voice\nPassword: deskmate\nOpen: http://192.168.4.1\nSelect a 2.4GHz Wi-Fi network",181,&lv_font_montserrat_16,0x8CBCE5);
  vh_text("Computer authorizations are kept.\nSetup closes after 2 minutes.",300,&lv_font_montserrat_16,0xA0A6A5);
  vh_button("CANCEL SETUP",369,VH_UI_CANCEL_SETUP,false);
 }else if(mode==VH_UI_HISTORY){
  vh_text("LAST COMMAND",125,&lv_font_montserrat_24,0x8CBCE5);vh_text(vh_recent_host.name,178,&lv_font_montserrat_24,0xF3EFE7);
  vh_text(vh_recent_result,234,&lv_font_montserrat_20,0xA0A6A5);vh_text("Replay is available only on\nthe original computer.",315,&lv_font_montserrat_16,0xA0A6A5);
 }else if(mode==VH_UI_DIAGNOSE){
  vh_text("CONNECTION CHECK",123,&lv_font_montserrat_24,0xF3EFE7);
  snprintf(text,sizeof(text),"Wi-Fi: %s\nTrust: saved\nVoice: %s",vh_view.wifi?"connected":"disconnected",slot==vh_view.catalog.active?vh_view.status:"not connected");
  vh_text(text,181,&lv_font_montserrat_16,0xA0A6A5);vh_button("RETRY CONNECTION",300,VH_UI_DO_CONNECT,true);vh_button("WI-FI SETTINGS",355,VH_UI_NETWORK_ACTION,false);
 }
 vh_status=vh_text("",427,&lv_font_montserrat_16,0xE8B66D);lv_obj_set_width(vh_status,240);lv_obj_set_x(vh_status,120);lv_label_set_long_mode(vh_status,LV_LABEL_LONG_DOT);vh_highlight();
}
static bool vh_open(void){
 if(vh_panel)return true;vh_snapshot();
 if(vh_view.command_busy||vh_view.catalog.switching||!vh_gate||!vh_gate(true))return false;
 if(vh_visibility)vh_visibility(true);
 vh_panel=lv_obj_create(vh_parent);lv_obj_remove_style_all(vh_panel);lv_obj_set_size(vh_panel,480,480);lv_obj_set_style_bg_color(vh_panel,lv_color_hex(0),0);lv_obj_set_style_bg_opa(vh_panel,LV_OPA_COVER,0);
 lv_obj_clear_flag(vh_panel,LV_OBJ_FLAG_SCROLLABLE);lv_obj_clear_flag(vh_pager,LV_OBJ_FLAG_SCROLLABLE);vh_page=0;vh_error[0]=0;vh_draw(VH_UI_LIST);return true;
}
static void vh_open_event(lv_event_t *e){if(lv_event_get_code(e)==LV_EVENT_CLICKED)vh_open();}
#if LVGL_VERSION_MAJOR >= 9
#define VH_KEY_SELECTED lv_buttonmatrix_get_selected_button
#define VH_KEY_TEXT lv_buttonmatrix_get_button_text
#define VH_KEY_SET lv_buttonmatrix_set_selected_button
#define VH_KEY_EVENT lv_obj_send_event
#else
#define VH_KEY_SELECTED lv_btnmatrix_get_selected_btn
#define VH_KEY_TEXT lv_btnmatrix_get_btn_text
#define VH_KEY_SET lv_btnmatrix_set_selected_btn
#define VH_KEY_EVENT lv_event_send
#endif
static void vh_key(int pin,bool held){
 if(!vh_panel)return;
 if(held&&pin==9){vh_back();return;}
 if(held)return;
 if(vh_keyboard){
  if(pin==0){VH_KEY_EVENT(vh_keyboard,LV_EVENT_VALUE_CHANGED,NULL);return;}
  uint32_t count=0;while(VH_KEY_TEXT(vh_keyboard,count))count++;
  uint32_t selected=VH_KEY_SELECTED(vh_keyboard);if(count){if(selected>=count)selected=0;else selected=(selected+count+(pin==9?-1:1))%count;VH_KEY_SET(vh_keyboard,selected);}return;
 }
 if(pin==0){if(vh_selected_row<vh_row_count){int action=vh_actions[vh_selected_row];vh_activate(action);}return;}
 vh_selected_row=(vh_selected_row+(pin==9?-1:1)+vh_row_count)%vh_row_count;vh_highlight();
}
static void vh_tick(void){
 if(!vh_panel)return;VoiceHostView old=vh_view;vh_snapshot();
 if(vh_mode==VH_UI_ADD&&vh_view.added_id[0]&&(strcmp(vh_view.added_id,vh_added_before)||vh_view.provision_revision!=vh_added_revision)){
  int i=vh_find(&vh_view.catalog,vh_view.added_id);if(i>=0){vh_selected_host=vh_view.catalog.hosts[i];vh_draw(VH_UI_ADDED);return;}
 }
 if(vh_mode==VH_UI_SETUP&&old.network_setup&&!vh_view.network_setup){vh_draw(VH_UI_NETWORK);return;}
 if((vh_view.ble_mode!=old.ble_mode||vh_view.wifi!=old.wifi||vh_view.catalog.revision!=old.catalog.revision||vh_view.ready!=old.ready||vh_view.connected!=old.connected||vh_view.paused!=old.paused||vh_view.catalog.switching!=old.catalog.switching)&&vh_mode!=VH_UI_RENAME){
  if(vh_selected_slot()<0&&vh_mode!=VH_UI_LIST&&vh_mode!=VH_UI_ADD&&vh_mode!=VH_UI_NETWORK&&vh_mode!=VH_UI_SETUP){vh_draw(VH_UI_LIST);return;}
  int selected=vh_selected_row;vh_draw(vh_mode);if(selected<vh_row_count){vh_selected_row=selected;vh_highlight();}
 }
 const char *message=vh_error[0]?vh_error:vh_view.catalog.switching?"CONNECTING...":vh_view.message;
 if(vh_status)lv_label_set_text(vh_status,message);
}
#endif
