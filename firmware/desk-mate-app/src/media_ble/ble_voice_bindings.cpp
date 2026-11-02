#include "ble_voice_bindings.h"
#include <Arduino.h>
#include <Preferences.h>
#include <freertos/semphr.h>
namespace {
struct Entry{char address[18];uint8_t type;char server[37];};
SemaphoreHandle_t lock(){static SemaphoreHandle_t l=xSemaphoreCreateMutex();return l;}
struct Store {
 Preferences prefs;Entry entries[8]={};bool ok;
 Store(){xSemaphoreTake(lock(),portMAX_DELAY);ok=prefs.begin("va_ble",false);if(ok&&prefs.getBytesLength("bindings")==sizeof(entries))prefs.getBytes("bindings",entries,sizeof(entries));for(auto&e:entries){e.address[17]=0;e.server[36]=0;e.type&=1;}}
 ~Store(){prefs.end();xSemaphoreGive(lock());}
 int find(const char*a,uint8_t t){for(int i=0;i<8;i++)if(entries[i].address[0]&&entries[i].type==(t&1)&&!strcmp(entries[i].address,a))return i;return -1;}
 bool save(){return ok&&prefs.putBytes("bindings",entries,sizeof(entries))==sizeof(entries);}
};
}
bool bv_binding_get(const char*a,uint8_t t,char out[37]){Store s;int i=s.find(a,t);out[0]=0;if(i<0)return false;memcpy(out,s.entries[i].server,37);out[36]=0;return true;}
bool bv_binding_set(const char*a,uint8_t t,const char*server){Store s;int i=s.find(a,t);if(i<0)for(int n=0;n<8;n++)if(!s.entries[n].address[0]){i=n;break;}if(i<0)return false;
 strlcpy(s.entries[i].address,a,18);s.entries[i].type=t&1;strlcpy(s.entries[i].server,server,37);return s.save();}
void bv_binding_forget(const char*a,uint8_t t){Store s;int i=s.find(a,t);if(i>=0){memset(&s.entries[i],0,sizeof(Entry));s.save();}}
void bv_binding_remove_server(const char*id){Store s;for(auto&e:s.entries)if(!strcmp(e.server,id))memset(&e,0,sizeof(e));s.save();}
