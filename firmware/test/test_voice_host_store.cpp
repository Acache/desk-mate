#include <assert.h>
#include <stdio.h>
#include "Preferences.h"
#include "../desk-mate-app/src/bili_voice/provisioning.h"
#include "../desk-mate-app/src/bili_voice/voice_host_model.h"
#include "../desk-mate-app/src/media_ble/ble_voice_bindings.h"
std::map<std::string,std::vector<unsigned char>> Preferences::data;
bool Preferences::fail_blob=false;
bool Preferences::fail_string=false;
bool Preferences::fail_remove=false;
static std::map<std::string,std::string> peer_bindings;
static bool fail_binding_write=false;
static int binding_writes=0;
static std::string binding_key(const char *peer,uint8_t type){return std::string(peer)+"/"+std::to_string(type);}
bool bv_binding_get(const char *peer,uint8_t type,char out[37]){
 auto it=peer_bindings.find(binding_key(peer,type));out[0]=0;
 if(it==peer_bindings.end())return false;
 snprintf(out,37,"%s",it->second.c_str());return true;
}
bool bv_binding_set(const char *peer,uint8_t type,const char *server){
 if(fail_binding_write){fail_binding_write=false;return false;}
 peer_bindings[binding_key(peer,type)]=server;binding_writes++;return true;
}
static VoiceConfig legacy;
const VoiceConfig *vc_config_peek(){return &legacy;}
static VoiceConfig make_config(int i){
 VoiceConfig c={};snprintf(c.device_id,37,"00000000-0000-4000-8000-%012d",i);
 snprintf(c.server_id,37,"11111111-1111-4111-8111-%012d",i);memset(c.token,'a',64);
 strcpy(c.ca_pem,"-----BEGIN CERTIFICATE----- test CA -----END CERTIFICATE-----");
 strcpy(c.tls_name,"voice.local");strcpy(c.ssid,"New network");strcpy(c.password,"new-password");c.port=8765;c.valid=true;return c;
}
static void change_device(VoiceConfig *cfg,int number,char token){
 snprintf(cfg->device_id,37,"00000000-0000-4000-8000-%012d",number);memset(cfg->token,token,64);cfg->token[64]=0;
}
static void test_usb_binding_grants(){
 const char *peer_a="12:34:56:78:9a:bc",*peer_b="12:34:56:78:9a:bd",*peer_c="12:34:56:78:9a:be";
 VoiceConfig old={};assert(vc_hosts_load(0,&old));
 auto other=make_config(99);peer_bindings[binding_key(peer_a,0)]=other.server_id;
 peer_bindings[binding_key(peer_b,0)]=other.server_id;
 auto original_bindings=peer_bindings;
 // Legacy imports do not become blanket permission to steal a peer binding.
 assert(!vc_hosts_binding_pending(0,old.device_id));
 assert(!vc_hosts_finalize_binding(0,&old,peer_a,0,true));assert(peer_bindings==original_bindings);

 VoiceConfig grant=old;change_device(&grant,90,'d');assert(vc_hosts_save(&grant,"USB grant"));
 assert(vc_hosts_binding_pending(0,grant.device_id));assert(peer_bindings==original_bindings);
 // auth_start/failed proof cannot consume the USB permission or write a binding.
 int writes=binding_writes;
 assert(!vc_hosts_finalize_binding(0,&grant,peer_a,0,false));
 assert(vc_hosts_binding_pending(0,grant.device_id)&&binding_writes==writes);
 const char *invalid[]={"","00:00:00:00:00:00","ff:ff:ff:ff:ff:ff","12-34-56-78-9a-bc","zz:34:56:78:9a:bc"};
 for(const char *peer:invalid)assert(!vc_hosts_finalize_binding(0,&grant,peer,0,true));
 assert(!vc_hosts_finalize_binding(0,&grant,peer_a,2,true));
 assert(vc_hosts_binding_pending(0,grant.device_id)&&binding_writes==writes);
 // A previous in-flight handshake cannot consume a fresh device's grant.
 assert(!vc_hosts_finalize_binding(0,&old,peer_a,0,true));
 assert(vc_hosts_binding_pending(0,grant.device_id));
 assert(vc_hosts_finalize_binding(0,&grant,peer_a,0,true));
 assert(!vc_hosts_binding_pending(0,grant.device_id));
 assert(peer_bindings[binding_key(peer_a,0)]==grant.server_id);
 assert(peer_bindings[binding_key(peer_b,0)]==other.server_id);
 // The consumed grant cannot overwrite a second already-bound peer.
 assert(!vc_hosts_finalize_binding(0,&grant,peer_b,0,true));
 assert(vc_hosts_finalize_binding(0,&grant,peer_a,0,true)); // normal same-peer reconnect

 VoiceConfig third={};assert(vc_hosts_load(1,&third));
 assert(vc_hosts_finalize_binding(1,&third,peer_c,0,true)); // consume its own earlier USB grant
 assert(!vc_hosts_binding_pending(1,third.device_id));
 assert(!vc_hosts_finalize_binding(1,&third,peer_a,0,true));
 assert(peer_bindings[binding_key(peer_a,0)]==grant.server_id);

 auto stale=grant;change_device(&grant,91,'e');assert(vc_hosts_save(&grant,"fresh grant"));
 assert(!vc_hosts_finalize_binding(0,&stale,peer_b,0,true));
 assert(vc_hosts_binding_pending(0,grant.device_id));
 stale=grant;memset(grant.token,'f',64);assert(vc_hosts_save(&grant,"same id new secret"));
 assert(!vc_hosts_finalize_binding(0,&stale,peer_b,0,true));
 assert(vc_hosts_binding_pending(0,grant.device_id));

 // Failure to persist the binding must not leave a reusable override grant.
 fail_binding_write=true;
 assert(!vc_hosts_finalize_binding(0,&grant,peer_b,0,true));
 assert(!vc_hosts_binding_pending(0,grant.device_id));
 assert(peer_bindings[binding_key(peer_b,0)]==other.server_id);
 assert(!vc_hosts_finalize_binding(0,&grant,peer_b,0,true));

 change_device(&grant,92,'g');Preferences::fail_string=true;
 assert(!vc_hosts_save(&grant,"failed pending write"));
 assert(!vc_hosts_binding_pending(0,grant.device_id));
 assert(!vc_hosts_finalize_binding(0,&grant,peer_b,0,true));
 assert(peer_bindings[binding_key(peer_b,0)]==other.server_id);
 assert(vc_hosts_save(&grant,"retry USB grant"));
 Preferences::fail_remove=true;writes=binding_writes;
 assert(!vc_hosts_finalize_binding(0,&grant,peer_b,0,true));
 assert(binding_writes==writes&&vc_hosts_binding_pending(0,grant.device_id));
 assert(vc_hosts_finalize_binding(0,&grant,peer_b,0,true));
 assert(!vc_hosts_binding_pending(0,grant.device_id));
 VoiceConfig restored={};assert(vc_hosts_load(0,&restored));
 assert(!strcmp(restored.ssid,"Changed Wi-Fi")&&!strcmp(restored.token,grant.token));
 assert(vc_hosts_load(1,&restored)&&!strcmp(restored.token,third.token));
}
int main(){
 legacy=make_config(0);Preferences p;p.begin("va_cfg");p.putString("ssid","Original Wi-Fi");p.putString("pass","original-password");p.putString("token","legacy-secret");p.end();
 vc_hosts_init();VoiceHostCatalog c;vc_hosts_catalog(&c);assert(vh_count(&c)==1&&c.active==0);
 assert(!Preferences::data.count("va_cfg/token")); // legacy credentials removed only after migration
 VoiceConfig loaded={};assert(vc_hosts_load(0,&loaded));assert(!strcmp(loaded.token,legacy.token));assert(!strcmp(loaded.ssid,"Original Wi-Fi"));
 for(int i=1;i<5;i++){auto next=make_config(i);assert(vc_hosts_save(&next,"My Mac"));}
 auto overflow=make_config(5);assert(!vc_hosts_save(&overflow,"Overflow"));
 vc_hosts_catalog(&c);assert(vh_count(&c)==5&&c.active==0);
 auto refresh=make_config(2);memset(refresh.token,'b',64);assert(vc_hosts_save(&refresh,"Renamed Mac"));
 vc_hosts_catalog(&c);assert(vh_count(&c)==5&&c.active==0);assert(!strcmp(c.hosts[2].name,"My Mac"));assert(vc_hosts_load(2,&loaded)&&loaded.token[0]=='b');assert(!strcmp(loaded.ssid,"Original Wi-Fi"));
 Preferences::fail_blob=true;memset(refresh.token,'c',64);assert(!vc_hosts_save(&refresh,"Failed write"));
 assert(vc_hosts_load(2,&loaded)&&loaded.token[0]=='b');
 VoiceHostRequest r={};strcpy(r.id,c.hosts[2].id);r.op=VH_DEFAULT;assert(vc_hosts_edit(&r));assert(vc_hosts_remember(2));
 r.op=VH_FORGET;assert(vc_hosts_edit(&r));vc_hosts_catalog(&c);assert(c.active==-1&&c.preferred==-1&&vh_count(&c)==4);assert(!Preferences::data.count("va_hosts/bind2"));
 assert(!vc_hosts_load(2,&loaded));assert(!vc_hosts_edit(&r));
 assert(vc_hosts_save(&overflow,"Added Mac"));vc_hosts_catalog(&c);assert(vh_count(&c)==5&&c.active==-1); // no implicit fallback
 assert(vc_hosts_network("Changed Wi-Fi",""));assert(vc_hosts_load(0,&loaded)&&!strcmp(loaded.ssid,"Changed Wi-Fi"));
 Preferences::fail_blob=true;assert(!vc_hosts_network("Failed network","bad"));assert(vc_hosts_load(0,&loaded)&&!strcmp(loaded.ssid,"Changed Wi-Fi"));
 assert(!vc_hosts_network("", ""));test_usb_binding_grants();
 puts("voice store: migration, isolation, shared Wi-Fi, one-use USB binding grant and stale/failed proof PASS");
}
