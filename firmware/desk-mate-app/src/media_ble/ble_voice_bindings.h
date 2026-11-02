#pragma once
#include <stdint.h>
bool bv_binding_get(const char*,uint8_t,char out[37]);
bool bv_binding_set(const char*,uint8_t,const char*);
void bv_binding_forget(const char*,uint8_t);
void bv_binding_remove_server(const char*);
