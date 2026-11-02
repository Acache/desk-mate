#ifndef DESKMATE_BLE_VOICE_PROTOCOL_H
#define DESKMATE_BLE_VOICE_PROTOCOL_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define BLE_LAB_SERVICE "d35c0001-69b0-4b89-9a72-5fd60d200001"
#define BLE_LAB_CONTROL "d35c0002-69b0-4b89-9a72-5fd60d200001"
#define BLE_LAB_DATA    "d35c0003-69b0-4b89-9a72-5fd60d200001"
#define BLE_LAB_INFO    "d35c0004-69b0-4b89-9a72-5fd60d200001"
enum { BLE_LAB_IDLE, BLE_LAB_RUNNING, BLE_LAB_COMPLETE, BLE_LAB_STOPPED,
       BLE_LAB_STALLED, BLE_LAB_LOW_MTU, BLE_LAB_INVALID, BLE_LAB_BUSY };
enum { BLE_LAB_START=1, BLE_LAB_ACK=2, BLE_LAB_STOP=3 };
typedef struct {
    uint32_t run,total,rate,sent_seq,sent_bytes,ack_seq,ack_bytes,started,last_ack;
    uint16_t payload,window;
    uint8_t state;
} BleLabModel;
static inline uint16_t ble_lab_u16(const uint8_t *p) { return (uint16_t)(p[0]|((uint16_t)p[1]<<8)); }
static inline uint32_t ble_lab_u32(const uint8_t *p) {
    return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static inline void ble_lab_put16(uint8_t *p,uint16_t n) { p[0]=(uint8_t)n;p[1]=(uint8_t)(n>>8); }
static inline void ble_lab_put32(uint8_t *p,uint32_t n) { for(int i=0;i<4;i++)p[i]=(uint8_t)(n>>(i*8)); }
static inline void ble_lab_reset(BleLabModel *m) { memset(m,0,sizeof(*m)); }
static inline bool ble_lab_owns(uint16_t handle,uint32_t generation,bool ready,
                                uint16_t request_handle,uint32_t request_generation,uint32_t subscription_generation) {
    return ready&&handle!=UINT16_MAX&&generation&&handle==request_handle&&
        generation==request_generation&&generation==subscription_generation;
}
static inline bool ble_lab_start(BleLabModel *m,uint32_t run,uint32_t total,uint32_t rate,
                                  uint16_t window,uint16_t mtu,uint32_t now) {
    if(m->state==BLE_LAB_RUNNING) return false;
    if(!run||(m->run&&(int32_t)(run-m->run)<=0)||!total||total>3200000||rate<1000||rate>80000||
       (uint64_t)total>(uint64_t)rate*60||window<4||window>32) { m->state=BLE_LAB_INVALID;return false; }
    if(mtu<67) { m->state=BLE_LAB_LOW_MTU;return false; }
    ble_lab_reset(m);m->run=run;m->total=total;m->rate=rate;m->window=window;
    m->payload=(mtu>247?244:mtu-3)-16;m->state=BLE_LAB_RUNNING;m->started=m->last_ack=now;
    return true;
}
static inline void ble_lab_tick(BleLabModel *m,uint32_t now) {
    if(m->state!=BLE_LAB_RUNNING)return;
    if((m->sent_seq>m->ack_seq&&now-m->last_ack>=2000)||
       now-m->started>(uint64_t)m->total*1000/m->rate+5000) m->state=BLE_LAB_STALLED;
}
static inline uint16_t ble_lab_next(const BleLabModel *m,uint32_t now) {
    if(m->state!=BLE_LAB_RUNNING||m->sent_bytes>=m->total||m->sent_seq-m->ack_seq>=m->window)return 0;
    uint32_t left=m->total-m->sent_bytes;uint16_t n=left<m->payload?(uint16_t)left:m->payload;
    return (uint64_t)(now-m->started)*m->rate/1000>=m->sent_bytes+n?n:0;
}
static inline void ble_lab_sent(BleLabModel *m,uint32_t now) {
    uint16_t n=ble_lab_next(m,now);if(n){m->sent_seq++;m->sent_bytes+=n;}
}
static inline bool ble_lab_ack(BleLabModel *m,uint32_t run,uint32_t seq,uint32_t offset,uint32_t now) {
    if(m->state!=BLE_LAB_RUNNING||run!=m->run||seq<m->ack_seq||seq>m->sent_seq)return false;
    uint64_t expected=(uint64_t)seq*m->payload;if(expected>m->total)expected=m->total;
    if(offset!=expected)return false;
    if(seq>m->ack_seq){m->ack_seq=seq;m->ack_bytes=offset;m->last_ack=now;}
    if(m->ack_bytes==m->total)m->state=BLE_LAB_COMPLETE;
    return true;
}
static inline void ble_lab_header(uint8_t *p,uint32_t run,uint16_t n,uint32_t seq,uint32_t offset) {
    p[0]=1;p[1]=2;ble_lab_put16(p+2,n);ble_lab_put32(p+4,run);ble_lab_put32(p+8,seq);ble_lab_put32(p+12,offset);
}
static inline void ble_lab_fill(uint8_t *p,uint16_t n,uint32_t offset) {
    for(uint16_t i=0;i<n;i++)p[i]=(uint8_t)((offset+i)*31+17);
}
#endif
