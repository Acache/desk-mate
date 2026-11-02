#include "ble_voice.h"
#include "ble_voice_bindings.h"
#include "../bili_voice/transport.h"
#include "../bili_voice/voice_controller.h"
#include "ble_manager.h"
#include "ble_model.h"
#include "ble_metadata.h"
#include "ble_voice_lab.h"
#include <Arduino.h>
#include <Preferences.h>
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>
#include <atomic>
static_assert(CONFIG_BT_NIMBLE_MAX_BONDS==BLE_HOST_LIMIT,"Use sketch build_opt.h for eight saved bonds");
static_assert(CONFIG_BT_NIMBLE_MAX_CONNECTIONS==1&&CONFIG_NIMBLE_MAX_CONNECTIONS==1,"Only one BLE connection is allowed");
static_assert(CONFIG_BT_NIMBLE_MAX_CCCDS>=BLE_HOST_LIMIT*4,"Keep HID, battery, service-change and voice subscriptions for every host");
#ifdef DESKMATE_BLE_DIAGNOSTICS
#include <esp_heap_caps.h>
#endif
#ifdef USING_NIMBLE_ARDUINO_HEADERS
#include <nimble/nimble/host/include/host/ble_hs.h>
#include <nimble/nimble/host/include/host/ble_store.h>
#include <nimble/nimble/host/include/host/ble_gatt.h>
#include <nimble/porting/nimble/include/os/os_mbuf.h>
#else
#include <host/ble_hs.h>
#include <host/ble_store.h>
#include <host/ble_gatt.h>
#include <os/os_mbuf.h>
#endif

