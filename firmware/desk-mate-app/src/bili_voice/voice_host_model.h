#ifndef DESKMATE_VOICE_HOST_MODEL_H
#define DESKMATE_VOICE_HOST_MODEL_H
#include "voice_hosts.h"
#include <string.h>
static inline VoiceHostCatalog vh_init(void) {
    VoiceHostCatalog c; memset(&c,0,sizeof(c)); c.active=c.target=c.preferred=c.previous=-1; return c;
}
static inline int vh_count(const VoiceHostCatalog *c) {
    int n=0;for(int i=0;i<VH_CAPACITY;i++)if(c->hosts[i].present)n++;return n;
}
static inline int vh_find(const VoiceHostCatalog *c,const char *id) {
    if(!id||!id[0])return -1;
    for(int i=0;i<VH_CAPACITY;i++)if(c->hosts[i].present&&!strcmp(c->hosts[i].id,id))return i;
    return -1;
}
static inline bool vh_start(VoiceHostCatalog *c,const char *id,bool busy) {
    int i=vh_find(c,id);if(i<0||busy||c->switching)return false;
    c->previous=c->active;c->target=i;c->switching=true;c->revision++;return true;
}
static inline void vh_finish(VoiceHostCatalog *c,bool ready) {
    if(!c->switching)return;
    c->active=ready?c->target:c->previous;c->target=c->active;c->switching=false;c->revision++;
}
static inline bool vh_remove(VoiceHostCatalog *c,const char *id) {
    int i=vh_find(c,id);if(i<0||c->switching)return false;
    memset(&c->hosts[i],0,sizeof(c->hosts[i]));
    if(c->active==i)c->active=c->target=-1;
    if(c->preferred==i)c->preferred=-1;
    c->revision++;return true;
}
#endif
