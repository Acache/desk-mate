#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <string>
using String=std::string;
using portMUX_TYPE=int;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x) ((void)(x))
