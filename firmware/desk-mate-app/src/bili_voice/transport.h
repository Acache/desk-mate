#ifndef DESKMATE_VOICE_TRANSPORT_H
#define DESKMATE_VOICE_TRANSPORT_H

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "pcm_ring.h"
#include "voice_contracts.h"

bool vc_transport_begin(QueueHandle_t action_queue, QueueHandle_t event_queue, pcm_ring_t *ring);
bool vc_transport_connected();
bool vc_transport_ready();
bool vc_transport_pending();
void vc_transport_ack_unknown();
uint32_t vc_transport_generation();
const char *vc_transport_status();
const char *vc_transport_stage();

#endif