namespace {
BleModel model;
BleSnapshot published;
portMUX_TYPE snapshot_lock=portMUX_INITIALIZER_UNLOCKED;
QueueHandle_t requests, events;
Preferences prefs;
bool prefs_ok, initialized, adv_dirty=true, dirty_prefs=false;
NimBLEServer *server;
NimBLECharacteristic *input;
std::atomic<uint16_t> live_handle(BLE_NO_HANDLE);
std::atomic<uint32_t> live_generation(0);
std::atomic<bool> queue_fault(false), radio_changed(false);
std::atomic<uint32_t> report_pending(0);
uint32_t next_generation=0, retry_at=0, retry_delay=1000;
uint32_t radio_status_at=0, inactive_since=0;
uint16_t key_handle=BLE_NO_HANDLE;
uint32_t key_generation=0, release_at=0, release_deadline=0;
char last_success[18]; uint8_t last_type;
using Stored=BleStored;
enum EventKind { CONNECT, AUTH, SUBSCRIBE, DISCONNECT, IDENTITY, REMOTE_NAME };
struct Event {
    EventKind kind; uint16_t handle; uint32_t generation;
    char address[18]; uint8_t type; bool ok, bonded; int reason;
    char name[BLE_NAME_BYTES];
};
Event pending_auth; bool auth_pending;
uint32_t auth_until,auth_retry,name_generation,name_until;
void default_name(BleHost &h) {
    snprintf(h.name,sizeof(h.name),"Device %.2s%.2s",h.address+12,h.address+15);
    h.name_kind=BLE_NAME_DEFAULT;
}
int remote_name(uint16_t handle,const ble_gatt_error *error,ble_gatt_attr *attr,void *arg) {
    uint32_t gen=(uint32_t)(uintptr_t)arg;
    if(handle!=live_handle.load()||gen!=live_generation.load()) return 0;
    Event ev={}; ev.kind=REMOTE_NAME; ev.handle=handle; ev.generation=gen;
    if(!error->status&&attr&&attr->om) {
        size_t length=OS_MBUF_PKTLEN(attr->om);
        if(length>=sizeof(ev.name)) length=sizeof(ev.name)-1;
        if(!os_mbuf_copydata(attr->om,0,length,ev.name)) {
            ev.ok=length>0;
            // Current font is Latin; offer a quick user name for unsupported
            // scripts rather than presenting missing-glyph boxes as a name.
            for(size_t i=0;i<length;i++) if((uint8_t)ev.name[i]<32||(uint8_t)ev.name[i]>126) ev.ok=false;
        }
    }
    xQueueSend(events,&ev,0); // Optional lookup must not retire a media session.
    return 0;
}
int find(const char *address,uint8_t type) {
    for(int i=0;i<model.view.count;i++)
        if(model.view.hosts[i].type==(type&1)&&!strcmp(model.view.hosts[i].address,address)) return i;
    return -1;
}
int find_view(const BleSnapshot &v,const NimBLEAddress &address) {
    std::string text=address.toString();
    for(int i=0;i<v.count;i++) if(v.hosts[i].type==(address.getType()&1)&&!strcmp(v.hosts[i].address,text.c_str())) return i;
    return -1;
}
void publish() {
    if(initialized) model.view.advertising=NimBLEDevice::getAdvertising()->isAdvertising();
    portENTER_CRITICAL(&snapshot_lock); published=model.view; portEXIT_CRITICAL(&snapshot_lock);
}
void message(const char *text) {
    snprintf(model.view.message,sizeof(model.view.message),"%s",text); model.view.revision++;
}
void persist() {
    if(!dirty_prefs) return;
    dirty_prefs=false;
    if(!prefs_ok) { message("Settings unavailable; changes last until restart"); return; }
    Stored data={}; data.version=3; data.enabled=model.view.enabled; data.automatic=model.view.auto_reconnect;
    snprintf(data.last,sizeof(data.last),"%s",last_success); data.last_type=last_type;
    memcpy(data.hosts,model.view.hosts,sizeof(data.hosts));
    if(prefs.putBytes("settings",&data,sizeof(data))!=sizeof(data)) message("Could not save Bluetooth settings");
}
void post(EventKind kind,NimBLEConnInfo &info,bool ok=false,int reason=0) {
    if(info.getConnHandle()!=live_handle.load()) return;
    Event ev={}; ev.kind=kind; ev.handle=info.getConnHandle(); ev.generation=live_generation.load();
    NimBLEAddress address=info.getIdAddress();
    snprintf(ev.address,sizeof(ev.address),"%s",address.toString().c_str()); ev.type=address.getType()&1;
    ev.ok=ok; ev.bonded=info.isBonded(); ev.reason=reason;
    if(xQueueSend(events,&ev,0)!=pdTRUE) queue_fault.store(true);
}
// Never use the library's round-robin overflow handler: it evicts old bonds.
int store_full(struct ble_store_status_event *event,void *) { return event->event_code==BLE_STORE_EVENT_FULL?0:BLE_HS_ENOMEM; }
class ServerCallbacks:public NimBLEServerCallbacks {
    void onConnect(NimBLEServer *s,NimBLEConnInfo &info) override {
        BleSnapshot v; ble_manager_read(&v);
        int i=find_view(v,info.getIdAddress());
        bool allowed=v.enabled&&((v.state==BLE_PAIRING&&i<0&&v.count<v.capacity)||
            ((v.state==BLE_WAITING||v.state==BLE_BACKGROUND)&&i>=0&&i==v.selected));
        Serial.printf("BLE: connection handle=%u host=%d allowed=%d state=%d\n",info.getConnHandle(),i,allowed,(int)v.state);
        uint16_t expected=BLE_NO_HANDLE;
        if(!allowed||!live_handle.compare_exchange_strong(expected,info.getConnHandle())) {
            radio_changed.store(true);
            s->disconnect(info.getConnHandle()); return;
        }
        if(++next_generation==0) ++next_generation;
        live_generation.store(next_generation); post(CONNECT,info);
    }
    void onAuthenticationComplete(NimBLEConnInfo &info) override { post(AUTH,info,info.isEncrypted()); }
    void onIdentity(NimBLEConnInfo &info) override { post(IDENTITY,info); }
    void onDisconnect(NimBLEServer *,NimBLEConnInfo &info,int reason) override {
#ifdef DESKMATE_BLE_LAB
        ble_voice_lab_disconnected(info.getConnHandle());
#endif
        radio_changed.store(true);
        post(DISCONNECT,info,false,reason);
        uint16_t expected=info.getConnHandle(); live_handle.compare_exchange_strong(expected,BLE_NO_HANDLE);
    }
};
class InputCallbacks:public NimBLECharacteristicCallbacks {
    void onSubscribe(NimBLECharacteristic *,NimBLEConnInfo &info,uint16_t value) override { post(SUBSCRIBE,info,(value&1)!=0); }
};
void release_reservation(uint32_t generation) {
    if(generation) report_pending.compare_exchange_strong(generation,0);
}
void clear_key(bool send_release) {
    if(key_handle!=BLE_NO_HANDLE&&send_release&&key_handle==live_handle.load()&&key_generation==live_generation.load()) {
        uint8_t released[2]={0,0}; input->notify(released,sizeof(released),key_handle);
    }
    release_reservation(key_generation);
    key_handle=BLE_NO_HANDLE;
}
void drop_link() {
    auth_pending=false; model.view.name_pending=false;
    clear_key(true);
    release_reservation(live_generation.load());
    uint16_t h=live_handle.load();
    if(h!=BLE_NO_HANDLE) server->disconnect(h);
}
void import_bonds(const Stored &saved) {
    model.view.count=0;
    int count=NimBLEDevice::getNumBonds();
    for(int i=0;i<count&&model.view.count<model.view.capacity;i++) {
        NimBLEAddress addr=NimBLEDevice::getBondedAddress(i);
        BleHost &h=model.view.hosts[model.view.count];
        snprintf(h.address,sizeof(h.address),"%s",addr.toString().c_str()); h.type=addr.getType()&1;
        default_name(h);
        for(const auto &old:saved.hosts) if((old.type&1)==h.type&&!strcmp(old.address,h.address)&&old.name_kind!=BLE_NAME_DEFAULT) {
            snprintf(h.name,sizeof(h.name),"%s",old.name); h.name_kind=old.name_kind; break;
        }
        model.view.count++;
    }
    if(count>model.view.capacity) message("Bond capacity changed; check existing hosts");
}
static uint32_t service_changed_generation=0;
void became_ready() {
    if(!ble_model_ready(&model)) return;
    BleHost &h=model.view.hosts[model.view.current];
    if(strcmp(last_success,h.address)||last_type!=h.type) {
        snprintf(last_success,sizeof(last_success),"%s",h.address); last_type=h.type; dirty_prefs=true;
    }
    retry_delay=1000; adv_dirty=true;
    if(service_changed_generation!=model.view.generation){service_changed_generation=model.view.generation;server->sendServiceChangedIndication();}
    if(name_generation!=model.view.generation&&h.name_kind!=BLE_NAME_USER) {
        name_generation=model.view.generation; name_until=millis()+2500;
        static const ble_uuid16_t uuid=BLE_UUID16_INIT(0x2a00);
        model.view.name_pending=ble_gattc_read_by_uuid(model.view.handle,1,UINT16_MAX,&uuid.u,remote_name,
            (void *)(uintptr_t)name_generation)==0;
    }
}
void process_event(const Event &ev,uint32_t now) {
    int i=find(ev.address,ev.type);
    switch(ev.kind) {
    case CONNECT:
        if(!ble_model_link(&model,ev.handle,ev.generation,i,now)) { server->disconnect(ev.handle); break; }
        publish();
        server->updateConnParams(ev.handle,12,24,0,200);
        if(!NimBLEDevice::startSecurity(ev.handle)) { message("Security setup failed; try pairing again"); server->disconnect(ev.handle); }
        break;
    case AUTH:
        if(model.session_retired||!ble_model_matches(&model,ev.handle,ev.generation)) break;
        if(!ev.ok||!ev.bonded) { model.secure=false; model.view.state=BLE_CONNECTING; message("Pairing failed; forget on host and retry"); server->disconnect(ev.handle); break; }
        if(NimBLEAddress(ev.address,ev.type).isRpa()||!NimBLEDevice::isBonded(NimBLEAddress(ev.address,ev.type))) {
            if(!auth_pending) { pending_auth=ev; auth_until=now+5000; auth_retry=now; auth_pending=true; }
            message("Saving pairing identity..."); break;
        }
        auth_pending=false;
        Serial.printf("BLE: verified stored identity host=%d bonds=%d type=%u\n",i,NimBLEDevice::getNumBonds(),ev.type);
        if(i<0&&model.pairing&&model.view.count<model.view.capacity) {
            i=model.view.count++;
            BleHost &h=model.view.hosts[i];
            snprintf(h.address,sizeof(h.address),"%s",ev.address); h.type=ev.type;
            default_name(h); dirty_prefs=true;
        }
        if(i<0||(!model.pairing&&i!=model.view.selected)) { server->disconnect(ev.handle); break; }
        model.view.current=(int8_t)i; model.view.selected=(int8_t)i;
        ble_model_security(&model,ev.handle,ev.generation,true,now); became_ready();
        break;
    case SUBSCRIBE:
        ble_model_subscribe(&model,ev.handle,ev.generation,ev.ok,now); became_ready(); break;
    case DISCONNECT:
        if(ble_model_matches(&model,ev.handle,ev.generation)) {
            clear_key(false);
            ble_model_disconnected(&model,ev.handle,ev.generation,now);
            retry_at=now+retry_delay; retry_delay=retry_delay<16000?retry_delay*2:30000;
            Serial.printf("BLE: disconnected reason=%d\n",ev.reason);
        }
        break;
    case IDENTITY:
        if(auth_pending&&ble_model_matches(&model,ev.handle,ev.generation)) {
            snprintf(pending_auth.address,sizeof(pending_auth.address),"%s",ev.address); pending_auth.type=ev.type;
        }
        break;
    case REMOTE_NAME:
        if(!ble_model_matches(&model,ev.handle,ev.generation)||model.session_retired) break;
        model.view.name_pending=false;
        Serial.printf("BLE: peer name available=%d\n",ev.ok);
        if(ev.ok&&model.view.current>=0) {
            BleHost &h=model.view.hosts[model.view.current];
            if(h.name_kind!=BLE_NAME_USER) {
                snprintf(h.name,sizeof(h.name),"%s",ev.name); h.name_kind=BLE_NAME_REMOTE; dirty_prefs=true;
            }
        }
        break;
    }
    adv_dirty=true;
}
void request(const BleRequest &r,uint32_t now) {
    int i=find(r.address,r.type);
    if(r.op==BLE_CMD_REPORT) {
        if(!ble_model_ready(&model)||r.generation!=model.view.generation||key_handle!=BLE_NO_HANDLE||
           live_handle.load()!=model.view.handle||live_generation.load()!=model.view.generation) { release_reservation(r.generation); return; }
        uint8_t report[2]={(uint8_t)r.usage,(uint8_t)(r.usage>>8)};
        if(input->notify(report,sizeof(report),model.view.handle)) {
            key_handle=model.view.handle; key_generation=model.view.generation;
            release_at=now+50; release_deadline=now+500;
        } else { release_reservation(r.generation); message("Media key failed; reconnect host"); }
        return;
    }
    if((vc_controller_busy()||vc_transport_pending())&&
       (r.op==BLE_CMD_PAIR||r.op==BLE_CMD_CONNECT||r.op==BLE_CMD_FORGET||r.op==BLE_CMD_ENABLE)){
        message("Finish the voice command before changing hosts");return;
    }
    model.view.message[0]=0;
    switch(r.op) {
    case BLE_CMD_CONNECT:
        if(i<0) { message("Host is no longer paired"); return; }
        if(!model.view.enabled) { message("Resume Bluetooth first"); return; }
        if(ble_model_ready(&model)&&i==model.view.current) return;
        ble_model_connect(&model,i,now); drop_link(); break;
    case BLE_CMD_DISCONNECT:
        if(r.address[0]&&i!=model.view.current&&i!=model.view.selected) { message("This host is not connected"); return; }
        ble_model_disconnect(&model); drop_link(); break;
    case BLE_CMD_PAIR:
        if(!ble_model_pair(&model,now)) { message(model.view.enabled?"8 hosts saved; forget one to add another":"Resume Bluetooth first"); return; }
        drop_link(); break;
    case BLE_CMD_CANCEL: ble_model_cancel(&model,now); drop_link(); break;
    case BLE_CMD_FORGET:
        if(i<0) { message("Host is no longer paired"); return; }
        // Forgetting a different saved host must not interrupt active media.
        if(i==model.view.current||i==model.view.selected||model.pairing) { ble_model_disconnect(&model); drop_link(); }
        if(!NimBLEDevice::deleteBond(NimBLEAddress(r.address,r.type))) { message("Could not forget host; retry"); break; }
        bv_binding_forget(r.address,r.type);
        if(!strcmp(last_success,r.address)&&last_type==r.type) last_success[0]=0;
        for(int n=i;n+1<model.view.count;n++) model.view.hosts[n]=model.view.hosts[n+1];
        model.view.count--; memset(&model.view.hosts[model.view.count],0,sizeof(BleHost));
        if(model.view.selected==i) model.view.selected=-1;
        else if(model.view.selected>i) model.view.selected--;
        if(model.view.current>i) model.view.current--;
        message("Forgotten. Also forget Desk-Mate on host"); dirty_prefs=true; break;
    case BLE_CMD_RENAME:
        if(i<0) { message("Host is no longer paired"); return; }
        if(!r.name[0]) { message("Enter a name first"); return; }
        for(size_t n=0;n<sizeof(r.name)&&r.name[n];n++) if(r.name[n]<32||r.name[n]>126) { message("Use a short English name"); return; }
        snprintf(model.view.hosts[i].name,BLE_NAME_BYTES,"%.*s",BLE_NAME_BYTES-1,r.name);
        model.view.hosts[i].name_kind=BLE_NAME_USER; dirty_prefs=true; break;
    case BLE_CMD_AUTO:
        model.view.auto_reconnect=r.value;
        if(!r.value&&!model.pairing&&!ble_model_ready(&model)) { ble_model_disconnect(&model); drop_link(); }
        else if(r.value&&model.view.state==BLE_IDLE&&model.view.selected>=0) {
            model.suppressed=false; ble_model_wait(&model,now);
        }
        dirty_prefs=true; break;
    case BLE_CMD_ENABLE:
        ble_model_pause(&model,!r.value,now); drop_link(); dirty_prefs=true; break;
    default: break;
    }
    model.view.revision++; adv_dirty=true; retry_at=now; retry_delay=1000;
}
void advertising(uint32_t now) {
    if(!adv_dirty||!ble_due(now,retry_at)) return;
    auto *adv=NimBLEDevice::getAdvertising();
    // A connection consumes advertising. Do not send ADV_DISABLE again when
    // the host already reports it stopped (some controllers reject that).
    if(adv->isAdvertising()) {
        int stop_rc=ble_gap_adv_stop();
        if(stop_rc&&stop_rc!=BLE_HS_EALREADY) {
            Serial.printf("BLE: advertising stop failed rc=%d\n",stop_rc);
            retry_at=now+1000; return;
        }
    }
    bool pairing=model.pairing, target=ble_model_allows(&model,model.view.selected);
    if(!model.view.enabled||(!pairing&&!target)||ble_model_ready(&model)) { adv_dirty=false; return; }
    if(live_handle.load()!=BLE_NO_HANDLE||model.view.handle!=BLE_NO_HANDLE) return;
    uint8_t peers=server->getConnectedCount();
    if(peers) return; // Wait for the previous/rejected link to finish disconnecting.
    // Replace the controller list atomically. NimBLE 2.5.0 whiteListRemove()
    // increments an iterator invalidated by erase/shrink; avoid that wrapper.
    // This module exclusively owns the list and never uses the wrapper cache.
    ble_addr_t peer={};
    if(!pairing) {
        BleHost &h=model.view.hosts[model.view.selected];
        peer=*NimBLEAddress(h.address,h.type).getBase();
    }
    int filter_rc=ble_gap_wl_set(pairing?nullptr:&peer,pairing?0:1);
    if(filter_rc) {
        Serial.printf("BLE: filter update failed rc=%d\n",filter_rc);
        message("Bluetooth filter update failed"); retry_at=now+1000; return;
    }
    adv->setScanFilter(false,!pairing);
    adv->setConnectableMode(BLE_GAP_CONN_MODE_UND);
    adv->setDiscoverableMode(pairing?BLE_GAP_DISC_MODE_GEN:BLE_GAP_DISC_MODE_NON);
    adv->setAdvertisingInterval(model.view.state==BLE_BACKGROUND?1600:320);
    // In 2.5.0 setters such as setName append fields instead of replacing
    // them. Build complete fresh packets so every restart stays bounded and
    // uploads the current flags, without duplicate names or stale cache data.
    NimBLEAdvertisementData packet,scan;
    bool packet_ok=packet.setFlags(pairing?BLE_HS_ADV_F_DISC_GEN:BLE_HS_ADV_F_BREDR_UNSUP)&&
        packet.setAppearance(GENERIC_HID)&&packet.addServiceUUID(NimBLEUUID((uint16_t)0x1812))&&
        packet.setName("Desk-Mate")&&scan.setName("Desk-Mate");
    if(!packet_ok||!adv->setAdvertisementData(packet)||!adv->setScanResponseData(scan)) {
        message("Could not update Bluetooth discovery"); retry_at=now+1000; return;
    }
    publish(); // callback policy must be visible before advertising begins.
    uint32_t duration=pairing?model.view.remaining_ms:0;
    if(adv->start(duration)) {
        adv_dirty=false; retry_delay=1000;
        Serial.printf("BLE: advertising pairing=%d filtered=%d duration=%lu packet=%s\n",pairing,!pairing,
            (unsigned long)duration,adv->getAdvertisementData().toString().c_str());
    }
    else {
        Serial.println("BLE: advertising start failed");
        message("Bluetooth busy; retrying"); retry_at=now+retry_delay;
        retry_delay=retry_delay<16000?retry_delay*2:30000;
    }
}
} // namespace

