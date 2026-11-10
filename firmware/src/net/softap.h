/* SoftAP fallback for the relay node. GPL-3.0-or-later. */
#pragma once
#include <stdbool.h>

bool softap_start_task(const char *ssid_suffix); /* bring AP up + HTTP server */
bool softap_stop(void);
bool softap_running(void);
bool softap_cmd_scan(char *out, size_t cap);     /* write JSON array of SSIDs */
bool softap_cmd_ap(bool enable, char *suffix);   /* manual NET AP on/off */
