/* W5500 TCP server task + accept loop + connection log. GPL-3.0-or-later.
 *
 * Compile with -DRX_W5500 to enable. When unset this file produces no code
 * and the linker never sees these symbols.
 *
 * Connection log: a small ring buffer of the last N connects/disconnects,
 * with remote IP:port, monotonic-timestamp, and a brief "first command"
 * hint (up to 32 chars from the first bytes the client sent). Hosts can
 * query it with the NET CONN ASCII command over USB (or over the ETH TCP
 * link itself). */
#ifdef RX_W5500
#include "eth_server.h"
#include "eth_w5500.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static const char *TAG = "eth_server";
#define ETH_ACCEPT_TASK_STACK  4096
#define ETH_ACCEPT_TASK_PRIO   5
#define ETH_ACCEPT_POLL_MS     500

/* ------- Connection log ring buffer ------- */
#define ETH_LOG_MAX  8          /* last 8 connections */
#define ETH_LOG_CMD  16         /* first-command hint (truncated, small to keep BSS out) */

typedef struct {
    char     ip_port[16];       /* "192.168.1.50:54321" (fits 15 chars + \0) */
    int64_t  tick_connect_ms;   /* xTaskGetTickCount() at connect */
    int64_t  tick_disconnect_ms; /* 0 if still connected */
    char     first_cmd[ETH_LOG_CMD]; /* first bytes client sent, or "" */
    bool     active;           /* true while this slot is the current session */
} eth_log_entry_t;

/* s_log lives in heap, not BSS — C5 BSS is capped at 0x40820000 RF bank and
 * has only ~200 bytes margin after the ETH queue/task. Allocating here
 * means we spend ~300 bytes of heap, which is harmless because this runs
 * after scanner_init has claimed its IQ buffer. */
static eth_log_entry_t *s_log;
static volatile uint8_t s_log_write_idx;   /* next write slot */
static volatile bool    s_log_ready;

static uint32_t s_drops;

/* ------- Connection log helpers ------- */
static void log_connect(const char *ip_port) {
    eth_log_entry_t *e = &s_log[s_log_write_idx];
    memset(e, 0, sizeof(*e));
    snprintf(e->ip_port, sizeof(e->ip_port), "%s", ip_port ? ip_port : "?:0");
    e->tick_connect_ms = pdMS_TO_TICKS(xTaskGetTickCount()) / portTICK_PERIOD_MS;
    e->first_cmd[0] = '\0';
    e->active = true;
    /* Mark previous "active" slot as disconnected (disconnect may race —
     * we clear active here so the accept loop doesn't double-log). */
    for (int i = 0; i < ETH_LOG_MAX; i++) {
        if (&s_log[i] != e && s_log[i].active) {
            s_log[i].tick_disconnect_ms = pdMS_TO_TICKS(xTaskGetTickCount()) / portTICK_PERIOD_MS;
            s_log[i].active = false;
        }
    }
    s_log_write_idx = (s_log_write_idx + 1) % ETH_LOG_MAX;
}

static void log_disconnect(void) {
    for (int i = 0; i < ETH_LOG_MAX; i++) {
        if (s_log[i].active) {
            s_log[i].tick_disconnect_ms = pdMS_TO_TICKS(xTaskGetTickCount()) / portTICK_PERIOD_MS;
            s_log[i].active = false;
            break;
        }
    }
}

static void log_first_cmd(const uint8_t *data, size_t n) {
    if (!data || n == 0) return;
    /* Find the active (current) slot. */
    eth_log_entry_t *e = NULL;
    for (int i = 0; i < ETH_LOG_MAX; i++) if (s_log[i].active) { e = &s_log[i]; break; }
    if (!e || e->first_cmd[0] != '\0') return;   /* already captured */
    size_t cap = sizeof(e->first_cmd) - 1;
    size_t copy = n < cap ? n : cap;
    memcpy(e->first_cmd, data, copy);
    e->first_cmd[copy] = '\0';
    /* Replace non-printables with '?' so the NET CONN dump is ASCII-safe. */
    for (size_t k = 0; k < copy; k++)
        if (e->first_cmd[k] < 0x20 || e->first_cmd[k] > 0x7E) e->first_cmd[k] = '?';
}

