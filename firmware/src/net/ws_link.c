/* GPL-3.0-or-later. */
#include <errno.h>
#include <inttypes.h>
#include <lwip/netdb.h>
#include <netinet/in.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <unistd.h>

#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "net_prefs.h"
#include "softap.h"
#include "ws_codec.h"
#include "ws_link.h"
#ifdef RX_WIFI_RELAY
#include "status_led.h"
#endif

static const char *TAG = "ws_link";

#define RX_RING_BYTES 1024 /* the inbound side carries ASCII commands only */
#define RX_MAX_ITEM 128    /* one frame is one command line: commands.c's line
                            * buffer holds 128, and its read buffer is the same
                            * size, so nothing handed over is ever truncated */
#define BACKOFF_MIN_MS 500
#define BACKOFF_MAX_MS 30000
#define RCV_TIMEOUT_MS 200 /* how often the serve loop re-checks the down flag */
#define HANDSHAKE_BUDGET_MS 4000

/* The socket has two tasks on it: the dial task reads and tears down,
 * link.c's writer task sends. One mutex around every send, close and reopen
 * is what keeps a stale fd number out of the writer's hands - after a close,
 * lwIP hands the same small number to the next socket, and a frame would then
 * land inside somebody else's handshake. */
static int sock = -1;
static SemaphoreHandle_t sock_mu;
static RingbufHandle_t rx_ring;
static TaskHandle_t dial_task;
static net_prefs_t cfg;
bool netif_built;   /* non-static — softap.c tears down/rebuilds */
/* esp_wifi_init ran. Separate from netif_built because the netif is created
 * first and the driver init can still fail: one flag for both meant a boot
 * failure at esp_wifi_init left netif_built false, and the operator's `NET ON`
 * then created the STA netif a second time - which asserts in wifi_default.c
 * and reboots the relay (measured 2026-11-02). */
bool wifi_inited;   /* non-static — softap.c deinits */
/* join_ap() brought the driver up (mode, start, power save). Re-arming - NET
 * ON again, or with new credentials - must not run that a second time:
 * esp_wifi_start and esp_wifi_connect both answer an error when the station is
 * already where we want it, and a relay that reported ERR net_start at that
 * moment would be lying about a working link. */
bool driver_up;
static uint8_t out_frame[WS_MAX_FRAME + 16];
static volatile bool sta_up;
static volatile bool ws_up;
static volatile bool want_up;
static volatile uint32_t rx_drops;
static uint32_t backoff_ms = BACKOFF_MIN_MS;
static char why[56]; /* the last thing that happened, for NET STATUS */
/* The driver's own words about why the station is not up. M1's last run left
 * `sta:false` with `why:""` for minutes: on_wifi_event took `void *data` and
 * threw the disconnect reason away, and the dial task never wrote `why` while
 * it waited for an IP. Two counters, not one string, because the reason code is
 * the driver's and `why` is ours - overwriting one with the other loses both. */
static volatile uint8_t sta_reason;
static volatile uint32_t sta_downs;

static void set_why(const char *text) {
    strlcpy(why, text, sizeof(why));
}

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = (const wifi_event_sta_disconnected_t *)data;
        printf("[EVT] STA_DISCONNECTED reason=%u want_up=%d sta_downs=%lu\n",
               d ? (unsigned)d->reason : 0, (int)want_up, (unsigned long)sta_downs + 1);
        /* Only a link we still wanted is a failure. NET OFF clears want_up
         * before it disconnects, and this event follows that call: counting it
         * would leave the relay reporting a reason code for the thing the
         * operator just asked for. */
        if (want_up) {
            sta_reason = d ? d->reason : 0;
            sta_downs++;
#ifdef RX_WIFI_RELAY
            printf("[LED] SL_STA_CONNECTING (amber slow blink)\n");
            status_led_set_state(SL_STA_CONNECTING);
#endif
        }
        sta_up = false;
        /* Reconnection is the driver's own job: a second retry loop here would
         * keep interrupting it. want_up only gates it so NET OFF stays off. */
        if (want_up) esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        printf("[EVT] STA_GOT_IP\n");
        sta_up = true;
        sta_reason = 0; /* an association that worked is not a failure to explain */
#ifdef RX_WIFI_RELAY
        printf("[LED] SL_STA_GOT_IP (amber solid)\n");
        status_led_set_state(SL_STA_GOT_IP);
