/* W5500 TCP server — one-client, wraps eth_w5500 driver. GPL-3.0-or-later. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Start the accept-loop task. */
void eth_server_start(void);

/* Non-blocking: is a client connected right now? */
bool eth_server_ready(void);

/* Read up to cap bytes, blocking up to timeout_ms (0 = non-blocking).
 * Returns count (0 = disconnect/timeout). */
int eth_server_read(uint8_t *buf, size_t cap, uint32_t timeout_ms);

/* Send; returns false on disconnect. */
bool eth_server_write(const void *data, size_t n);

/* Drop counter. */
uint32_t eth_server_drops(void);

/* ---- Connection log (last ETH_LOG_MAX connections) ----
 * Writes a human-readable dump of the recent connection history (remote IP:port,
 * connect/disconnect times, first command bytes). Call from the command task via
 * NET CONN. Returns number of bytes written, or cap-1 on truncate. */
int eth_server_conn_log(char *out, size_t cap);
