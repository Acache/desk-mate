/* Included by transport.cpp: owned entirely by the voice transport task. */
#include <memory>
static uint32_t bv_epoch=0,bv_deadline=0;
static int bv_slot=-1,bv_phase=0;
static std::unique_ptr<VoiceConfig> bv_candidate;
static String bv_transcript;
static uint8_t bv_key[32];
static void bv_hex(const uint8_t*b,size_t n,char*out){for(size_t i=0;i<n;i++)snprintf(out+2*i,3,"%02x",b[i]);}
static bool bv_nonce(const char*s){if(strlen(s)!=64)return false;for(int i=0;i<64;i++)if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return false;return true;}
static String bv_proof(const char*role){
 String input=String(role)+"|"+bv_transcript;uint8_t digest[32];char text[65];
 mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256),bv_key,32,(const uint8_t*)input.c_str(),input.length(),digest);bv_hex(digest,32,text);return String(text);
}
static bool bv_equal(const char*a,const String&b){if(strlen(a)!=b.length())return false;uint8_t diff=0;for(size_t i=0;i<b.length();i++)diff|=a[i]^b[i];return diff==0;}
static void bv_clear_auth(){bv_phase=0;bv_slot=-1;bv_candidate.reset();bv_transcript="";memset(bv_key,0,sizeof(bv_key));}
static void bv_fail(const char*status){bv_clear_auth();host_disconnect();set_status(status);}
static void poll_ble_session(){
 BvLink link;ble_voice_link(&link);
 if(!link.ready||bv_epoch!=link.epoch){
  if(s_connected){s_connected=s_ready=false;s_ts.hello_done=false;post_event(VOICE_EV_TRANSPORT_LOST);}
  bv_clear_auth();bv_epoch=link.epoch;
 }
 if(!link.ready){set_status("CONNECT_BLUETOOTH");return;}
 if(s_paused){set_status("PAUSED");return;}
 if(bv_phase&&bv_phase<3&&(int32_t)(millis()-bv_deadline)>=0){bv_fail("BLE_AUTH_TIMEOUT");return;}
 // Owned only by the transport task; auth and nested reply writes must not stack 2KB buffers.
 static BvMessage message;
 for(int n=0;n<4&&ble_voice_receive(&message);n++){
  if(message.epoch!=link.epoch)continue;
  if(message.kind!=1){bv_fail("BLE_PROTOCOL_ERROR");return;}
  if(bv_phase==3&&s_connected){handle_text((const char*)message.bytes,message.length);continue;}
  JsonDocument doc;if(deserializeJson(doc,message.bytes,message.length)){bv_fail("BLE_AUTH_FAILED");return;}
  const char*type=doc["type"]|"";
  if(!strcmp(type,"ble_auth_start")&&bv_phase==0){
   const char*id=doc["server_id"]|"",*host_nonce=doc["nonce"]|"";
   int slot=vh_find(&s_hosts,id);char binding[37];
   if(slot<0||!bv_nonce(host_nonce)||(s_pending&&strcmp(s_ts.pending.server_id,id))||
      (bv_binding_get(link.address,link.type,binding)&&strcmp(binding,id))){bv_fail("BLE_PAIRING_REQUIRED");return;}
   bv_candidate.reset(new(std::nothrow)VoiceConfig{});
   if(!bv_candidate||!vc_hosts_load(slot,bv_candidate.get())){bv_fail("BLE_PAIRING_REQUIRED");return;}
   bv_slot=slot;uint8_t random[32];char nonce[65];esp_fill_random(random,32);bv_hex(random,32,nonce);
   bv_transcript=String("desk-mate-ble-v1|")+id+"|"+bv_candidate->device_id+"|"+String(link.epoch)+"|"+nonce+"|"+host_nonce;
   mbedtls_sha256((const uint8_t*)bv_candidate->token,strlen(bv_candidate->token),bv_key,0);
   JsonDocument reply;reply["type"]="ble_challenge";reply["server_id"]=id;reply["device_id"]=bv_candidate->device_id;reply["nonce"]=nonce;reply["epoch"]=link.epoch;
   if(!send_doc(reply)){bv_fail("BLE_SEND_FAILED");return;}bv_phase=1;bv_deadline=millis()+10000;
  }else if(!strcmp(type,"ble_auth_proof")&&bv_phase==1){
   if(!bv_equal(doc["proof"]|"",bv_proof("server"))){bv_fail("BLE_AUTH_FAILED");return;}
   JsonDocument reply;reply["type"]="ble_auth_proof";reply["proof"]=bv_proof("device");
   if(!send_doc(reply)){bv_fail("BLE_SEND_FAILED");return;}bv_phase=2;
  }else if(!strcmp(type,"ble_auth_complete")&&bv_phase==2){
   if(!bv_equal(doc["proof"]|"",bv_proof("complete"))||!bv_binding_set(link.address,link.type,bv_candidate->server_id)||!vc_config_activate(bv_slot)){
    bv_fail("BLE_AUTH_FAILED");return;
   }
   s_hosts.active=s_hosts.target=bv_slot;s_hosts.switching=false;s_switch_reset=true;
   bv_candidate.reset();bv_transcript="";memset(bv_key,0,32);bv_phase=3;
   xQueueReset(s_actions);pcm_ring_reset(s_ring);s_ts.command_id[0]=s_ts.sha256[0]=0;
   s_generation++;s_ts.generation=s_generation;s_ts.command_active=false;s_ts.begin_ack=false;s_ts.begin_sent=false;
   s_ts.seal_pending=s_ts.seal_sent=s_ts.commit_sent=false;s_ts.hello_done=s_ts.hello_ready=false;
   s_connected=true;s_ready=false;s_ts.last_pong_ms=s_ts.connect_ms=millis();s_ts.ping_ms=millis();
   load_feedback_nvs();send_hello(*vc_config_peek());set_status("BLE_AUTHENTICATED");
  }else{bv_fail("BLE_AUTH_FAILED");return;}
 }
 if(!s_connected&&!bv_phase)set_status("START_MAC_ASSISTANT");
}
