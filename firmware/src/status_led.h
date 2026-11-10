/* Board status LED: two backends sit on top of a shared WS2812 RMT driver.
 *
 *   Scan backend (ESP32-C5 / ESP32-S3 head) — signal-driven: steady green for
 *   the first second a channel is heard, blinking while it stays, dark 2 s
 *   after silence. Called from the IRAM scan loop via status_led_signal().
 *
 *   Relay backend (ESP32-S3 relay host) — state-machine driven: one of
 *   BOOT / AP_SETUP / STA_CONNECTING / STA_GOT_IP / WS_CONNECTING / WS_OK /
 *   ERROR_RED / OFF. Called from softap.c and ws_link.c on each status
 *   transition; never from IRAM (so it can use heap, printf etc.).
 *
 * Both live on a single WS2812 RGB LED whose GPIO is given by
 * STATUS_LED_GPIO in build_flags (C5-Zero=27, S3-Zero=21). A board without
 * STATUS_LED_GPIO compiles out to ESP_ERR_NOT_SUPPORTED; calling the init is
 * still safe — main.c already ignores its return. GPL-3.0-or-later. */
#pragma once
#include "esp_err.h"

/* -------- shared (any backend that has STATUS_LED_GPIO) -------- */

esp_err_t status_led_init(void);

/* Scan backend only: one channel heard now. Cheap, IRAM-safe, any call rate. */
void status_led_signal(void);

/* -------- relay backend only (RX_WIFI_RELAY) -------- */
#ifdef RX_WIFI_RELAY
typedef enum {
    SL_BOOT = 0,         /* off → "firmware has come up" (500 ms slow blink) */
    SL_AP_SETUP,         /* red breath: setup portal open, waiting for the operator */
    SL_AP_CLIENT,        /* red solid: a phone is connected to the setup portal */
    SL_STA_CONNECTING,   /* amber slow blink: dialing the home router */
    SL_STA_GOT_IP,       /* amber solid: DHCP lease got, next dial WS */
    SL_WS_CONNECTING,    /* green fast blink: dialing the server */
    SL_WS_OK,            /* green solid: WS handshake done, relay normal */
    SL_ERROR_RED,        /* red fast blink: any unhandled failure */
    SL_OFF               /* dark */
} status_led_state_t;

/* Non-blocking; posts the new state to the 50 ms timer and returns. */
void status_led_set_state(status_led_state_t s);
#endif
