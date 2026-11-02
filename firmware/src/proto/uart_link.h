/* The UART0 link. Two images have one and mean different things by it: the C5
 * sends its frames *out* to whatever listens (the Pi's RotorHazard, or the S3
 * relay), the relay image reads a sweep head's bytes *in*. The esp32s3 scanner
 * image has neither and keeps the stub. GPL-3.0-or-later. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Install UART0 on this target's fixed pair (C5: GP11/GP12; relay: GPIO43/44 =
 * U0TXD/U0RXD). False where the target has no such link. */
bool uart_link_init(void);
/* True once the driver is installed: the C5 carries replies straight (no
 * queue), so this is all link_ready needs to know. */
bool uart_link_ready(void);
/* Blocks until the bytes reach the TX ring; the UART shifts them out at line
 * rate whether or not anything listens, so this cannot hang. */
int uart_link_write(const void *data, size_t n);
int uart_link_read(uint8_t *buf, size_t capacity, uint32_t timeout_ms);
/* What the line actually carries, so the pacing clamp can be derived from it
 * (line_pace.h) instead of assumed to be 115200. */
uint32_t uart_link_line_rate(void);
unsigned uart_link_tx_ring_bytes(void);
/* Relay only: (re)open the head link on a given pair; 0 keeps the compiled
 * default, and a pair already in use returns true without touching anything.
 * Safe to call from the command task: nothing else owns the driver there, and
 * the pipe reads what is in the ring after a delete-and-install anyway. */
bool uart_link_open(int tx, int rx);
void uart_link_pins(int *tx, int *rx);
