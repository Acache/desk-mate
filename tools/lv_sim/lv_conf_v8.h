#include "../../firmware/desk-mate-app/lv_conf.h"
/* Same widgets/fonts/color depth as hardware; extra memory only for screenshots. */
#undef LV_MEM_SIZE
#ifndef LV_SIM_POOL_BYTES
#define LV_SIM_POOL_BYTES (1024 * 1024)
#endif
#define LV_MEM_SIZE LV_SIM_POOL_BYTES
#undef LV_USE_SNAPSHOT
#define LV_USE_SNAPSHOT 1

/* Fail fast in native tests instead of the firmware's halt loop. */
#undef LV_ASSERT_HANDLER_INCLUDE
#define LV_ASSERT_HANDLER_INCLUDE <stdlib.h>
#undef LV_ASSERT_HANDLER
#define LV_ASSERT_HANDLER abort();
#undef LV_USE_LOG
#define LV_USE_LOG 1
#undef LV_LOG_LEVEL
#define LV_LOG_LEVEL LV_LOG_LEVEL_WARN
#define LV_LOG_PRINTF 1
