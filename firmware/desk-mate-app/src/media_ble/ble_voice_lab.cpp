#include "ble_voice_lab.h"
#ifdef DESKMATE_BLE_LAB
#include "ble_voice_protocol.h"
#include "ble_manager.h"
#include "../bili_voice/voice_controller.h"
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <atomic>

namespace {
struct Request { uint8_t bytes[16];uint16_t handle,mtu;uint32_t generation; };
QueueHandle_t queue;
NimBLEServer *owner;
NimBLECharacteristic *control,*data;
BleLabModel model;
uint32_t current_generation;
uint16_t current_handle=BLE_NO_HANDLE;
std::atomic<uint32_t> subscribed(0);
std::atomic<bool> overflow(false);
bool gate;
uint8_t status[28]={1};
portMUX_TYPE status_lock=portMUX_INITIALIZER_UNLOCKED;
void publish() {
    uint8_t bytes[28]={1,model.state};ble_lab_put16(bytes+2,model.payload);
    ble_lab_put32(bytes+4,model.run);ble_lab_put32(bytes+8,model.sent_seq);ble_lab_put32(bytes+12,model.sent_bytes);
    ble_lab_put32(bytes+16,model.ack_seq);ble_lab_put32(bytes+20,model.ack_bytes);ble_lab_put32(bytes+24,model.rate);
    portENTER_CRITICAL(&status_lock);memcpy(status,bytes,sizeof(status));portEXIT_CRITICAL(&status_lock);
}
class ControlCallbacks:public NimBLECharacteristicCallbacks {
    void onRead(NimBLECharacteristic *c,NimBLEConnInfo &) override {
        uint8_t bytes[28];portENTER_CRITICAL(&status_lock);memcpy(bytes,status,sizeof(bytes));portEXIT_CRITICAL(&status_lock);
        c->setValue(bytes,sizeof(bytes));
    }
    void onWrite(NimBLECharacteristic *c,NimBLEConnInfo &info) override {
        if(!info.isEncrypted()||!info.isBonded())return;
        BleSnapshot v;ble_manager_read(&v);
        if(v.handle!=info.getConnHandle()||v.state!=BLE_READY)return;
        auto value=c->getValue();if(value.size()!=16)return;
        Request r={};memcpy(r.bytes,value.data(),16);r.handle=info.getConnHandle();r.mtu=info.getMTU();r.generation=v.generation;
        if(xQueueSend(queue,&r,0)!=pdTRUE)overflow.store(true);
    }
};
class DataCallbacks:public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic *,NimBLEConnInfo &info,uint16_t value) override {
        BleSnapshot v;ble_manager_read(&v);
        if(v.handle==info.getConnHandle()&&info.isEncrypted()&&info.isBonded())subscribed.store(value&1?v.generation:0);
    }
};
class InfoCallbacks:public NimBLECharacteristicCallbacks {
    void onRead(NimBLECharacteristic *c,NimBLEConnInfo &info) override {
        BleSnapshot v;ble_manager_read(&v);
        uint8_t bytes[12]={1,(uint8_t)owner->getConnectedCount()};
        ble_lab_put16(bytes+2,info.getMTU());ble_lab_put16(bytes+4,info.getConnInterval());
        ble_lab_put32(bytes+8,v.generation);c->setValue(bytes,sizeof(bytes));
    }
};
}
bool ble_voice_lab_begin(NimBLEServer *server) {
    owner=server;queue=xQueueCreate(8,sizeof(Request));if(!queue)return false;
    auto *service=server->createService(BLE_LAB_SERVICE);
    control=service->createCharacteristic(BLE_LAB_CONTROL,NIMBLE_PROPERTY::READ|NIMBLE_PROPERTY::READ_ENC|
        NIMBLE_PROPERTY::WRITE|NIMBLE_PROPERTY::WRITE_NR|NIMBLE_PROPERTY::WRITE_ENC,28);
    data=service->createCharacteristic(BLE_LAB_DATA,NIMBLE_PROPERTY::NOTIFY,244);
    auto *info=service->createCharacteristic(BLE_LAB_INFO,NIMBLE_PROPERTY::READ|NIMBLE_PROPERTY::READ_ENC,12);
    static ControlCallbacks cc;static DataCallbacks dc;static InfoCallbacks ic;
    control->setCallbacks(&cc);data->setCallbacks(&dc);info->setCallbacks(&ic);
    ble_lab_reset(&model);publish();control->setValue(status,sizeof(status));return true;
}
void ble_voice_lab_disconnected(uint16_t handle) {
    (void)handle;subscribed.store(0);
}
void ble_voice_lab_poll(uint16_t handle,uint32_t generation,bool ready) {
    if(!queue)return;
    uint32_t now=millis();
    if(handle!=current_handle||generation!=current_generation) {
        ble_lab_reset(&model);current_handle=handle;current_generation=generation;
    }
    bool fault=overflow.exchange(false);
    if(fault)xQueueReset(queue);
    if(!ready||subscribed.load()!=generation||fault) {
        if(model.state==BLE_LAB_RUNNING)model.state=BLE_LAB_STOPPED;
    }
    Request r;
    for(int i=0;i<8&&xQueueReceive(queue,&r,0)==pdTRUE;i++) {
        if(!ble_lab_owns(handle,generation,ready,r.handle,r.generation,subscribed.load())||r.bytes[0]!=1)continue;
        uint32_t run=ble_lab_u32(r.bytes+4),a=ble_lab_u32(r.bytes+8),b=ble_lab_u32(r.bytes+12);
        if(r.bytes[1]==BLE_LAB_START) {
            if(model.state==BLE_LAB_RUNNING)continue;
            if(!gate&&!vc_controller_management(true)){model.state=BLE_LAB_BUSY;continue;}
            gate=true;
            if(ble_lab_start(&model,run,a,b,ble_lab_u16(r.bytes+2),r.mtu,now)) {
                owner->setDataLen(handle,251);
                owner->updateConnParams(handle,12,12,0,200); // request 15ms for the bounded transfer
                owner->updatePhy(handle,BLE_GAP_LE_PHY_2M_MASK,BLE_GAP_LE_PHY_2M_MASK,0);
                Serial.printf("BLE LAB: start payload=%u rate=%lu bytes=%lu peers=%u\n",model.payload,
                    (unsigned long)model.rate,(unsigned long)model.total,owner->getConnectedCount());
            }
        } else if(r.bytes[1]==BLE_LAB_ACK)ble_lab_ack(&model,run,a,b,now);
        else if(r.bytes[1]==BLE_LAB_STOP&&run==model.run&&model.state==BLE_LAB_RUNNING)model.state=BLE_LAB_STOPPED;
    }
    ble_lab_tick(&model,now);
    for(int n=0;n<8;n++) {
        uint16_t length=ble_lab_next(&model,now);if(!length||!ready||subscribed.load()!=generation)break;
        uint8_t packet[244];ble_lab_header(packet,model.run,length,model.sent_seq,model.sent_bytes);
        ble_lab_fill(packet+16,length,model.sent_bytes);
        if(!data->notify(packet,length+16,handle))break;
        ble_lab_sent(&model,now);
    }
    if(gate&&model.state!=BLE_LAB_RUNNING) {
        if(vc_controller_management(false)) {
            gate=false;
            if(ready)owner->updateConnParams(handle,12,24,0,200);
            Serial.printf("BLE LAB: stopped state=%u received=%lu sent=%lu\n",model.state,
                (unsigned long)model.ack_bytes,(unsigned long)model.sent_bytes);
        }
    }
    publish();
}
#endif
