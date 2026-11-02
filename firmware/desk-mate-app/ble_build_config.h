#ifndef DESKMATE_BLE_BUILD_CONFIG_H
#define DESKMATE_BLE_BUILD_CONFIG_H
/* Force-included for ALL sketch/library C and C++ units via build_opt.h.
 * ESP32's prebuilt sdkconfig defines these unconditionally, overriding -D.
 * Load it first, then configure the source-built NimBLE host consistently.
 * The prebuilt controller is unchanged; the host admits only one connection.
 */
#include <sdkconfig.h>
#undef CONFIG_BT_NIMBLE_MAX_BONDS
#undef CONFIG_BT_NIMBLE_MAX_CONNECTIONS
#undef CONFIG_NIMBLE_MAX_CONNECTIONS
#undef CONFIG_BT_NIMBLE_MAX_CCCDS
#define CONFIG_BT_NIMBLE_MAX_BONDS 8
#define CONFIG_BT_NIMBLE_MAX_CONNECTIONS 1
#define CONFIG_NIMBLE_MAX_CONNECTIONS 1
#define CONFIG_BT_NIMBLE_MAX_CCCDS 40
#endif
