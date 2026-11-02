#include "voice_hosts.h"
#include "voice_host_model.h"
#include "provisioning.h"
#include <Preferences.h>
#include <freertos/semphr.h>
#include <stdio.h>
#include <string.h>

static VoiceHostCatalog catalog;
static char added_id[37];
static uint32_t credentials_revision;
static SemaphoreHandle_t store_lock;
static portMUX_TYPE view_mux=portMUX_INITIALIZER_UNLOCKED;
uint32_t vc_hosts_credentials_revision(){portENTER_CRITICAL(&view_mux);uint32_t r=credentials_revision;portEXIT_CRITICAL(&view_mux);return r;}
static void publish(const VoiceHostCatalog &c) { portENTER_CRITICAL(&view_mux);catalog=c;portEXIT_CRITICAL(&view_mux); }
void vc_hosts_catalog(VoiceHostCatalog *out) { portENTER_CRITICAL(&view_mux);*out=catalog;portEXIT_CRITICAL(&view_mux); }
void vc_hosts_added(char out[37]) { portENTER_CRITICAL(&view_mux);memcpy(out,added_id,37);portEXIT_CRITICAL(&view_mux); }
static void key(char out[8],const char *prefix,int i) { snprintf(out,8,"%s%d",prefix,i); }
static bool config_ok(const VoiceConfig &c) {
 return c.valid && c.device_id[36]==0 && strlen(c.device_id)==36 && c.server_id[36]==0 && strlen(c.server_id)==36 &&
 c.token[64]==0 && strlen(c.token)==64 && c.ca_pem[sizeof(c.ca_pem)-1]==0 && strstr(c.ca_pem,"BEGIN CERTIFICATE") &&
 c.tls_name[sizeof(c.tls_name)-1]==0 && c.manual_host[sizeof(c.manual_host)-1]==0 && c.port;
}
// Variable-length CA avoids five padded 4KB blobs exhausting the NVS partition.
#pragma pack(push,1)
struct HostDisk {
 uint32_t magic;char device_id[37],server_id[37],token[65],tls[96],host[96];
 uint16_t port,ca_bytes;int64_t time;
};
#pragma pack(pop)
static bool read_config(Preferences &p,const char *key,VoiceConfig *out){
 size_t n=p.getBytesLength(key);if(n<sizeof(HostDisk)+2||n>sizeof(HostDisk)+sizeof(out->ca_pem))return false;
 uint8_t *bytes=(uint8_t *)malloc(n);if(!bytes)return false;
 bool ok=p.getBytes(key,bytes,n)==n;
 HostDisk h={};if(ok)memcpy(&h,bytes,sizeof(h));
 ok=ok&&h.magic==0x56483131&&h.ca_bytes>=2&&h.ca_bytes<=sizeof(out->ca_pem)&&n==sizeof(h)+h.ca_bytes&&bytes[n-1]==0&&
 h.device_id[36]==0&&h.server_id[36]==0&&h.token[64]==0&&h.tls[95]==0&&h.host[95]==0;
 if(ok){
  memset(out,0,sizeof(*out));memcpy(out->device_id,h.device_id,37);memcpy(out->server_id,h.server_id,37);memcpy(out->token,h.token,65);
  memcpy(out->tls_name,h.tls,96);memcpy(out->manual_host,h.host,96);memcpy(out->ca_pem,bytes+sizeof(h),h.ca_bytes);
  out->port=h.port;out->trusted_time_unix_s=h.time;out->valid=true;ok=config_ok(*out);
 }
 free(bytes);return ok;
}
static bool write_config(Preferences &p,const char *key,const VoiceConfig &c){
 HostDisk h={};h.magic=0x56483131;h.port=c.port;h.time=c.trusted_time_unix_s;h.ca_bytes=(uint16_t)(strlen(c.ca_pem)+1);
 memcpy(h.device_id,c.device_id,37);memcpy(h.server_id,c.server_id,37);memcpy(h.token,c.token,65);memcpy(h.tls,c.tls_name,96);memcpy(h.host,c.manual_host,96);
 size_t n=sizeof(h)+h.ca_bytes;uint8_t *bytes=(uint8_t *)malloc(n);if(!bytes)return false;
 memcpy(bytes,&h,sizeof(h));memcpy(bytes+sizeof(h),c.ca_pem,h.ca_bytes);bool ok=p.putBytes(key,bytes,n)==n;free(bytes);return ok;
}
static void ascii_name(char out[VH_NAME_BYTES],const char *name,int slot) {
 size_t n=0;if(name)for(;*name && n<VH_NAME_BYTES-1;name++)if((unsigned char)*name>=32&&(unsigned char)*name<127)out[n++]=*name;
 out[n]=0;if(!n)snprintf(out,VH_NAME_BYTES,"Mac %d",slot+1);
}
struct WifiDisk{char ssid[33],password[65];};
static bool read_network(Preferences &p,char ssid[33],char password[65]){
 WifiDisk w={};
 if(p.getBytesLength("wifi")==sizeof(w)&&p.getBytes("wifi",&w,sizeof(w))==sizeof(w)&&w.ssid[32]==0&&w.password[64]==0){memcpy(ssid,w.ssid,33);memcpy(password,w.password,65);return ssid[0]!=0;}
 snprintf(ssid,33,"%s",p.getString("ssid","").c_str());snprintf(password,65,"%s",p.getString("pass","").c_str());return ssid[0]!=0;
}
void vc_hosts_init() {
 if(store_lock)return;
 store_lock=xSemaphoreCreateMutex();catalog=vh_init();
 Preferences p;if(!p.begin("va_hosts",false))return;
 VoiceHostCatalog c=vh_init();
 VoiceConfig *cfg=(VoiceConfig *)calloc(1,sizeof(VoiceConfig));
 if(!cfg){p.end();return;}
 for(int i=0;i<VH_CAPACITY;i++) {
  char k[8];key(k,"cfg",i);
  if(!read_config(p,k,cfg))continue;
  c.hosts[i].present=true;snprintf(c.hosts[i].id,37,"%s",cfg->server_id);
  key(k,"name",i);ascii_name(c.hosts[i].name,p.getString(k,"").c_str(),i);
 }
 // Import once. The marker prevents a removed legacy host from resurrecting.
 if(!p.getBool("migrated",false)) {
  const VoiceConfig *old=vc_config_peek();
  if(!vh_count(&c)&&old&&old->valid) {
   *cfg=*old;cfg->ssid[0]=cfg->password[0]=0;
   if(write_config(p,"cfg0",*cfg)) {
    c.hosts[0].present=true;snprintf(c.hosts[0].id,37,"%s",cfg->server_id);
    snprintf(c.hosts[0].name,VH_NAME_BYTES,"My Mac");p.putString("name0","My Mac");p.putInt("last",0);
   } else {free(cfg);p.end();publish(c);return;}
  }
  if(p.putBool("migrated",true)) {
   Preferences legacy;if(legacy.begin("va_cfg",false)){
    const char *keys[]={"dev_id","srv_id","token","tls","host","ca","port","time"};
    for(const char *k:keys)legacy.remove(k);legacy.end();
   }
  }
 }
 int last=p.getInt("last",vh_count(&c)?0:-1),boot=p.getInt("boot",-1);
 c.preferred=boot>=0&&boot<VH_CAPACITY&&c.hosts[boot].present?boot:-1;
 c.active=c.target=c.preferred>=0?c.preferred:(last>=0&&last<VH_CAPACITY&&c.hosts[last].present?last:-1);
 p.end();free(cfg);publish(c);
}
bool vc_hosts_load(int slot,VoiceConfig *out) {
 if(!store_lock||slot<0||slot>=VH_CAPACITY||xSemaphoreTake(store_lock,pdMS_TO_TICKS(100))!=pdTRUE)return false;
 Preferences p;bool ok=false;
 if(p.begin("va_hosts",true)) { char k[8];key(k,"cfg",slot);ok=read_config(p,k,out);p.end(); }
 if(ok&&p.begin("va_cfg",true)) {
  read_network(p,out->ssid,out->password);p.end();
 }
 xSemaphoreGive(store_lock);return ok;
}
void vc_hosts_read_network(char ssid[33],char password[65]){
 ssid[0]=password[0]=0;if(!store_lock||xSemaphoreTake(store_lock,pdMS_TO_TICKS(100))!=pdTRUE)return;
 Preferences p;if(p.begin("va_cfg",true)){read_network(p,ssid,password);p.end();}xSemaphoreGive(store_lock);
}
bool vc_hosts_network(const char *ssid,const char *password) {
 if(!ssid||!password||!ssid[0]||strlen(ssid)>32||strlen(password)>64||!store_lock||xSemaphoreTake(store_lock,pdMS_TO_TICKS(100))!=pdTRUE)return false;
 Preferences p;bool ok=p.begin("va_cfg",false);
 if(ok){WifiDisk w={};snprintf(w.ssid,sizeof(w.ssid),"%s",ssid);snprintf(w.password,sizeof(w.password),"%s",password);ok=p.putBytes("wifi",&w,sizeof(w))==sizeof(w);if(ok){p.remove("ssid");p.remove("pass");}p.end();}
 xSemaphoreGive(store_lock);return ok;
}
bool vc_hosts_save(const VoiceConfig *cfg,const char *name) {
 if(!cfg||!config_ok(*cfg)||!store_lock||xSemaphoreTake(store_lock,pdMS_TO_TICKS(100))!=pdTRUE)return false;
 VoiceHostCatalog c;vc_hosts_catalog(&c);int slot=vh_find(&c,cfg->server_id);
 if(slot<0)for(int i=0;i<VH_CAPACITY;i++)if(!c.hosts[i].present){slot=i;break;}
 if(slot<0){xSemaphoreGive(store_lock);return false;}
 VoiceConfig *saved=(VoiceConfig *)malloc(sizeof(*saved));if(!saved){xSemaphoreGive(store_lock);return false;}
 *saved=*cfg;saved->ssid[0]=saved->password[0]=0;
 Preferences p;bool ok=p.begin("va_hosts",false);
 char k[8];key(k,"cfg",slot);
 if(ok) {
  ok=write_config(p,k,*saved);
  if(ok) {
   bool fresh=!c.hosts[slot].present;c.hosts[slot].present=true;snprintf(c.hosts[slot].id,37,"%s",cfg->server_id);
   if(fresh)ascii_name(c.hosts[slot].name,name,slot);
   key(k,"name",slot);p.putString(k,c.hosts[slot].name);
   if(c.active<0&&vh_count(&c)==1){c.active=c.target=slot;p.putInt("last",slot);}
   c.revision++;publish(c);
   portENTER_CRITICAL(&view_mux);snprintf(added_id,37,"%s",cfg->server_id);credentials_revision++;portEXIT_CRITICAL(&view_mux);
  }
  p.end();
 }
 free(saved);xSemaphoreGive(store_lock);
 // Network is global: adding another computer never replaces an existing SSID.
 if(ok){Preferences network;if(network.begin("va_cfg",true)){char ssid[33],pass[65];bool empty=!read_network(network,ssid,pass);network.end();if(empty&&cfg->ssid[0])ok=vc_hosts_network(cfg->ssid,cfg->password);}}
 return ok;
}
bool vc_hosts_remember(int slot) {
 if(!store_lock||xSemaphoreTake(store_lock,pdMS_TO_TICKS(100))!=pdTRUE)return false;
 Preferences p;bool ok=p.begin("va_hosts",false);
 if(ok){ok=p.putInt("last",slot)>0;p.end();}
 if(ok){VoiceHostCatalog c;vc_hosts_catalog(&c);c.active=c.target=slot;c.revision++;publish(c);}
 xSemaphoreGive(store_lock);return ok;
}
bool vc_hosts_edit(const VoiceHostRequest *r) {
 if(!r||!store_lock||xSemaphoreTake(store_lock,pdMS_TO_TICKS(100))!=pdTRUE)return false;
 VoiceHostCatalog c;vc_hosts_catalog(&c);int i=vh_find(&c,r->id);bool ok=false;
 Preferences p;if(i>=0&&p.begin("va_hosts",false)) {
  char k[8];
  if(r->op==VH_RENAME) {key(k,"name",i);char n[VH_NAME_BYTES];ascii_name(n,r->name,i);ok=p.putString(k,n)>0;if(ok)memcpy(c.hosts[i].name,n,sizeof(n));}
  else if(r->op==VH_DEFAULT){ok=p.putInt("boot",i)>0;if(ok)c.preferred=i;}
  else if(r->op==VH_FORGET){key(k,"cfg",i);ok=p.remove(k);if(ok){key(k,"name",i);p.remove(k);vh_remove(&c,r->id);if(c.preferred<0)p.putInt("boot",-1);p.putInt("last",c.active);}}
  p.end();if(ok){c.revision++;publish(c);}
 }
 xSemaphoreGive(store_lock);return ok;
}