#endif
    } else {
        (void)data;
    }
}

/* Every exit of join_ap names the IDF call that refused, because M1 collapsed
 * all seven of them into "wifi_start" and the board then could not say which
 * step it had failed at - the one thing L1.5 asks it for. The heap figure rides
 * along because it is what separates ESP_ERR_NO_MEM from a state error without
 * needing a second build. */
static bool join_failed(char *bad, size_t badn, const char *step, esp_err_t e) {
    snprintf(bad, badn, "%s %s heap=%u/%u", step, esp_err_to_name(e),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    return false;
}

static void on_wifi_event(void *arg, esp_event_base_t eb, int32_t id, void *data);
static bool events_registered;

/* Idempotent registration of the WiFi/IP event handlers. Called from
 * ws_link_start (so the no-creds softAP path tracks events too) and from
 * join_ap. Handlers live on the default event loop and survive
 * esp_wifi_deinit/reinit, so one successful registration covers all later
 * driver cycles. */
bool ws_link_register_events(void) {
    if (events_registered) return true;
    /* Create the default loop if it doesn't exist yet; ESP_ERR_INVALID_STATE
     * just means it already exists. */
    esp_event_loop_create_default();
    esp_err_t e1 = esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL, NULL);
    esp_err_t e2 = esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_wifi_event, NULL, NULL);
    if (e1 != ESP_OK || e2 != ESP_OK) return false;
    events_registered = true;
    return true;
}

static bool join_ap(char *bad, size_t badn) {
    if (!netif_built) {
        /* lwIP's TCP/IP task comes up with esp_netif_init, not with the netif
         * itself. Without it the station associates, DHCP sends its first
         * discover, and tcpip.c:257 asserts "Invalid mbox" - a reboot loop a
         * saved NET ON walks straight back into. Idempotent, so an image that
         * already ran it is unaffected. */
        esp_err_t e = esp_netif_init();
        if (e != ESP_OK) return join_failed(bad, badn, "netif_init", e);
        if (!esp_netif_create_default_wifi_sta()) return join_failed(bad, badn, "netif_sta", ESP_FAIL);
        /* Up before anything that can still fail: from this line the netif
         * exists, and creating it twice is the assert, not a retry. */
        netif_built = true;
    }
    if (!wifi_inited) {
        /* The relay image never runs main.c's Wi-Fi bring-up, so the driver
         * init belongs here: esp_wifi_set_mode fails without it. RAM storage
         * keeps the driver from writing a second copy of our credentials. */
        const wifi_init_config_t mic = WIFI_INIT_CONFIG_DEFAULT();
        esp_err_t e = esp_wifi_init(&mic);
        if (e != ESP_OK) return join_failed(bad, badn, "wifi_init", e);
        wifi_inited = true;
    }
    /* Event handlers must be registered on EVERY path — after a softAP
     * round-trip wifi_inited is already true and this branch is skipped.
     * Without them sta_up/GOT_IP are never tracked and dial_task tears
     * down a freshly joined STA 10 s later ("STA down 10 s" → softAP). */
    if (!ws_link_register_events()) return join_failed(bad, badn, "evt_reg", ESP_FAIL);
    /* RAM, not FLASH: our copy of the credentials is namespace "net", and the
     * driver would otherwise write a second one behind our back. */
    esp_err_t e = esp_wifi_set_storage(WIFI_STORAGE_RAM);
    if (e != ESP_OK) return join_failed(bad, badn, "storage", e);
    if (!driver_up) {
        /* Bring the station mode up first — set_config refuses without a mode. */
        e = esp_wifi_set_mode(WIFI_MODE_STA);
        if (e != ESP_OK) return join_failed(bad, badn, "set_mode", e);
        e = esp_wifi_start();
        if (e != ESP_OK) return join_failed(bad, badn, "start", e);
        e = esp_wifi_set_ps(WIFI_PS_NONE);
        if (e != ESP_OK) return join_failed(bad, badn, "set_ps", e);
        driver_up = true;
    } else {
        esp_wifi_disconnect();
    }
    /* Now the driver is in STA mode and running: we can configure SSID/PASS
     * and attempt to connect. If there are no credentials, skip the connect
     * step — dial_task_fn will bring up softAP instead. */
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, cfg.ssid, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, cfg.pass, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = cfg.pass[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    e = esp_wifi_set_config(WIFI_IF_STA, &wc);
    if (e != ESP_OK) return join_failed(bad, badn, "set_config", e);
    if (!cfg.ssid[0]) {
        /* Empty SSID — don't connect. Driver is up; dial_task brings AP. */
        return true;
    }
    e = esp_wifi_connect();
    return e == ESP_OK || join_failed(bad, badn, "connect", e);
}

/* recv() with the socket's SO_RCVTIMEO: fills n bytes, or returns false if the
 * peer stalled past budget, closed or errored. Any progress resets the clock. */
static bool read_fill(int fd, uint8_t *buf, size_t n, uint32_t budget_ms) {
    size_t got = 0;
    uint32_t waited = 0;
    while (got < n) {
        ssize_t r = recv(fd, buf + got, n - got, 0);
        if (r > 0) {
            got += (size_t)r;
            waited = 0;
            continue;
        }
        if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
            waited += RCV_TIMEOUT_MS;
            if (waited > budget_ms) return false;
            continue;
        }
        return false;
    }
    return true;
}

