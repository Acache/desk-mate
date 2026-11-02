#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../desk-mate-app/src/media_ble/ble_model.h"
#include "../desk-mate-app/src/media_ble/ble_metadata.h"
static BleModel m;
static void host(int i, const char *id) {
    snprintf(m.view.hosts[i].address, sizeof(m.view.hosts[i].address), "%s", id);
    snprintf(m.view.hosts[i].name, sizeof(m.view.hosts[i].name), "Host %d", i+1);
}
int main(void) {
    BleStoredV1 old={0}; BleStored migrated;
    old.version=1; old.enabled=old.automatic=1; old.last_type=2;
    snprintf(old.last,sizeof(old.last),"aa:00:00:00:00:01");
    snprintf(old.hosts[0].address,18,"aa:00:00:00:00:01");
    snprintf(old.hosts[0].name,BLE_NAME_BYTES,"Host 1"); old.hosts[0].type=2;
    snprintf(old.hosts[1].name,BLE_NAME_BYTES,"Office Mac");
    assert(ble_metadata_decode(&old,sizeof(old),&migrated));
    assert(migrated.version==3&&migrated.enabled&&migrated.automatic&&migrated.last_type==0);
    assert(migrated.hosts[0].type==0&&migrated.hosts[0].name_kind==BLE_NAME_DEFAULT);
    assert(migrated.hosts[1].name_kind==BLE_NAME_USER&&!strcmp(migrated.hosts[1].name,"Office Mac"));
    BleStored roundtrip; migrated.hosts[1].name_kind=BLE_NAME_REMOTE;
    assert(ble_metadata_decode(&migrated,sizeof(migrated),&roundtrip));
    assert(roundtrip.hosts[1].name_kind==BLE_NAME_REMOTE&&!strcmp(roundtrip.hosts[1].name,"Office Mac"));
    assert(!ble_metadata_decode(&old,sizeof(old)-1,&roundtrip));
    /* This is the actual old three-slot v2 wire layout, independent of the new limit. */
    struct {
        uint32_t version; uint8_t enabled, automatic;
        char last[18]; uint8_t last_type; BleHost hosts[3];
    } v2={0};
    v2.version=2; v2.enabled=1; v2.automatic=0; v2.last_type=1;
    snprintf(v2.last,18,"aa:00:00:00:00:03");
    for(int i=0;i<3;i++) {
        snprintf(v2.hosts[i].address,18,"aa:00:00:00:00:0%d",i+1);
        snprintf(v2.hosts[i].name,BLE_NAME_BYTES,"Saved device %d",i+1);
        v2.hosts[i].name_kind=BLE_NAME_USER;
    }
    assert(ble_metadata_decode(&v2,sizeof(v2),&migrated));
    assert(migrated.version==3&&migrated.enabled&&!migrated.automatic);
    assert(migrated.last_type==1&&!strcmp(migrated.last,v2.last));
    assert(!memcmp(migrated.hosts,v2.hosts,sizeof(v2.hosts)));
    for(int i=3;i<BLE_HOST_LIMIT;i++) assert(!migrated.hosts[i].address[0]);
    assert(!ble_metadata_decode(&v2,sizeof(v2)-1,&roundtrip));
    ble_model_init(&m, 3);
    host(0,"aa:00:00:00:00:01"); host(1,"aa:00:00:00:00:02"); m.view.count=2;
    ble_model_connect(&m,0,100);
    assert(ble_model_allows(&m,0) && !ble_model_allows(&m,1) && !ble_model_allows(&m,-1));
    assert(ble_model_link(&m,4,1,0,110));
    assert(!ble_model_link(&m,5,2,0,111));
    ble_model_security(&m,4,1,true,120); assert(!ble_model_ready(&m));
    ble_model_subscribe(&m,4,1,true,130); assert(ble_model_ready(&m));
    ble_model_connect(&m,1,140); assert(!ble_model_ready(&m));
    ble_model_security(&m,4,1,true,141); ble_model_subscribe(&m,4,1,true,142);
    assert(!ble_model_ready(&m)); /* retired auth cannot revive the old host */
    ble_model_disconnected(&m,4,1,150);
    assert(ble_model_allows(&m,1) && !ble_model_allows(&m,0));
    assert(ble_model_link(&m,4,2,1,160));
    ble_model_security(&m,4,2,true,170); ble_model_subscribe(&m,4,2,true,180);
    assert(ble_model_ready(&m));
    ble_model_disconnected(&m,4,1,190); assert(ble_model_ready(&m));
    ble_model_disconnect(&m); ble_model_disconnected(&m,4,2,200);
    assert(m.view.state==BLE_IDLE && !ble_model_allows(&m,1));
    assert(ble_model_pair(&m,300)); assert(!ble_model_allows(&m,0) && ble_model_allows(&m,-1));
    assert(!ble_model_link(&m,7,3,0,310));
    assert(m.pairing && m.view.state==BLE_PAIRING); /* old host cannot consume the pairing window */
    uint32_t first_window=m.view.window_id;
    ble_model_tick(&m,50300); assert(m.view.remaining_ms==70000);
    assert(ble_model_pair(&m,50300)); /* explicit Add always begins a fresh window */
    assert(m.view.window_id!=first_window && m.view.remaining_ms==120000);
    assert(m.previous==1); /* renewal keeps the original return target */
    ble_model_tick(&m,170301); assert(m.view.state!=BLE_PAIRING && !ble_model_allows(&m,-1));
    m.view.count=3; assert(!ble_model_pair(&m,130000));
    m.view.count=2; ble_model_connect(&m,1,140000); ble_model_tick(&m,170001);
    assert(m.view.state==BLE_BACKGROUND);
    m.view.auto_reconnect=false; ble_model_connect(&m,1,180000); ble_model_tick(&m,210001);
    assert(m.view.state==BLE_IDLE && !ble_model_allows(&m,1));
    ble_model_pause(&m,true,220000); assert(m.view.state==BLE_PAUSED && !ble_model_allows(&m,-1));
    assert(!ble_model_pair(&m,220100));
    ble_model_pause(&m,false,220200); assert(m.view.state==BLE_IDLE);
    ble_model_connect(&m,0,0xfffffff0u); ble_model_tick(&m,30001u);
    assert(m.view.state==BLE_IDLE); /* deadline wraparound */
    assert(BLE_HOST_LIMIT==8);
    ble_model_init(&m,255); assert(m.view.capacity==8);
    m.view.count=7; assert(ble_model_pair(&m,1));
    m.view.count=8; assert(!ble_model_pair(&m,2));
    for(int i=0;i<8;i++) {
        char id[18]; snprintf(id,sizeof(id),"aa:00:00:00:00:0%d",i+1); host(i,id);
    }
    ble_model_connect(&m,7,3);
    for(int i=0;i<7;i++) assert(!ble_model_allows(&m,i));
    assert(!ble_model_link(&m,9,10,0,4));
    assert(ble_model_link(&m,9,10,7,4));
    assert(!ble_model_link(&m,10,11,7,5));
    ble_model_security(&m,9,10,true,6); ble_model_subscribe(&m,9,10,true,6);
    assert(ble_model_ready(&m)&&m.view.current==7);
    ble_model_connect(&m,0,7); assert(!ble_model_ready(&m));
    assert(!ble_model_link(&m,10,11,0,8)); /* wait until old handle disconnects */
    ble_model_disconnected(&m,9,10,9);
    assert(!ble_model_link(&m,10,11,7,10));
    assert(ble_model_link(&m,10,11,0,10));
    migrated.version=3; memcpy(migrated.hosts,m.view.hosts,sizeof(migrated.hosts));
    assert(ble_metadata_decode(&migrated,sizeof(migrated),&roundtrip));
    assert(!strcmp(roundtrip.hosts[7].address,"aa:00:00:00:00:08"));
    puts("BLE policy: target isolation, stale sessions, windows, capacity, pause, rollover PASS");
}
