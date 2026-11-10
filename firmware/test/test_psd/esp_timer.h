/* Host stand-in for esp_timer.h. psd.c only uses it for the prep/fft/power
 * stopwatch, so the absolute origin does not matter; the tests never assert on
 * those fields, only that a real FFT ran. Test scaffolding only.
 * GPL-3.0-or-later. */
#pragma once
#include <stdint.h>
#include <time.h>

static inline int64_t esp_timer_get_time(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}
