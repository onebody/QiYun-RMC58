/* Host stand-in for the IDF error codes psd.c uses. Values are IDF 5.5's.
 * Test scaffolding only: never compiled into a firmware image.
 * GPL-3.0-or-later. */
#pragma once
#include <stddef.h>

typedef int esp_err_t;

#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_NOT_FOUND 0x103
#define ESP_ERR_INVALID_STATE 0x104
#define ESP_ERR_INVALID_SIZE 0x105
