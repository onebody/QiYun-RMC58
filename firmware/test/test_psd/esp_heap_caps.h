/* Host stand-in for esp_heap_caps.h. The capability bits describe which of the
 * ESP's memories a block came from; on a host there is only one, so they are
 * ignored and every allocation goes through malloc. psd.c's aligned request is
 * what matters: it must stay aligned or the float FFT would read misaligned.
 * Test scaffolding only. GPL-3.0-or-later. */
#pragma once
#include <stdint.h>
#include <stdlib.h>

#define MALLOC_CAP_INTERNAL 0x00000100u
#define MALLOC_CAP_8BIT 0x00000008u

static inline void *heap_caps_malloc(size_t size, uint32_t caps) {
    (void)caps;
    return malloc(size);
}

static inline void *heap_caps_aligned_alloc(size_t align, size_t size, uint32_t caps) {
    (void)caps;
    void *p = NULL;
    /* posix_memalign wants align a power of two and a multiple of sizeof(void *). */
    if (align >= sizeof(void *) && (align & (align - 1)) == 0 && posix_memalign(&p, align, size) != 0) return NULL;
    return p;
}

static inline void heap_caps_free(void *p) { free(p); }
