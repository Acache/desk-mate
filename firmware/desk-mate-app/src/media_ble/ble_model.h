#ifndef DESKMATE_BLE_MODEL_H
#define DESKMATE_BLE_MODEL_H
#include "ble_contracts.h"
#include <string.h>
typedef struct {
    BleSnapshot view;
    bool secure, subscribed, suppressed, pairing, session_retired;
    int8_t previous;
    bool previous_suppressed;
    uint32_t deadline, pair_deadline;
} BleModel;
static inline bool ble_due(uint32_t now, uint32_t end) { return (int32_t)(now-end)>=0; }
static inline void ble_model_init(BleModel *m, uint8_t capacity) {
    memset(m,0,sizeof(*m)); m->view.enabled=true; m->view.auto_reconnect=true;
    m->view.capacity=capacity>BLE_HOST_LIMIT?BLE_HOST_LIMIT:capacity;
    m->view.selected=m->view.current=m->previous=-1; m->view.handle=BLE_NO_HANDLE;
    m->view.state=BLE_IDLE;
}
static inline void ble_model_wait(BleModel *m, uint32_t now) {
    m->session_retired=true;
    m->secure=m->subscribed=false; m->view.current=-1;
    m->view.state=!m->view.enabled?BLE_PAUSED:
        (m->suppressed||m->view.selected<0)?BLE_IDLE:BLE_WAITING;
    m->deadline=now+BLE_WAIT_MS; m->view.remaining_ms=0; m->view.revision++;
}
static inline void ble_model_connect(BleModel *m, int index, uint32_t now) {
    if(index<0||index>=m->view.count||!m->view.enabled) return;
    m->pairing=false; m->view.selected=(int8_t)index; m->suppressed=false;
    ble_model_wait(m,now);
}
static inline void ble_model_disconnect(BleModel *m) {
    m->pairing=false; m->suppressed=true; m->session_retired=true; m->secure=m->subscribed=false;
    m->view.current=-1; m->view.state=m->view.enabled?BLE_IDLE:BLE_PAUSED; m->view.revision++;
}
static inline bool ble_model_pair(BleModel *m, uint32_t now) {
    if(!m->view.enabled||m->view.count>=m->view.capacity) return false;
    if(!m->pairing) { m->previous=m->view.selected; m->previous_suppressed=m->suppressed; }
    m->pairing=true; m->pair_deadline=now+BLE_PAIR_MS; m->suppressed=false;
    if(++m->view.window_id==0) ++m->view.window_id;
    m->session_retired=true;
    m->secure=m->subscribed=false; m->view.current=-1;
    m->view.state=BLE_PAIRING; m->view.remaining_ms=BLE_PAIR_MS; m->view.revision++; return true;
}
static inline void ble_model_cancel(BleModel *m, uint32_t now) {
    if(m->pairing) {
        m->view.selected=m->previous;
        m->suppressed=m->previous_suppressed||!m->view.auto_reconnect;
        m->pairing=false;
    } else m->suppressed=true;
    ble_model_wait(m,now);
}
static inline bool ble_model_allows(const BleModel *m, int index) {
    if(!m->view.enabled||m->suppressed) return false;
    if(m->pairing) return index<0&&m->view.count<m->view.capacity;
    return index>=0&&index==m->view.selected&&
        (m->view.state==BLE_WAITING||m->view.state==BLE_BACKGROUND||m->view.state==BLE_CONNECTING);
}
static inline bool ble_model_link(BleModel *m, uint16_t handle, uint32_t gen, int index, uint32_t now) {
    if(m->view.handle!=BLE_NO_HANDLE||!ble_model_allows(m,index)) return false;
    m->view.handle=handle; m->view.generation=gen; m->view.current=(int8_t)index;
    m->session_retired=false;
    m->secure=m->subscribed=false; m->view.state=BLE_CONNECTING;
    m->deadline=now+BLE_WAIT_MS; m->view.revision++; return true;
}
static inline bool ble_model_matches(const BleModel *m, uint16_t handle, uint32_t gen) {
    return m->view.handle==handle&&m->view.generation==gen&&handle!=BLE_NO_HANDLE;
}
static inline bool ble_model_ready(const BleModel *m) {
    return m->view.enabled&&!m->suppressed&&!m->session_retired&&m->view.handle!=BLE_NO_HANDLE&&
        m->view.current>=0&&m->view.current==m->view.selected&&m->secure&&m->subscribed;
}
static inline void ble_model_update_ready(BleModel *m) {
    if(ble_model_ready(m)) {
        m->pairing=false; m->view.state=BLE_READY; m->view.remaining_ms=0; m->view.message[0]=0;
    }
    m->view.revision++;
}
static inline void ble_model_security(BleModel *m,uint16_t handle,uint32_t gen,bool ok,uint32_t now) {
    (void)now;
    if(m->session_retired||!ble_model_matches(m,handle,gen)) return;
    m->secure=ok;
    if(!ok&&m->view.state==BLE_READY) { m->view.state=BLE_CONNECTING; m->deadline=now+BLE_WAIT_MS; }
    ble_model_update_ready(m);
}
static inline void ble_model_subscribe(BleModel *m,uint16_t handle,uint32_t gen,bool ok,uint32_t now) {
    (void)now;
    if(m->session_retired||!ble_model_matches(m,handle,gen)) return;
    m->subscribed=ok;
    if(!ok&&m->view.state==BLE_READY) { m->view.state=BLE_CONNECTING; m->deadline=now+BLE_WAIT_MS; }
    ble_model_update_ready(m);
}
static inline void ble_model_disconnected(BleModel *m,uint16_t handle,uint32_t gen,uint32_t now) {
    if(!ble_model_matches(m,handle,gen)) return;
    m->view.handle=BLE_NO_HANDLE; m->view.current=-1; m->secure=m->subscribed=false;
    if(m->pairing) { m->view.state=BLE_PAIRING; m->view.revision++; }
    else {
        if(!m->view.auto_reconnect && m->view.state==BLE_READY) m->suppressed=true;
        ble_model_wait(m,now);
    }
}
static inline void ble_model_pause(BleModel *m,bool paused,uint32_t now) {
    m->view.enabled=!paused; m->pairing=false; m->suppressed=paused||!m->view.auto_reconnect;
    ble_model_wait(m,now);
}
static inline void ble_model_tick(BleModel *m,uint32_t now) {
    if(m->pairing) {
        if(ble_due(now,m->pair_deadline)) ble_model_cancel(m,now);
        else m->view.remaining_ms=m->pair_deadline-now;
    } else if(m->view.state==BLE_WAITING||m->view.state==BLE_CONNECTING) {
        if(ble_due(now,m->deadline)) {
            m->view.state=m->view.auto_reconnect?BLE_BACKGROUND:BLE_IDLE;
            if(!m->view.auto_reconnect) m->suppressed=true;
            m->session_retired=true;
    m->secure=m->subscribed=false; m->view.current=-1;
            m->view.remaining_ms=0; m->view.revision++;
        } else m->view.remaining_ms=m->deadline-now;
    }
}
#endif
