/* GPL-3.0-or-later. */
#include <inttypes.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "link.h"
#include "relay_pipe.h"
#include "uart_link.h"
#include "ws_link.h"

/* One chunk is what one uart_link_read returns; 1024 matches the C5's own TX
 * ring so a whole spectrum frame never needs two reads at the source, and it is
 * well inside the WS queue's largest item (32 KiB NOSPLIT ring). */
#define PIPE_CHUNK 1024
#define PIPE_POLL_MS 20

static TaskHandle_t task;
/* The pipe's own ledger: bytes that reached the uplink queue, and bytes the
 * uplink could not take. link.c counts its queue drops separately - two
 * ledgers, two owners. */
static volatile uint32_t bytes_up;
static volatile uint32_t lost_down;

static void pipe_task_fn(void *arg) {
    (void)arg;
    /* Not on the stack: this task's whole job is to move a 1 KB chunk, and the
     * relay's heap is where the RAM lives (the link queue is already 72 KiB). */
    static uint8_t chunk[PIPE_CHUNK];
    for (;;) {
        if (!uart_link_ready()) {
            /* NET UART reopened the link on another pair; the wire may also be
             * idle while nobody has armed a head at all. */
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        const int n = uart_link_read(chunk, sizeof(chunk), PIPE_POLL_MS);
        if (n <= 0) continue;
        /* Bounded by the queue link_raw_to already is: with the socket down we
         * drain the UART (so the head keeps sweeping and the gap in the
         * recording is real) and count what we could not send. Parking here
         * instead would let noise accumulate in the ring and then blast it at
         * the uplink on reconnect. */
        if (!ws_link_up()) {
            lost_down += (uint32_t)n;
            continue;
        }
        if (link_raw_to(LINK_WS, chunk, (size_t)n)) bytes_up += (uint32_t)n;
    }
}

void relay_pipe_start(void) {
    if (task) return;
    if (xTaskCreate(pipe_task_fn, "head_pipe", 4096, NULL, 5, &task) != pdPASS) task = NULL;
}

void relay_pipe_status(char *out, size_t cap) {
    int tx = -1, rx = -1;
    uart_link_pins(&tx, &rx);
    /* A JSON fragment, same shape as ws_link_status(): NET STATUS splices
     * these into one object. head_baud is here because "why is the head quiet"
     * is nearly always a baud or a pin pair, and the board is the only witness. */
    snprintf(out, cap,
             "\"head_ready\":%s,\"head_baud\":%" PRIu32 ",\"head_pins\":\"%d/%d\","
             "\"head_bytes_up\":%" PRIu32 ",\"head_lost_down\":%" PRIu32,
             uart_link_ready() ? "true" : "false", uart_link_line_rate(), tx, rx, bytes_up, lost_down);
}

bool relay_pipe_send(const char *line) {
    if (!uart_link_ready() || !line || !line[0]) return false;
    char cmd[128];
    const int n = snprintf(cmd, sizeof(cmd), "%s\n", line);
    if (n <= 0 || (size_t)n >= sizeof(cmd)) return false;
    return uart_link_write(cmd, (size_t)n) == n;
}