static bool send_all(int fd, const uint8_t *buf, size_t n) {
    size_t sent = 0;
    while (sent < n) {
        ssize_t r = send(fd, buf + sent, n - sent, 0);
        if (r <= 0) {
            if (r < 0 && (errno == EINTR || errno == EAGAIN)) continue;
            return false;
        }
        sent += (size_t)r;
    }
    return true;
}

static bool dial_and_handshake(const char *host, uint16_t port, const char *path) {
    char port_text[8];
    snprintf(port_text, sizeof(port_text), "%u", port);
    struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_STREAM}, *ai = NULL;
    if (getaddrinfo(host, port_text, &hints, &ai) != 0 || !ai) {
        set_why("dns");
        return false;
    }
    int fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
    if (fd < 0) {
        freeaddrinfo(ai);
        set_why("socket");
        return false;
    }
    struct timeval tv = {.tv_sec = RCV_TIMEOUT_MS / 1000, .tv_usec = (RCV_TIMEOUT_MS % 1000) * 1000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    if (connect(fd, ai->ai_addr, ai->ai_addrlen) != 0) {
        close(fd);
        freeaddrinfo(ai);
        set_why("connect");
        return false;
    }
    freeaddrinfo(ai);

    /* serve.mjs keys the node endpoint on ?id= (web/serve.mjs:471) and the
     * setup page no longer collects one — the relay names itself after its
     * MAC, the same suffix the softAP SSID carries. A URL that already has
     * an id= keeps the operator's choice. */
    char full_path[160];
    if (!strstr(path, "id=")) {
        uint8_t mac[6];
        esp_read_mac(mac, ESP_IF_WIFI_STA);
        snprintf(full_path, sizeof(full_path), "%s%sid=widscan58-%02x%02x%02x",
                 path, strchr(path, '?') ? "&" : "?", mac[3], mac[4], mac[5]);
        path = full_path;
    }

    uint8_t raw[16];
    char key[25], want[29];
    esp_fill_random(raw, sizeof(raw));
    ws_b64(raw, sizeof(raw), key, sizeof(key));
    ws_accept_key(key, want);
    char req[512];
    int n = snprintf(req, sizeof(req),
                     "GET %s HTTP/1.1\r\nHost: %s:%u\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n",
                     path, host, port, key);
    if (n <= 0 || (size_t)n >= sizeof(req)) {
        close(fd);
        set_why("url");
        return false;
    }
    xSemaphoreTake(sock_mu, portMAX_DELAY);
    sock = fd;
    bool ok = send_all(fd, (const uint8_t *)req, (size_t)n);
    xSemaphoreGive(sock_mu);
    if (!ok) {
        close(fd);
        sock = -1;
        set_why("send");
        return false;
    }

    /* The bridge answers the handshake before it decides whether it can serve
     * us (web/serve.mjs:368-390), so a 101 alone is not a link. Without these
     * checks a bad id or a replaced node would look "connected" and the relay
     * would sit in the node list producing zero frames - the false-alive the
     * spec forbids. */
    char resp[2048];
    size_t have = 0;
    uint32_t waited = 0;
    bool done = false;
    while (have + 1 < sizeof(resp) && waited <= HANDSHAKE_BUDGET_MS) {
        ssize_t r = recv(fd, resp + have, 1, 0);
        if (r == 1) {
            have++;
            resp[have] = 0; /* keep the string bounded for the header walk */
            waited = 0;
            if (have >= 4 && !memcmp(resp + have - 4, "\r\n\r\n", 4)) {
                done = true;
                break;
            }
            continue;
        }
        if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
            waited += RCV_TIMEOUT_MS;
            continue;
        }
        break;
    }
    if (!done) {
        close(fd);
        sock = -1;
        set_why(have ? "handshake_short" : "handshake");
        return false;
    }
    if (strncmp(resp, "HTTP/1.1 101", 12) != 0 && strncmp(resp, "HTTP/1.0 101", 12) != 0) {
        close(fd);
        sock = -1;
        set_why("not_101");
        return false;
    }
    ws_fold_response(resp, have);
    char value[64];
    if (!ws_header_value(resp, have, "Upgrade", value, sizeof(value)) || strcasecmp(value, "websocket") != 0) {
        close(fd);
        sock = -1;
        set_why("no_upgrade");
        return false;
    }
    if (!ws_header_value(resp, have, "Sec-WebSocket-Accept", value, sizeof(value)) || strcmp(value, want) != 0) {
        close(fd);
        sock = -1;
        set_why("bad_accept");
        return false;
    }
    return true;
}