/* Public read of the log. `out` gets a JSON array up to out_cap bytes
 * (truncated silently). Returns number of bytes written, or 0 on truncate.
 * Format, one entry per line (not strict JSON — convenient for a terminal):
 *
 *   ETH_CONN n=2 active=1
 *   [0] 192.168.1.50:54321 connect_ms=12345 discon_ms=12400 cmd="HELP"
 *   [1] 192.168.1.51:60000 connect_ms=11000 discon_ms=11200 cmd="PROTOCOL MONITOR"
 *
 * (n=2 means slot 2 is the one just written; oldest is at s_log_write_idx.) */
int eth_server_conn_log(char *out, size_t cap) {
    if (!out || cap == 0) return 0;
    size_t w = 0;
    uint8_t count = 0;
    /* Count used slots. */
    for (int i = 0; i < ETH_LOG_MAX; i++) if (s_log[i].ip_port[0]) count++;
    w += snprintf(out + w, cap - w, "ETH_CONN n=%u active=%d\n",
                  (unsigned)count, eth_connected() ? 1 : 0);
    /* Iterate oldest → newest. */
    for (int k = 0; k < ETH_LOG_MAX; k++) {
        int i = (s_log_write_idx + k) % ETH_LOG_MAX;
        if (s_log[i].ip_port[0] == '\0') continue;
        w += snprintf(out + w, cap - w,
                      "[%u] %s conn_ms=%" PRIi64 " %s cmd=\"%s\"\n",
                      (unsigned)k, s_log[i].ip_port,
                      s_log[i].tick_connect_ms,
                      s_log[i].active ? "ACTIVE" :
                          (s_log[i].tick_disconnect_ms ? "" : "CLOSED"),
                      s_log[i].first_cmd);
        if (w >= cap) { out[cap - 1] = '\0'; return (int)(cap - 1); }
    }
    return (int)w;
}

/* ------- Accept loop ------- */
static void accept_task(void *arg) {
    (void)arg;
    /* Accept loop: eth_listen() already put socket 0 in LISTEN. */
    for (;;) {
        if (!eth_accept(ETH_ACCEPT_POLL_MS)) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        /* Capture remote IP right after accept succeeds. */
        char remote[32] = {0};
        eth_remote(remote, sizeof(remote));
        log_connect(remote);
        ESP_LOGI(TAG, "client connected %s", remote);

        /* Poll until disconnect. peek with timeout so we don't spin. */
        while (eth_connected()) {
            uint8_t peek;
            int n = eth_recv(&peek, 1, 1000);   /* 1s timeout */
            if (n > 0) {
                /* First recv → log command hint. */
                log_first_cmd(&peek, 1);
                /* Continue draining: we only peeked; let the real reader
                 * (link_eth/commands) handle the data through the queue. */
            } else if (n < 0) {
                /* Disconnect — eth_recv returns 0 on disconnect already. */
                break;
            }
        }
        log_disconnect();
        ESP_LOGI(TAG, "client disconnected");
        eth_disconnect();
        eth_listen();
    }
}

void eth_server_start(void) {
    /* Allocate the connection log from heap — must run AFTER link_eth_start()
     * has already spent its 8 KB, but before scanner_init was even called
     * would be too early (we'd race the IQ buffer). Fortunately main.c calls
     * us after both. */
    if (!s_log) {
        size_t need = sizeof(eth_log_entry_t) * ETH_LOG_MAX;
        s_log = heap_caps_malloc(need, MALLOC_CAP_8BIT);
        if (s_log) {
            memset(s_log, 0, need);
            s_log_ready = true;
        } else {
            ESP_LOGW(TAG, "conn log alloc failed (%u B)", (unsigned)need);
        }
    }
    xTaskCreatePinnedToCore(accept_task, "eth_accept", ETH_ACCEPT_TASK_STACK, NULL,
                            ETH_ACCEPT_TASK_PRIO, NULL, portNUM_PROCESSORS - 1);
}

bool eth_server_ready(void) { return eth_connected(); }

int eth_server_read(uint8_t *buf, size_t cap, uint32_t timeout_ms) {
    int n = eth_recv(buf, cap, timeout_ms);
    if (n > 0) log_first_cmd(buf, n);
    return n;
}

bool eth_server_write(const void *data, size_t n) {
    if (!eth_connected()) { s_drops++; return false; }
    if (!eth_send(data, n)) { s_drops++; return false; }
    return true;
}

uint32_t eth_server_drops(void) { return s_drops; }
#endif /* RX_W5500 */