void ble_manager_read(BleSnapshot *out) {
    if(!out) return;
    portENTER_CRITICAL(&snapshot_lock); *out=published; portEXIT_CRITICAL(&snapshot_lock);
}
bool ble_manager_submit(const BleRequest *r) { return r&&requests&&xQueueSend(requests,r,0)==pdTRUE; }
bool ble_manager_ready() { BleSnapshot v; ble_manager_read(&v); return v.state==BLE_READY; }
bool ble_manager_send(uint16_t usage) {
    BleSnapshot v; ble_manager_read(&v);
    if(v.state!=BLE_READY) return false;
    uint32_t expected=0;
    if(!report_pending.compare_exchange_strong(expected,v.generation)) return false;
    BleRequest r={}; r.op=BLE_CMD_REPORT; r.usage=usage; r.generation=v.generation;
    if(ble_manager_submit(&r)) return true;
    release_reservation(v.generation); return false;
}
bool ble_manager_begin() {
    ble_model_init(&model,CONFIG_BT_NIMBLE_MAX_BONDS);
    requests=xQueueCreate(8,sizeof(BleRequest)); events=xQueueCreate(16,sizeof(Event));
    if(!requests||!events) { message("Bluetooth unavailable: low memory"); model.view.state=BLE_ERROR; publish(); return false; }
    if(!NimBLEDevice::init("Desk-Mate")) { message("Bluetooth initialization failed"); model.view.state=BLE_ERROR; publish(); return false; }
    NimBLEDevice::setMTU(247);
    ble_hs_cfg.store_status_cb=store_full;
    NimBLEDevice::setSecurityAuth(true,false,true); NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
    Stored saved={}; prefs_ok=prefs.begin("dm-ble",false);
    uint8_t raw[sizeof(BleStored)>sizeof(BleStoredV1)?sizeof(BleStored):sizeof(BleStoredV1)]={};
    size_t stored_size=prefs_ok?prefs.getBytesLength("settings"):0;
    if(stored_size&&stored_size<=sizeof(raw)) {
        prefs.getBytes("settings",raw,stored_size);
        ble_metadata_decode(raw,stored_size,&saved);
    }
    if(saved.version==3) {
        model.view.enabled=saved.enabled; model.view.auto_reconnect=saved.automatic;
        snprintf(last_success,sizeof(last_success),"%s",saved.last); last_type=saved.last_type;
    }
    import_bonds(saved);
    int last=find(last_success,last_type);
    model.view.selected=last>=0?last:(!saved.version&&model.view.count?0:-1);
    model.suppressed=!model.view.auto_reconnect; ble_model_wait(&model,millis());
    if(last<0&&saved.version&&model.view.count) message("Choose a saved device to reconnect");
    server=NimBLEDevice::createServer(); static ServerCallbacks sc; static InputCallbacks ic;
    server->setCallbacks(&sc,false); server->advertiseOnDisconnect(false);
    auto *hid=new NimBLEHIDDevice(server); input=hid->getInputReport(1); input->setCallbacks(&ic);
    static const uint8_t report[]={0x05,0x0C,0x09,0x01,0xA1,0x01,0x85,0x01,0x15,0x00,0x26,0xFF,0x03,0x19,0x00,0x2A,0xFF,0x03,0x75,0x10,0x95,0x01,0x81,0x00,0xC0};
    hid->setManufacturer("Desk-Mate"); hid->setPnp(0x02,0x303A,0x4001,0x0200); hid->setHidInfo(0,2);
    hid->setReportMap((uint8_t *)report,sizeof(report)); uint8_t zero[2]={0,0}; input->setValue(zero,2);
#ifdef DESKMATE_BLE_LAB
    // Append after existing HID services so their attribute order stays stable.
    if(!ble_voice_lab_begin(server)){message("BLE lab unavailable: low memory");model.view.state=BLE_ERROR;publish();return false;}
#endif
    if(!ble_voice_begin(server)){message("BLE voice unavailable: low memory");model.view.state=BLE_ERROR;publish();return false;}
    server->start();
    auto *adv=NimBLEDevice::getAdvertising();
    adv->setAdvertisingCompleteCallback([](NimBLEAdvertising *) { radio_changed.store(true); });
    adv->enableScanResponse(true);
    initialized=true; dirty_prefs=true; persist(); publish();
    Serial.printf("BLE: manager ready bonds=%u target=%d enabled=%d auto=%d\n",model.view.count,model.view.selected,model.view.enabled,model.view.auto_reconnect);
    return true;
}
void ble_manager_poll() {
    if(!initialized) return;
    uint32_t now=millis();
#ifdef DESKMATE_BLE_DIAGNOSTICS
    static uint32_t heap_check_at;
    if(ble_due(now,heap_check_at)) {
        heap_check_at=now+1000;
        if(!heap_caps_check_integrity_all(true)) {
            Serial.println("BLE: heap integrity failed"); abort();
        }
    }
#endif
    if(radio_changed.exchange(false)) {
        adv_dirty=true;
        if(model.pairing) retry_at=now;
    }
    if(queue_fault.exchange(false)) {
        ble_model_disconnect(&model); drop_link();
        // Reconcile even if the matching disconnect event was dropped.
        model.view.handle=BLE_NO_HANDLE;
        message("Bluetooth event overflow; select host to retry"); adv_dirty=true;
    }
    Event ev; for(int n=0;n<16&&xQueueReceive(events,&ev,0)==pdTRUE;n++) process_event(ev,now);
    if(auth_pending) {
        if(model.session_retired||!ble_model_matches(&model,pending_auth.handle,pending_auth.generation)) auth_pending=false;
        else if(ble_due(now,auth_until)) {
            auth_pending=false; message("Pairing was not saved; please retry"); drop_link();
        } else if(ble_due(now,auth_retry)) {
            auth_retry=now+100;
            auto info=server->getPeerInfoByHandle(pending_auth.handle);
            if(info.getConnHandle()==pending_auth.handle&&info.isEncrypted()&&info.isBonded()) {
                auto id=info.getIdAddress();
                snprintf(pending_auth.address,sizeof(pending_auth.address),"%s",id.toString().c_str()); pending_auth.type=id.getType()&1;
                if(NimBLEDevice::isBonded(NimBLEAddress(pending_auth.address,pending_auth.type))) process_event(pending_auth,now);
            }
        }
    }
    if(model.view.name_pending&&ble_due(now,name_until)) model.view.name_pending=false;
    BleRequest r; for(int n=0;n<8&&xQueueReceive(requests,&r,0)==pdTRUE;n++) request(r,now);
    BleState before=model.view.state; bool was_pairing=model.pairing;
    ble_model_tick(&model,now);
    if(before!=model.view.state||was_pairing!=model.pairing) {
        adv_dirty=true;
        if(before==BLE_CONNECTING||was_pairing) drop_link();
        if(was_pairing&&!model.pairing) message("Pairing window closed");
        else if(before==BLE_WAITING) message("Open host Bluetooth settings to connect");
    }
    if(key_handle!=BLE_NO_HANDLE&&ble_due(now,release_at)) {
        bool same=key_handle==live_handle.load()&&key_generation==live_generation.load();
        uint8_t zero[2]={0,0};
        if(!same||input->notify(zero,2,key_handle)) { release_reservation(key_generation); key_handle=BLE_NO_HANDLE; }
        else if(ble_due(now,release_deadline)) { message("Key release failed; reconnecting"); drop_link(); }
    }
    // Recover a consumed/aborted advertising procedure even if its completion
    // callback is lost. Never restart while a connection is being admitted.
    if(!adv_dirty&&model.view.enabled&&!ble_model_ready(&model)&&
       (model.pairing||ble_model_allows(&model,model.view.selected))&&
       live_handle.load()==BLE_NO_HANDLE&&model.view.handle==BLE_NO_HANDLE&&
       !server->getConnectedCount()&&
       !NimBLEDevice::getAdvertising()->isAdvertising()) {
        if(!inactive_since) inactive_since=now?now:1;
        else if(now-inactive_since>=500) {
            adv_dirty=true; retry_at=now; inactive_since=0;
            Serial.println("BLE: recovering inactive advertising");
        }
    } else inactive_since=0;
    advertising(now); persist(); publish();
    ble_voice_poll(model.view.handle,model.view.generation,ble_model_ready(&model));
#ifdef DESKMATE_BLE_LAB
    ble_voice_lab_poll(model.view.handle,model.view.generation,ble_model_ready(&model));
#endif
    if(ble_due(now,radio_status_at)) {
        radio_status_at=now+5000;
        Serial.printf("BLE: radio state=%d pairing=%d active=%d peers=%u live=%u model=%u dirty=%d remaining=%lu retry_in=%ld message=%s\n",
            (int)model.view.state,model.pairing,NimBLEDevice::getAdvertising()->isAdvertising(),
            server->getConnectedCount(),live_handle.load(),model.view.handle,adv_dirty,(unsigned long)model.view.remaining_ms,
            (long)(int32_t)(retry_at-now),model.view.message);
    }
}
