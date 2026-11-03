#include "ble_voice.h"
#include "ble_manager.h"
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <atomic>
namespace {
struct Packet {uint16_t length,handle;uint32_t generation;uint8_t bytes[244];};
QueueHandle_t incoming,received,outgoing;
NimBLEServer* server;NimBLECharacteristic *tx,*status;
std::atomic<bool> enabled(true),audio_active(false);
bool fast_interval=false;
std::atomic<uint32_t> subscribed(0);std::atomic<bool> reset(false),overflow(false);
portMUX_TYPE mux=portMUX_INITIALIZER_UNLOCKED;BvLink current_link={};
BvAssembler assembly={};BvMessage sending={};uint16_t offset;
uint32_t sent,acked,last_ack;
void clear(){
 memset(&assembly,0,sizeof(assembly));sending.length=0;offset=0;sent=acked=0;last_ack=millis();
 xQueueReset(incoming);xQueueReset(received);xQueueReset(outgoing);
}
class Rx:public NimBLECharacteristicCallbacks {
 void onWrite(NimBLECharacteristic*c,NimBLEConnInfo&i)override{
  if(!i.isEncrypted()||!i.isBonded())return;
  BleSnapshot v;ble_manager_read(&v);if(v.state!=BLE_READY||i.getConnHandle()!=v.handle)return;
  auto value=c->getValue();if(value.size()<16||value.size()>244){overflow=true;return;}
  Packet p={};p.length=value.size();p.handle=i.getConnHandle();p.generation=v.generation;memcpy(p.bytes,value.data(),p.length);
  if(xQueueSend(incoming,&p,0)!=pdTRUE)overflow=true;
 }
};
class Tx:public NimBLECharacteristicCallbacks{
 void onSubscribe(NimBLECharacteristic*,NimBLEConnInfo&i,uint16_t value)override{
  BleSnapshot v;ble_manager_read(&v);
  if(v.handle==i.getConnHandle()&&i.isEncrypted()&&i.isBonded())subscribed=value&1?v.generation:0;
 }
};
class Status:public NimBLECharacteristicCallbacks{
 void onRead(NimBLECharacteristic*c,NimBLEConnInfo&i)override{
  BvLink v;ble_voice_link(&v);uint8_t b[14]={1,(uint8_t)(v.ready?3:0)};
  bv_put16(b+2,i.getMTU());bv_put32(b+4,v.epoch);bv_put32(b+8,v.generation);bv_put16(b+12,BV_MAX);c->setValue(b,14);
 }
};
}
bool ble_voice_begin(NimBLEServer*s){
 server=s;incoming=xQueueCreate(32,sizeof(Packet));received=xQueueCreate(8,sizeof(BvMessage));outgoing=xQueueCreate(4,sizeof(BvMessage));
 if(!incoming||!received||!outgoing)return false;
 auto* service=s->createService(BV_SERVICE);
 auto* rx=service->createCharacteristic(BV_RX,NIMBLE_PROPERTY::WRITE|NIMBLE_PROPERTY::WRITE_NR|NIMBLE_PROPERTY::WRITE_ENC,244);
 tx=service->createCharacteristic(BV_TX,NIMBLE_PROPERTY::NOTIFY,244);
 status=service->createCharacteristic(BV_STATUS,NIMBLE_PROPERTY::READ|NIMBLE_PROPERTY::READ_ENC,14);
 static Rx r;static Tx t;static Status st;rx->setCallbacks(&r);tx->setCallbacks(&t);status->setCallbacks(&st);Serial.printf("BLE VOICE: service ready heap=%u\n",ESP.getFreeHeap());return true;
}
void ble_voice_link(BvLink*out){portENTER_CRITICAL(&mux);*out=current_link;portEXIT_CRITICAL(&mux);}
void ble_voice_reset(){reset=true;}
void ble_voice_enable(bool value){enabled=value;reset=true;}
void ble_voice_audio(bool value){audio_active=value;}
bool ble_voice_writable(){return outgoing&&uxQueueSpacesAvailable(outgoing)>0;}
uint16_t ble_voice_queue_free(){return outgoing?(uint16_t)uxQueueSpacesAvailable(outgoing):0;}
void ble_voice_stats(uint32_t *s,uint32_t *a){if(s)*s=sent;if(a)*a=acked;}
bool ble_voice_send(uint8_t kind,const uint8_t*b,size_t n){
 BvLink v;ble_voice_link(&v);if(!v.ready||!n||n>BV_MAX||!outgoing)return false;
 // Only the voice transport task calls send; keep the 2KB queue scratch off its stack.
 static BvMessage m={};m.kind=kind;m.length=n;m.epoch=v.epoch;memcpy(m.bytes,b,n);return xQueueSend(outgoing,&m,kind==1?pdMS_TO_TICKS(250):0)==pdTRUE;
}
bool ble_voice_receive(BvMessage*m){return received&&xQueueReceive(received,m,0)==pdTRUE;}
void ble_voice_poll(uint16_t handle,uint32_t generation,bool ready){
 if(!incoming)return;
 BvLink v;ble_voice_link(&v);bool available=enabled.load()&&ready&&subscribed.load()==generation;
 // Diagnose silent-link reports: only this log shows whether the voice link
 // actually lost readiness, and whether the TX queue/window is stuck.
 static uint32_t diag_at=0;
 if(v.ready!=available||millis()-diag_at>=10000){
  diag_at=millis();
  Serial.printf("BLE VOICE: ready=%d sub=%lu gen=%lu enabled=%d model=%d sent=%lu acked=%lu q=%u\n",
   (int)available,(unsigned long)subscribed.load(),(unsigned long)generation,(int)enabled.load(),
   (int)ready,(unsigned long)sent,(unsigned long)acked,(unsigned)ble_voice_queue_free());
 }
 bool generation_changed=v.generation!=generation;
 bool do_reset=reset.exchange(false);
 bool overflowed=overflow.exchange(false);
 bool hard_reset=generation_changed||do_reset||overflowed;
 if(hard_reset) Serial.printf("BLE VOICE: session reset gen=%d reset=%d overflow=%d ready=%d\n",
   (int)generation_changed,(int)do_reset,(int)overflowed,(int)available);
 if(hard_reset){
  clear();v.epoch=esp_random();if(!v.epoch)v.epoch=1;v.generation=generation;v.ready=available;
  if(available){auto info=server->getPeerInfoByHandle(handle);v.mtu=info.getMTU();BleSnapshot snap;ble_manager_read(&snap);
   if(snap.current>=0){strlcpy(v.address,snap.hosts[snap.current].address,sizeof(v.address));v.type=snap.hosts[snap.current].type;}
   server->setDataLen(handle,251);server->updateConnParams(handle,12,24,0,200);fast_interval=false;
   server->updatePhy(handle,BLE_GAP_LE_PHY_2M_MASK,BLE_GAP_LE_PHY_2M_MASK,0);
  }
  portENTER_CRITICAL(&mux);current_link=v;portEXIT_CRITICAL(&mux);
 } else if(v.ready!=available){
  /* Transient data-path flap (e.g. an HID subscription blip): keep the epoch,
   * queues and authenticated session; only the ready flag changes. */
  v.ready=available;
  portENTER_CRITICAL(&mux);current_link=v;portEXIT_CRITICAL(&mux);
 }
 if(!v.ready)return;
 // HID may subscribe before ATT exchange completes. Refresh the negotiated MTU
 // instead of retaining the initial 23-byte value for an entire voice session.
 uint16_t negotiated=server->getPeerInfoByHandle(handle).getMTU();
 if(negotiated!=v.mtu){v.mtu=negotiated;portENTER_CRITICAL(&mux);current_link=v;portEXIT_CRITICAL(&mux);Serial.printf("BLE VOICE: mtu=%u\n",negotiated);}
 bool fast=audio_active.load();if(fast!=fast_interval){server->updateConnParams(handle,12,fast?12:24,0,200);fast_interval=fast;}
 Packet p;
 for(int i=0;i<32&&xQueueReceive(incoming,&p,0)==pdTRUE;i++){
  if(p.handle!=handle||p.generation!=generation||bv_u32(p.bytes+2)!=v.epoch)continue;
  if(p.bytes[0]==1&&p.bytes[1]==3&&p.length==16&&!bv_u16(p.bytes+10)&&!bv_u16(p.bytes+12)&&!bv_u16(p.bytes+14)){
   uint32_t a=bv_u32(p.bytes+6);if(a>=acked&&a<=sent){acked=a;last_ack=millis();}
   continue;
  }
  int result=bv_append(&assembly,p.bytes,p.length,v.epoch);if(result<0){reset=true;break;}
  if(result==1){static BvMessage m={};m.kind=assembly.kind;m.length=assembly.total;m.epoch=v.epoch;memcpy(m.bytes,assembly.bytes,m.length+1);
   if(xQueueSend(received,&m,0)!=pdTRUE){reset=true;break;}}
  if(result==1||assembly.next%4==0){uint8_t ack[16];bv_header(ack,3,v.epoch,assembly.next,0,0,0);
   /* A transient controller buffer shortage must not invalidate the
    * authenticated session: the next cumulative ACK covers this one. */
   tx->notify(ack,16,handle);}
 }
 if(sent>acked&&millis()-last_ack>10000){
  Serial.printf("BLE VOICE: ack window stalled sent=%lu acked=%lu\n",(unsigned long)sent,(unsigned long)acked);
  reset=true;return;}
 for(int i=0;i<8&&sent-acked<BV_WINDOW;i++){
  if(!sending.length){if(xQueueReceive(outgoing,&sending,0)!=pdTRUE)break;offset=0;if(sending.epoch!=v.epoch){sending.length=0;continue;}}
  if(v.mtu<=BV_HEADER+3){reset=true;break;}
  uint16_t n=min(bv_payload(v.mtu),(uint16_t)(sending.length-offset));
  uint8_t packet[244];bv_header(packet,sending.kind,v.epoch,sent,offset,sending.length,n);memcpy(packet+16,sending.bytes+offset,n);
  if(!tx->notify(packet,n+16,handle))break;
  if(sent==acked)last_ack=millis();sent++;offset+=n;if(offset==sending.length)sending.length=0;
 }
}
