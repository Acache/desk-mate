#include <assert.h>
#include <stdio.h>
#include "../desk-mate-app/src/media_ble/voice_wire.h"
int main(void){
 assert(bv_payload(23)==4&&bv_payload(247)==228&&bv_payload(517)==228&&bv_payload(19)==0);
 uint8_t p[244];BvAssembler a={0};
 bv_header(p,2,0x12345678,0,0,5,3);memcpy(p+16,"abc",3);
 const uint8_t golden[]={1,2,0x78,0x56,0x34,0x12,0,0,0,0,0,0,5,0,3,0,'a','b','c'};
 assert(!memcmp(p,golden,sizeof(golden)));assert(bv_append(&a,p,19,0x12345678)==0);
 bv_header(p,2,0x12345678,1,3,5,2);memcpy(p+16,"de",2);assert(bv_append(&a,p,18,0x12345678)==1);
 assert(a.next==2&&!memcmp(a.bytes,"abcde",5));assert(bv_append(&a,p,18,0x12345678)==-1);
 memset(&a,0,sizeof(a));bv_header(p,1,1,0,0,2049,1);assert(bv_append(&a,p,17,1)==-1);
 bv_header(p,1,1,0,1,2,1);assert(bv_append(&a,p,17,1)==-1);
 bv_header(p,1,1,0,0,2,1);assert(bv_append(&a,p,17,1)==0);
 bv_header(p,2,1,1,1,2,1);assert(bv_append(&a,p,17,1)==-1);
 // Sixty seconds of synthetic 20ms audio frames, including an ATT MTU
 // exchange during the first fragmented message. No radio or microphone.
 memset(&a,0,sizeof(a));uint32_t packet_seq=0;uint8_t frame[648];
 for(uint32_t f=0;f<3000;f++){
  bv_put32(frame,f);bv_put16(frame+4,640);bv_put16(frame+6,0);
  for(int i=8;i<648;i++)frame[i]=(uint8_t)(f+i*31);
  uint16_t off=0;
  while(off<648){uint16_t cap=bv_payload(packet_seq<3?23:247),n=(uint16_t)(648-off);if(n>cap)n=cap;
   bv_header(p,2,9,packet_seq++,off,648,n);memcpy(p+16,frame+off,n);
   int result=bv_append(&a,p,n+16,9);off+=n;assert(result==(off==648?1:0));
  }
  assert(!memcmp(a.bytes,frame,648));
 }
 assert(a.next==packet_seq);
 puts("voice wire vectors/bounds/epochs/60s synthetic stream passed");return 0;
}
