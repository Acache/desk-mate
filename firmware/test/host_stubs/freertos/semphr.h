#pragma once
using SemaphoreHandle_t=void *;
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
inline SemaphoreHandle_t xSemaphoreCreateMutex(){return (void *)1;}
inline int xSemaphoreTake(SemaphoreHandle_t,int){return 1;}
inline void xSemaphoreGive(SemaphoreHandle_t){}