static void take_down(const char *reason) {
    printf("WS take_down reason='%s' ws_up=%d sock=%d\n",
           reason ? reason : "?", ws_up, sock);
    xSemaphoreTake(sock_mu, portMAX_DELAY);
    ws_up = false;
    int fd = sock;
    sock = -1;
    xSemaphoreGive(sock_mu);
    if (reason) set_why(reason);
    if (fd >= 0) close(fd);
}

/* Discard a frame we are not handing out, so the stream stays framed. */
static bool skip_payload(int fd, size_t len) {
    uint8_t scratch[128];
    while (len) {
        size_t chunk = len < sizeof(scratch) ? len : sizeof(scratch);
        if (!read_fill(fd, scratch, chunk, 2000)) return false;
        len -= chunk;
    }
    return true;
}

static void serve_until_down(void) {
    int fd = sock;
    uint8_t hdr[10];
    printf("WS serve_until_down START fd=%d\n", fd);
    for (;;) {
        if (!ws_up || !want_up) {
            /* This task owns the fd, and a stop must be the one to end the
             * session: the bridge only drops the endpoint when the TCP closes,
             * and returning without closing leaves a node that is "online" with
             * nobody behind it. */
            take_down("stopped");
            return;
        }
        size_t have = 0;
        ws_head_t h = {0};
        for (;;) {
            ssize_t r = recv(fd, hdr + have, 1, 0);
            if (r == 1) {
                have++;
                h = ws_read_head(hdr, have);
                if (h.err != WS_NEED_MORE) break;
                continue;
            }
            if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
                if (have) { /* half a header, then silence: the stream is gone */
                    take_down("peer_stall");
                    return;
                }
                h.err = WS_NEED_MORE;
                break; /* idle: the outer loop re-checks want_up */
            }
            take_down(r == 0 ? "peer_closed" : "recv");
            return;
        }
        if (h.err == WS_BAD) {
            printf("WS BAD frame: hdr=%02x %02x %02x %02x %02x %02x\n",
                   hdr[0], hdr[1], hdr[2], hdr[3], hdr[4], hdr[5]);
            take_down("peer_frame");
            return;
        }
        if (h.err == WS_NEED_MORE) continue;
        if (h.opcode == WS_OP_PING) {
            if (h.len > 125) {
                take_down("ping_oversize");
                return;
            }
            uint8_t payload[125], pong[10 + 125];
            if (!read_fill(fd, payload, h.len, 2000)) {
                take_down("peer_stall");
                return;
            }
            uint32_t mask;
            esp_fill_random(&mask, sizeof(mask));
            size_t total = ws_write_frame(pong, sizeof(pong), WS_OP_PONG, payload, h.len, mask);
            xSemaphoreTake(sock_mu, portMAX_DELAY);
            bool sent = ws_up && total && send_all(sock, pong, total);
            xSemaphoreGive(sock_mu);
            if (!sent) {
                take_down("pong_failed");
                return;
            }
            continue;
        }
        if (h.opcode == WS_OP_CLOSE) {
            /* serve.mjs puts the reason in the close payload behind a 2-byte
             * status code: 1008 "bad-id", "replaced", "busy". Keep the text. */
            uint8_t raw[40];
            size_t take = h.len < sizeof(raw) - 1 ? h.len : sizeof(raw) - 1;
            if (take && !read_fill(fd, raw, take, 2000)) {
                take_down("peer_stall");
                return;
            }
            raw[take] = 0;
            if (h.len > take && !skip_payload(fd, h.len - take)) {
                take_down("peer_stall");
                return;
            }
            take_down(take > 2 ? (const char *)raw + 2 : (const char *)raw);
            return;
        }
        if (!h.fin || h.len > RX_MAX_ITEM) {
            rx_drops++;
            set_why(h.len > RX_MAX_ITEM ? "inbound_oversize" : "inbound_fragmented");
            if (!skip_payload(fd, h.len)) {
                take_down("peer_stall");
                return;
            }
            continue;
        }
        uint8_t item[RX_MAX_ITEM];
        if (!read_fill(fd, item, h.len, 2000)) {
            printf("WS read_fill failed len=%zu\n", h.len);
            take_down("peer_stall");
            return;
        }
        printf("WS rx op=%u len=%zu payload[0]=%c ok=%d\n",
               (unsigned)h.opcode, h.len,
               item[0] >= 0x20 && item[0] < 0x7F ? item[0] : '.',
               xRingbufferSend(rx_ring, item, h.len, 0) == pdTRUE);
    }
}

