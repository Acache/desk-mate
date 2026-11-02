#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../desk-mate-app/src/media_ble/ble_voice_protocol.h"

int main(void) {
    assert(ble_lab_owns(4,2,true,4,2,2));
    assert(!ble_lab_owns(4,2,true,4,1,2)); // reused handle, retired generation
    assert(!ble_lab_owns(4,2,true,4,2,1)); // stale notification subscription
    assert(!ble_lab_owns(4,2,false,4,2,2));
    assert(!ble_lab_owns(UINT16_MAX,2,true,UINT16_MAX,2,2));
    assert(!ble_lab_owns(4,2,true,5,2,2));
    BleLabModel m; ble_lab_reset(&m);
    assert(!ble_lab_start(&m,0,320000,32000,12,185,0));
    assert(!ble_lab_start(&m,1,320000,32000,33,185,0));
    assert(!ble_lab_start(&m,1,320000,32000,12,23,0));
    assert(m.state==BLE_LAB_LOW_MTU);
    assert(!ble_lab_start(&m,1,3200001,32000,12,185,0));
    assert(ble_lab_start(&m,1,320000,32000,12,185,100));
    assert(m.payload==166&&ble_lab_next(&m,100)==0);
    assert(ble_lab_next(&m,120)==166);
    for(int i=0;i<12;i++) { assert(ble_lab_next(&m,1000)==166); ble_lab_sent(&m,1000); }
    assert(!ble_lab_next(&m,1000)); // receiver credits exhausted
    assert(!ble_lab_ack(&m,2,12,1992,1001));
    assert(!ble_lab_ack(&m,1,13,2158,1001));
    assert(!ble_lab_ack(&m,1,12,1991,1001));
    assert(ble_lab_ack(&m,1,8,1328,1001));
    assert(!ble_lab_ack(&m,1,7,1162,1002));
    assert(!ble_lab_start(&m,2,100,32000,12,185,1002));
    assert(m.run==1&&m.sent_bytes==1992&&m.state==BLE_LAB_RUNNING);
    ble_lab_tick(&m,3002); assert(m.state==BLE_LAB_STALLED);
    assert(!ble_lab_start(&m,1,100,32000,12,185,3003)); // stale run
    assert(ble_lab_start(&m,2,335,32000,12,185,3003));
    assert(ble_lab_next(&m,4000)==166); ble_lab_sent(&m,4000);
    assert(ble_lab_next(&m,4000)==166); ble_lab_sent(&m,4000);
    assert(ble_lab_next(&m,4000)==3); ble_lab_sent(&m,4000);
    assert(!ble_lab_next(&m,4000)&&m.state==BLE_LAB_RUNNING);
    assert(ble_lab_ack(&m,2,3,335,4001)&&m.state==BLE_LAB_COMPLETE);
    uint8_t data[244]; ble_lab_header(data,2,3,7,332);
    assert(data[0]==1&&data[1]==2&&ble_lab_u16(data+2)==3);
    assert(ble_lab_u32(data+4)==2&&ble_lab_u32(data+8)==7&&ble_lab_u32(data+12)==332);
    ble_lab_fill(data+16,3,332);
    for(int i=0;i<3;i++) assert(data[16+i]==(uint8_t)((332+i)*31+17));
    ble_lab_reset(&m);
    assert(ble_lab_start(&m,1,32000,32000,12,185,0xfffffff0u));
    assert(ble_lab_next(&m,100)==166); // rollover
    ble_lab_sent(&m,100); ble_lab_tick(&m,2100); assert(m.state==BLE_LAB_STALLED);
    puts("BLE lab: framing, capacity, credit, cumulative ACK, stale run, timeout, rollover PASS");
}
