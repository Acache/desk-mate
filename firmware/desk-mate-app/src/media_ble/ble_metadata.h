#ifndef DESKMATE_BLE_METADATA_H
#define DESKMATE_BLE_METADATA_H
#include "ble_contracts.h"
#include <stddef.h>
#include <string.h>
typedef struct {
    uint32_t version;
    uint8_t enabled, automatic;
    char last[18]; uint8_t last_type;
    BleHost hosts[BLE_HOST_LIMIT];
} BleStored;
/* Persistent v1/v2 layouts are frozen at the original three slots. */
typedef struct {
    uint32_t version;
    uint8_t enabled, automatic;
    char last[18]; uint8_t last_type;
    BleHost hosts[3];
} BleStoredV2;
typedef struct { char address[18]; uint8_t type; char name[BLE_NAME_BYTES]; } BleOldHost;
typedef struct {
    uint32_t version;
    uint8_t enabled, automatic;
    char last[18]; uint8_t last_type;
    BleOldHost hosts[3];
} BleStoredV1;
static inline bool ble_metadata_decode(const void *bytes,size_t length,BleStored *out) {
    memset(out,0,sizeof(*out));
    if(length<sizeof(uint32_t)) return false;
    uint32_t version; memcpy(&version,bytes,sizeof(version));
    if(version==3&&length==sizeof(*out)) memcpy(out,bytes,sizeof(*out));
    else if(version==2&&length==sizeof(BleStoredV2)) {
        BleStoredV2 old; memcpy(&old,bytes,sizeof(old));
        out->version=3; out->enabled=old.enabled; out->automatic=old.automatic;
        memcpy(out->last,old.last,sizeof(out->last)); out->last_type=old.last_type;
        memcpy(out->hosts,old.hosts,sizeof(old.hosts));
    }
    else if(version==1&&length==sizeof(BleStoredV1)) {
        BleStoredV1 old; memcpy(&old,bytes,sizeof(old));
        out->version=3; out->enabled=old.enabled; out->automatic=old.automatic;
        memcpy(out->last,old.last,sizeof(out->last)); out->last_type=old.last_type;
        for(int i=0;i<3;i++) {
            old.hosts[i].name[BLE_NAME_BYTES-1]=0;
            memcpy(out->hosts[i].address,old.hosts[i].address,sizeof(out->hosts[i].address));
            out->hosts[i].type=old.hosts[i].type;
            memcpy(out->hosts[i].name,old.hosts[i].name,BLE_NAME_BYTES);
            out->hosts[i].name_kind=(!strcmp(old.hosts[i].name,"Host 1")||!strcmp(old.hosts[i].name,"Host 2")||!strcmp(old.hosts[i].name,"Host 3"))?BLE_NAME_DEFAULT:BLE_NAME_USER;
        }
    } else return false;
    out->last[17]=0; out->last_type&=1;
    for(int i=0;i<BLE_HOST_LIMIT;i++) {
        out->hosts[i].address[17]=0; out->hosts[i].name[BLE_NAME_BYTES-1]=0;
        out->hosts[i].type&=1;
        if(out->hosts[i].name_kind>BLE_NAME_USER) out->hosts[i].name_kind=BLE_NAME_DEFAULT;
    }
    return true;
}
#endif
