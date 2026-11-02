#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define BV_SERVICE "d35c1001-69b0-4b89-9a72-5fd60d200001"
#define BV_RX "d35c1002-69b0-4b89-9a72-5fd60d200001"
#define BV_TX "d35c1003-69b0-4b89-9a72-5fd60d200001"
#define BV_STATUS "d35c1004-69b0-4b89-9a72-5fd60d200001"
#define BV_MAX 2048
#define BV_HEADER 16
#define BV_WINDOW 24
static inline uint16_t bv_u16(const uint8_t *p){return p[0]|((uint16_t)p[1]<<8);}
static inline uint32_t bv_u32(const uint8_t *p){return bv_u16(p)|((uint32_t)bv_u16(p+2)<<16);}
static inline void bv_put16(uint8_t*p,uint16_t v){p[0]=v;p[1]=v>>8;}
static inline void bv_put32(uint8_t*p,uint32_t v){bv_put16(p,v);bv_put16(p+2,v>>16);}
static inline void bv_header(uint8_t*p,uint8_t kind,uint32_t epoch,uint32_t seq,uint16_t off,uint16_t total,uint16_t len){
 p[0]=1;p[1]=kind;bv_put32(p+2,epoch);bv_put32(p+6,seq);bv_put16(p+10,off);bv_put16(p+12,total);bv_put16(p+14,len);
}
static inline uint16_t bv_payload(uint16_t mtu){return mtu>19?(mtu>247?247:mtu)-19:0;}
typedef struct {uint32_t next;uint16_t used,total;uint8_t kind;uint8_t bytes[BV_MAX+1];} BvAssembler;
/* -1 malformed, 0 partial, 1 complete; old epochs are discarded by caller. */
static inline int bv_append(BvAssembler*a,const uint8_t*p,size_t n,uint32_t epoch){
 if(n<BV_HEADER||p[0]!=1||p[1]<1||p[1]>2||bv_u32(p+2)!=epoch||bv_u32(p+6)!=a->next)return -1;
 uint16_t off=bv_u16(p+10),total=bv_u16(p+12),len=bv_u16(p+14);
 if(!len||n!=BV_HEADER+len||!total||total>BV_MAX||off!=a->used||off+len>total)return -1;
 if(off&&(total!=a->total||p[1]!=a->kind))return -1;
 a->kind=p[1];a->total=total;memcpy(a->bytes+off,p+BV_HEADER,len);a->used+=len;a->next++;
 if(a->used==total){a->bytes[total]=0;a->used=0;return 1;}return 0;
}
