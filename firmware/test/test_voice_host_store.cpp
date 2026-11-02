#include <assert.h>
#include <stdio.h>
#include "Preferences.h"
#include "../desk-mate-app/src/bili_voice/provisioning.h"
#include "../desk-mate-app/src/bili_voice/voice_host_model.h"
std::map<std::string,std::vector<unsigned char>> Preferences::data;
bool Preferences::fail_blob=false;
static VoiceConfig legacy;
const VoiceConfig *vc_config_peek(){return &legacy;}
static VoiceConfig make_config(int i){
 VoiceConfig c={};snprintf(c.device_id,37,"00000000-0000-4000-8000-%012d",i);
 snprintf(c.server_id,37,"11111111-1111-4111-8111-%012d",i);memset(c.token,'a',64);
 strcpy(c.ca_pem,"-----BEGIN CERTIFICATE----- test CA -----END CERTIFICATE-----");
 strcpy(c.tls_name,"voice.local");strcpy(c.ssid,"New network");strcpy(c.password,"new-password");c.port=8765;c.valid=true;return c;
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
 r.op=VH_FORGET;assert(vc_hosts_edit(&r));vc_hosts_catalog(&c);assert(c.active==-1&&c.preferred==-1&&vh_count(&c)==4);
 assert(!vc_hosts_load(2,&loaded));assert(!vc_hosts_edit(&r));
 assert(vc_hosts_save(&overflow,"Added Mac"));vc_hosts_catalog(&c);assert(vh_count(&c)==5&&c.active==-1); // no implicit fallback
 assert(vc_hosts_network("Changed Wi-Fi",""));assert(vc_hosts_load(0,&loaded)&&!strcmp(loaded.ssid,"Changed Wi-Fi"));
 Preferences::fail_blob=true;assert(!vc_hosts_network("Failed network","bad"));assert(vc_hosts_load(0,&loaded)&&!strcmp(loaded.ssid,"Changed Wi-Fi"));
 assert(!vc_hosts_network("", ""));puts("voice store: migration, append/dedup, capacity, write failure, forget, shared Wi-Fi PASS");
}
