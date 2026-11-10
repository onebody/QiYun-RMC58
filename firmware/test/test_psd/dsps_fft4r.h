/* Host stand-in for esp-dsp's radix-4 header; same rule as dsps_fft2r.h: init
 * succeeds, transform aborts. Test scaffolding only. GPL-3.0-or-later. */
#pragma once
#include "dsps_fft2r.h"

static inline esp_err_t dsps_fft4r_init_fc32(float *table, int size) { return ESP_OK; }
static inline void dsps_fft4r_deinit_fc32(void) {}
static inline esp_err_t dsps_fft4r_fc32(float *x, int N) {
    PSD_HOST_NO_ESPDSP("dsps_fft4r_fc32");
    return ESP_ERR_INVALID_STATE;
}
static inline esp_err_t dsps_bit_rev4r_fc32(float *x, int N) {
    PSD_HOST_NO_ESPDSP("dsps_bit_rev4r_fc32");
    return ESP_ERR_INVALID_STATE;
}
