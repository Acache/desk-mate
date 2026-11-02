#include <assert.h>
#include <stdio.h>
#include "../desk-mate-app/src/bili_voice/voice_host_model.h"
int main(void) {
 VoiceHostCatalog c=vh_init();assert(c.active==-1 && vh_count(&c)==0);
 c.hosts[0].present=c.hosts[3].present=true;
 strcpy(c.hosts[0].id,"A");strcpy(c.hosts[3].id,"B");c.active=c.target=c.preferred=0;
 assert(!vh_start(&c,"missing",false));assert(!vh_start(&c,"B",true));
 assert(vh_start(&c,"B",false));assert(c.active==0 && c.target==3);
 assert(!vh_start(&c,"A",false));assert(!vh_remove(&c,"A"));
 vh_finish(&c,false);assert(c.active==0&&!c.switching);
 assert(vh_start(&c,"B",false));vh_finish(&c,true);assert(c.active==3);
 assert(vh_remove(&c,"A") && c.preferred==-1 && c.active==3);
 assert(vh_remove(&c,"B") && c.active==-1 && c.target==-1); // no implicit fallback
 c.hosts[3].present=true;strcpy(c.hosts[3].id,"C");
 assert(!vh_start(&c,"B",false)); // stale identity cannot target reused slot
 assert(vh_start(&c,"C",false));vh_finish(&c,true);assert(c.active==3);
 puts("voice host identities, rollback, removal, busy isolation: PASS");
}