/* AP fallback triggers:
 *   - No STA for AP_TRIGGER_DOWN_MS (10 s) with saved creds → softAP.
 *   - No saved creds at all → softAP right away (first boot).
 *   - STA up but WS dial fails AP_TRIGGER_DIAL_CYCLES times at max backoff
 *     → softAP (covers the "moved to new subnet" case). */
#define AP_TRIGGER_DOWN_MS   10000
#define AP_TRIGGER_DIAL_CYCLES 3   /* 3 × 30 s = 90 s of trying serve.mjs */

/* The dial task's patience counters. File scope because ws_link_start (the
 * POST handler's task, or a NET ON command) must zero them when the operator
 * re-arms with fresh credentials: without the reset, the "STA down 10 s"
 * trigger fired the moment the saved AP finished, racing softap_stop and
 * leaving the device with no AP and no STA (measured 2026-11-09). */
static volatile int sta_down_ms;      /* how long STA has been down, in ms */
static volatile int dial_fail_cycles; /* consecutive dial failures at max backoff */

/* Bring up softAP with a chip-suffixed SSID. Shared by all three fallback paths
 * (first boot, STA down too long, dial exhausted). */
static void bring_up_softap(const char *reason) {
    char chip[12];
    uint8_t mac[6];
    esp_read_mac(mac, ESP_IF_WIFI_STA);
    snprintf(chip, sizeof(chip), "%02x%02x%02x", mac[3], mac[4], mac[5]);
    printf("SOFTAP bring up reason=%s chip=%s\n", reason ? reason : "?", chip);
    ESP_LOGW(TAG, "%s — bringing up softAP", reason ? reason : "?");
    softap_start_task(chip);
}

