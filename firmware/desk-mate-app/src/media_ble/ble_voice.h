#pragma once
#include "voice_wire.h"
#include "ble_contracts.h"
class NimBLEServer;
struct BvMessage {uint16_t length;uint8_t kind;uint32_t epoch;uint8_t bytes[BV_MAX+1];};
struct BvLink {bool ready;uint32_t epoch,generation;uint16_t mtu;char address[18];uint8_t type;};
bool ble_voice_begin(NimBLEServer*);
void ble_voice_poll(uint16_t,uint32_t,bool);
void ble_voice_link(BvLink*);
bool ble_voice_receive(BvMessage*);
bool ble_voice_send(uint8_t,const uint8_t*,size_t);
bool ble_voice_writable();
void ble_voice_reset();

void ble_voice_enable(bool);
void ble_voice_audio(bool);
