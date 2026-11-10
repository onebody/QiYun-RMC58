/* One serial line's worth of pacing, as pure arithmetic so the host can pin
 * it. Two numbers used to be literals chosen for 115200 (a 9000 B/s budget and
 * a 512-bin spectrum clamp); both are derived from the line rate here instead,
 * and at 115200 the derivation gives the same two numbers back. That identity
 * is the whole reason the Pi's RotorHazard path is safe: it is parameterised,
 * not changed - test/test_pace asserts it.
 * GPL-3.0-or-later. */
#pragma once
#include <stdint.h>

/* 8N1 puts 10 bits on the line per byte. */
static inline uint32_t pace_line_bytes(uint32_t baud) { return baud / 10; }

/* Frames may use 25/32 of the line: 11520 B/s * 25/32 = 9000, which is the
 * budget the F1 measurement ran with. Divide first so both anchors stay
 * exact in integers. */
static inline uint32_t pace_budget(uint32_t baud) { return pace_line_bytes(baud) / 32 * 25; }

/* A SPECTRUM frame is 6 (header) + 20 (spectrum header) + bins + 4 (crc);
 * scanner.c's own per-rotation accounting rounds that overhead to 32, so this
 * clamp uses 32 too rather than introducing a second number for the same
 * thing. 512 bins at 115200 was the measured-good clamp; the line scales
 * linearly, and the two ceilings below are what actually stops it growing. */
#define PACE_FRAME_OVERHEAD 32
#define PACE_BINS_IN_FRAME_BUF 1024 /* link.c: FRAME_UART_MAX's payload ceiling */

static inline uint16_t pace_spec_bins(uint32_t baud, uint32_t tx_ring_bytes) {
    uint32_t by_line = 512u * (baud / 115200u);
    uint32_t by_ring = tx_ring_bytes - PACE_FRAME_OVERHEAD;
    uint32_t cap = by_line < PACE_BINS_IN_FRAME_BUF ? by_line : PACE_BINS_IN_FRAME_BUF;
    if (by_ring < cap) cap = by_ring;
    return (uint16_t)cap;
}
