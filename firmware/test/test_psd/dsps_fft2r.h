/* Host stand-in for esp-dsp's radix-2 header.
 *
 * The firmware's float FFT paths and PSD_FFT_S16 call into esp-dsp, which this
 * build does not link. The init calls succeed so psd_init() can run, but every
 * transform aborts: a test that strayed onto a stubbed FFT would crash loudly
 * instead of passing on garbage. Only PSD_FFT_S16R is real on the host -- its
 * kernel, fft_r4_sc16.h, is plain C in src/dsp. (dsps_fft_w_table_sc16 is
 * deliberately not declared: psd.c names it only on the S3, which this branch
 * is not.)
 *
 * Signatures follow esp-dsp 0.5.x, the version this repo vendors.
 * Test scaffolding only. GPL-3.0-or-later. */
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "esp_err.h"

#ifndef PSD_HOST_NO_ESPDSP
#define PSD_HOST_NO_ESPDSP(what)                                                             \
    do {                                                                                     \
        fprintf(stderr, "\npsd host test: %s is esp-dsp and is not linked here; only the "   \
                        "PSD_FFT_S16R path runs on the host.\n", (what));                    \
        abort();                                                                             \
    } while (0)
#endif

static inline esp_err_t dsps_fft2r_init_fc32(float *table, int size) { return ESP_OK; }
static inline void dsps_fft2r_deinit_fc32(void) {}
static inline esp_err_t dsps_fft2r_init_sc16(int16_t *table, int size) { return ESP_OK; }
static inline void dsps_fft2r_deinit_sc16(void) {}
static inline esp_err_t dsps_fft2r_fc32(float *x, int N) {
    PSD_HOST_NO_ESPDSP("dsps_fft2r_fc32");
    return ESP_ERR_INVALID_STATE;
}
static inline esp_err_t dsps_fft2r_sc16(int16_t *x, int N) {
    PSD_HOST_NO_ESPDSP("dsps_fft2r_sc16");
    return ESP_ERR_INVALID_STATE;
}