static void dial_task_fn(void *arg) {
    (void)arg;
    char host[64], path[128];
    uint16_t port = 80;
    const char *bad = NULL;
    bool ap_was_up = false;
    printf("DIAL start want_up=%d ssid='%s' url='%s'\n", (int)want_up, cfg.ssid, cfg.url);

    for (;;) {
        while (!want_up) {
            /* No credentials at all → softAP is the default state; don't
             * wait for want_up, just drop through and start it. */
            if (!cfg.ssid[0] || !cfg.url[0]) break;
            sta_down_ms = 0;
            dial_fail_cycles = 0;
            backoff_ms = BACKOFF_MIN_MS;
            if (softap_running()) { softap_stop(); ap_was_up = false; }
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        /* ---- No credentials saved at all ----
         * First boot: nothing to try, bring up AP right away. */
        if (!cfg.ssid[0] || !cfg.url[0]) {
            if (!softap_running()) {
                printf("DIAL no creds → bring_up_softap\n");
                /* tiny delay so the STA driver has a chance to confirm there is
                 * nothing configured to associate with */
                vTaskDelay(pdMS_TO_TICKS(1500));
                if (!sta_up) bring_up_softap("no credentials saved");
            }
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        /* ---- STA cannot associate (wrong SSID, wrong password, out of range) ---- */
        if (!sta_up) {
            if (!why[0]) set_why("no_ip");
            dial_fail_cycles = 0; /* STA is not up yet, dial cannot count */
            sta_down_ms += 200;
            if (sta_down_ms >= AP_TRIGGER_DOWN_MS && !softap_running())
                bring_up_softap("STA down 10 s");
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }

        /* ---- STA has IP. Reset patience, tear AP down. ---- */
        sta_down_ms = 0;
        if (ap_was_up) {
            softap_stop();
            ap_was_up = false;
        }

        if (!ws_parse_url(cfg.url, host, sizeof(host), &port, path, sizeof(path), &bad)) {
            set_why(bad ? bad : "url");
            want_up = false;
            continue;
        }

        /* ---- Try to reach serve.mjs ---- */
#ifdef RX_WIFI_RELAY
        printf("[LED] SL_WS_CONNECTING (green fast blink)\n");
        status_led_set_state(SL_WS_CONNECTING);
#endif
        if (!dial_and_handshake(host, port, path)) {
            set_why("dial");
            if (backoff_ms >= BACKOFF_MAX_MS) {
                dial_fail_cycles++;
                if (dial_fail_cycles >= AP_TRIGGER_DIAL_CYCLES && !softap_running()) {
#ifdef RX_WIFI_RELAY
                    printf("[LED] SL_ERROR_RED (red fast blink — all paths exhausted)\n");
                    status_led_set_state(SL_ERROR_RED);
#endif
                    bring_up_softap("WS dial exhausted");
                }
            } else {
                dial_fail_cycles = 0; /* give up early — not yet "tried hard" */
            }
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            backoff_ms = backoff_ms * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : backoff_ms * 2;
            continue;
        }

        /* ---- Finally connected. ---- */
        dial_fail_cycles = 0;
        backoff_ms = BACKOFF_MIN_MS;
        ws_up = true;
#ifdef RX_WIFI_RELAY
        printf("[LED] SL_WS_OK (green solid — relay healthy)\n");
        status_led_set_state(SL_WS_OK);
#endif
        set_why("up");
        serve_until_down();
    }
}

esp_err_t ws_link_open(void) {
    if (!sock_mu) sock_mu = xSemaphoreCreateMutex();
    if (!rx_ring) rx_ring = xRingbufferCreate(RX_RING_BYTES, RINGBUF_TYPE_NOSPLIT);
    return sock_mu && rx_ring ? ESP_OK : ESP_ERR_NO_MEM;
}

bool ws_link_start(const net_prefs_t *p) {
    char bad[56];
    bad[0] = 0;
    if (!p) return false;
    printf("WS_START ssid='%s' url='%s' on=%d\n", p->ssid, p->url, (int)p->on);
    /* Same credentials AND the STA is healthy: nothing to redo. If the STA is
     * down (failed association), fall through and re-arm — a re-save with the
     * same SSID must still get a fresh patience window below, or the dial
     * task fires its stale "STA down 10 s" trigger during the save handoff. */
    if (want_up && sta_up && !memcmp(&cfg, p, sizeof(cfg))) return true;
    if (ws_link_open() != ESP_OK) return false;
    /* Track WiFi/IP events on every path — including the no-creds softAP one.
     * Without this, after a softAP round-trip (softap_start inits the driver,
     * so join_ap's init branch never runs) the handlers were never registered
     * and dial_task saw the freshly joined STA as "down" and tore it back
     * down 10 s later. */
    ws_link_register_events();
    cfg = *p;
    /* Fresh credentials get a fresh patience window: the dial task's "STA
     * down 10 s" counter may already be past the trigger from the previous
     * (failed) credentials, and firing it during the save handoff raced
     * softap_stop — AP and STA both lost (measured 2026-11-09). */
    sta_down_ms = 0;
    dial_fail_cycles = 0;
    backoff_ms = BACKOFF_MIN_MS;
    if (!dial_task && xTaskCreate(dial_task_fn, "ws_dial", 6144, NULL, 5, &dial_task) != pdPASS) return false;
    if (!cfg.ssid[0]) {
        /* No credentials: dial_task_fn will init softAP directly.
         * We deliberately skip join_ap — its STA-mode configs can break
         * softAP beaconing on ESP32-S3. */
        printf("WS_START: no creds, deferring WiFi init to dial_task\n");
        return true;
    }
    if (!join_ap(bad, sizeof(bad))) {
        set_why(bad[0] ? bad : "wifi_start");
    } else {
        want_up = true;
    }
    printf("WS_START → want_up=%d\n", (int)want_up);
    return true;
}

/* Zero the dial task's fallback patience counters. The config-portal POST
 * handler calls this BEFORE softap_stop: while the stop window is open the
 * dial task's stale "STA down 10 s" counter would otherwise fire
 * bring_up_softap mid-handoff (measured 2026-11-09). */
void ws_link_reset_patience(void) {
    sta_down_ms = 0;
    dial_fail_cycles = 0;
    backoff_ms = BACKOFF_MIN_MS;
}

bool ws_link_stop(void) {
    /* Order matters: the dial task has to close the TCP while the STA is still
     * up, or the FIN goes out over a dead interface and the bridge keeps the
     * endpoint registered forever. */
    want_up = false;
    for (int i = 0; i < 20 && ws_up; i++) vTaskDelay(pdMS_TO_TICKS(50));
    sta_up = false;
    why[0] = 0; /* NET ON re-arms from a blank, so the wait gets named again */
    esp_wifi_disconnect();
    return !ws_up;
}

bool ws_link_sta_up(void) {
    return sta_up;
}

bool ws_link_up(void) {
    return ws_up;
}

int ws_link_write(const void *data, size_t n) {
    if (!ws_up || !data) return -1;
    uint32_t mask;
    esp_fill_random(&mask, sizeof(mask));
    size_t total = ws_write_frame(out_frame, sizeof(out_frame), WS_OP_BIN, data, n, mask);
    if (!total) return -1;
    int rc = -1;
    xSemaphoreTake(sock_mu, portMAX_DELAY);
    if (ws_up && sock >= 0 && send_all(sock, out_frame, total)) rc = (int)n;
    else ws_up = false; /* broken session; the dial task re-dials */
    xSemaphoreGive(sock_mu);
    return rc;
}

int ws_link_read(uint8_t *buf, size_t cap, uint32_t timeout_ms) {
    if (!rx_ring || !buf || !cap) return 0;
    size_t size = 0;
    TickType_t wait = timeout_ms == UINT32_MAX ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms);
    uint8_t *item = xRingbufferReceive(rx_ring, &size, wait);
    if (!item) return 0;
    /* One item is one whole frame, so a cap below its size would drop the
     * tail - the caller's buffer is RX_MAX_ITEM for exactly that reason. The
     * clamp stays as the guard it is, counted rather than silent. */
    size_t copy = size < cap ? size : cap;
    memcpy(buf, item, copy);
    vRingbufferReturnItem(rx_ring, item);
    if (copy < size) rx_drops++;
    return (int)copy;
}

uint32_t ws_link_drops(void) {
    return rx_drops;
}

void ws_link_status(char *out, size_t cap) {
    wifi_ap_record_t ap;
    int rssi = esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
    /* The address the station holds and what its DHCP client claims to be doing.
     * "no_ip" alone could not tell a lease that never arrived from one that
     * arrived and never reached the dial task - and on 2026-11-02 the relay was
     * associated and answering ARP while it reported exactly that. */
    char ip[16] = "none";
    const char *dhcp = "none";
    esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ii = {0};
    if (sta && esp_netif_get_ip_info(sta, &ii) == ESP_OK && ii.ip.addr)
        snprintf(ip, sizeof(ip), IPSTR, IP2STR(&ii.ip));
    esp_netif_dhcp_status_t ds = ESP_NETIF_DHCP_STOPPED;
    if (sta && esp_netif_dhcpc_get_status(sta, &ds) == ESP_OK)
        dhcp = ds == ESP_NETIF_DHCP_STARTED ? "started" : ds == ESP_NETIF_DHCP_INIT ? "init" : "stopped";
    /* A JSON fragment, not a kv line: NET STATUS hands this straight to the
     * platform, and every field there is a field. why is our own literal. */
    snprintf(out, cap, "\"sta\":%s,\"ws\":%s,\"rssi\":%d,\"backoff_ms\":%" PRIu32
                    ",\"in_drops\":%" PRIu32 ",\"why\":\"%s\",\"sta_reason\":%" PRIu8
                    ",\"sta_downs\":%" PRIu32 ",\"ip4\":\"%s\",\"dhcp\":\"%s\"",
             sta_up ? "true" : "false", ws_up ? "true" : "false", rssi, backoff_ms, rx_drops, why,
             sta_reason, sta_downs, ip, dhcp);
}
