/* AP mode fallback for the relay: if STA cannot join the saved Wi-Fi, the
 * relay brings up a softAP with a tiny HTTP server so the operator can
 * browser-connect and reconfigure credentials. The AP auto-exits once STA
 * succeeds. GPL-3.0-or-later. */
#include <inttypes.h>
#include <lwip/netdb.h>
#include <netinet/in.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include <errno.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "net_prefs.h"
#include "ws_link.h"
#ifdef RX_WIFI_RELAY
#include "status_led.h"   /* relay 状态机后端 */
#endif

static const char *TAG = "softap";
static TaskHandle_t softap_task;
static TaskHandle_t dns_task_h;
static volatile bool ap_up;
static volatile bool dns_up;
static uint16_t ap_port = 80; /* softAP HTTP */
/* Serializes softap_start vs softap_stop: the POST handler's stop and the
 * dial task's "STA down 10 s" re-trigger ran interleaved once (2026-11-09) —
 * the stop's tail killed the AP the start had just raised and left ap_up
 * stuck true, so the AP never came back until power cycle. */
static SemaphoreHandle_t ap_mu;

static void ap_lock(void) {
    if (!ap_mu) ap_mu = xSemaphoreCreateMutex();
    xSemaphoreTake(ap_mu, portMAX_DELAY);
}

static void ap_unlock(void) {
    if (ap_mu) xSemaphoreGive(ap_mu);
}
/* True once softap_start has done its own full WiFi init (so join_ap
 * does not re-init or interfere when we later switch back to STA). */
static bool wifi_init_done_by_softap;

/* ---- DNS captive-portal server ----
 *
 * The softAP DHCP server hands out 192.168.4.1 as the DNS server (via
 * CONFIG_LWIP_DHCPS_ADD_DNS=y at build time), so every DNS query from a
 * connected phone arrives at our UDP port 53. We answer every A-record
 * query with 192.168.4.1 — iOS/Android then resolve
 * captive.apple.com / connectivitycheck.gstatic.com etc. to us, hit our
 * HTTP server, see our config page instead of the expected "Success" or
 * 204, and pop the captive-portal browser. */
#define DNS_PORT 53
#define DNS_AP_IP_ADDR 0x0104A8C0UL /* 192.168.4.1, little-endian */

/* Extract the queried domain name from a DNS question section. */
static int dns_qname(const uint8_t *pkt, int len, int qoff, char *out, int outcap) {
    int o = 0, p = qoff;
    while (p < len && pkt[p]) {
        int lab = pkt[p];
        if (o + lab + 1 >= outcap) return -1;
        if (lab & 0xC0) { /* compression pointer */
            int ptr = ((lab & 0x3F) << 8) | pkt[p + 1];
            if (ptr >= qoff) return -1; /* safety */
            return dns_qname(pkt, len, ptr, out + o, outcap - o);
        }
        memcpy(out + o, pkt + p + 1, lab);
        o += lab;
        out[o++] = '.';
        p += lab + 1;
    }
    if (o > 0) out[o - 1] = 0; /* trim trailing dot */
    else out[0] = 0;
    return o;
}

static void dns_reply(uint8_t *buf, int n, struct sockaddr_in *from, socklen_t fromlen, int dns) {
    if (n < 12) return;
    uint16_t flags = (buf[2] << 8) | buf[3];
    if (!(flags & 0x0100)) return; /* not a standard query */
    int qd = (buf[4] << 8) | buf[5];
    if (qd != 1) return;
    int p = 12;
    char qname[80] = {0};
    dns_qname(buf, n, 12, qname, sizeof(qname));
    while (p < n && buf[p]) {
        int lab = buf[p];
        if (p + lab + 1 >= n) return;
        p += lab + 1;
    }
    p += 5;
    if (p > n) return;
    /* QNAME ends with null byte at buf[p-5]; QTYPE starts at buf[p-4] (MSB),
     * buf[p-3] (LSB); QCLASS at buf[p-2] MSB, buf[p-1] LSB. */
    uint16_t qtype = (buf[p - 4] << 8) | buf[p - 3];
    uint16_t qclass = (buf[p - 2] << 8) | buf[p - 1];
    printf("SOFTAP_DNS: q='%s' type=%d class=%d\n", qname, qtype, qclass);

    /* Build response in-place. */
    buf[2] = 0x81; buf[3] = 0x80; /* QR=1, RD=1, RA=1 */
    buf[6] = 0; buf[7] = 1;       /* ANCOUNT = 1 */
    buf[8] = buf[9] = buf[10] = buf[11] = 0;

    /* Answer section — pointer to qname, type A, class IN, TTL 60s, rdlength 4. */
    int ans = p;
    buf[ans++] = 0xC0; buf[ans++] = 0x0C;
    buf[ans++] = 0; buf[ans++] = 1;   /* type A */
    buf[ans++] = 0; buf[ans++] = 1;   /* class IN */
    buf[ans++] = 0; buf[ans++] = 0; buf[ans++] = 0; buf[ans++] = 60;
    buf[ans++] = 0; buf[ans++] = 4;
    if (qtype == 1 && qclass == 1) {
        uint32_t ip = DNS_AP_IP_ADDR;
        buf[ans++] = ip & 0xff; buf[ans++] = (ip >> 8) & 0xff;
        buf[ans++] = (ip >> 16) & 0xff; buf[ans++] = (ip >> 24) & 0xff;
        printf("SOFTAP_DNS: → 192.168.4.1\n");
    } else {
        /* Non-A query: empty answer, set RCODE=3 (NXDOMAIN) */
        buf[3] = (buf[3] & 0xF0) | 3;
        ans = p; /* truncate answer section */
    }
    sendto(dns, buf, ans, 0, (struct sockaddr *)from, fromlen);
}

static void softap_dns_task(void *arg) {
    (void)arg;
    printf("SOFTAP_DNS: task starting\n");
    int dns = socket(AF_INET, SOCK_DGRAM, 0);
    if (dns < 0) { printf("SOFTAP_DNS: socket failed\n"); vTaskDelete(NULL); return; }
    int opt = 1;
    setsockopt(dns, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct timeval tv = {.tv_sec = 2};
    setsockopt(dns, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(DNS_PORT), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(dns, (struct sockaddr *)&a, sizeof(a)) != 0) {
        printf("SOFTAP_DNS: bind failed\n"); close(dns); vTaskDelete(NULL); return;
    }
    printf("SOFTAP_DNS: listening on UDP :%d\n", DNS_PORT);
    uint8_t buf[512];
    struct sockaddr_in from;
    socklen_t fromlen;
    dns_up = true;
    while (dns_up) {
        fromlen = sizeof(from);
        int n = recvfrom(dns, buf, sizeof(buf) - 1, 0, (struct sockaddr *)&from, &fromlen);
        if (n > 0) dns_reply(buf, n, &from, fromlen, dns);
    }
    close(dns);
    printf("SOFTAP_DNS: stopped\n");
    vTaskDelete(NULL);
}

/* ---- WiFi event tracker ---- */
static void softap_wifi_event(void *arg, esp_event_base_t eb, int32_t id, void *data) {
    (void)arg; (void)data;
    if (eb == WIFI_EVENT && id == WIFI_EVENT_AP_START) {
        printf("[EVT] AP_START\n");
#ifdef RX_WIFI_RELAY
        status_led_set_state(SL_AP_SETUP);  /* red breath — portal open */
#endif
    }
    if (eb == WIFI_EVENT && id == WIFI_EVENT_AP_STOP) {
        printf("[EVT] AP_STOP\n");
#ifdef RX_WIFI_RELAY
        status_led_set_state(SL_OFF);
#endif
    }
    if (eb == WIFI_EVENT && id == WIFI_EVENT_AP_STACONNECTED) {
        wifi_event_ap_staconnected_t *s = (wifi_event_ap_staconnected_t *)data;
        printf("[EVT] STACONNECTED %02x:%02x:%02x:%02x:%02x:%02x\n",
               s->mac[0], s->mac[1], s->mac[2], s->mac[3], s->mac[4], s->mac[5]);
#ifdef RX_WIFI_RELAY
        status_led_set_state(SL_AP_CLIENT);  /* red solid — a phone is here */
#endif
    }
    if (eb == WIFI_EVENT && id == WIFI_EVENT_AP_STADISCONNECTED) {
        wifi_event_ap_stadisconnected_t *d = (wifi_event_ap_stadisconnected_t *)data;
        printf("[EVT] STADISCONNECTED reason=%d\n", d ? (int)d->reason : -1);
#ifdef RX_WIFI_RELAY
        status_led_set_state(SL_AP_SETUP);  /* no clients → back to breath */
#endif
    }
    if (eb == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED)  printf("[EVT] STA_DISCONNECTED\n");
    if (eb == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED)     printf("[EVT] STA_CONNECTED\n");
}

/* AP HTML page — SIMPLIFIED: only SSID + password + server host.
 * No URL prefix/suffix, no relay ID. Server discovers us on the LAN. */
static const char AP_HTML[] =
    "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
    "<title>Widscan58 Setup</title>"
    "<style>body{font-family:-apple-system,sans-serif;max-width:360px;margin:2em auto;padding:0 1em}"
    "h2{font-size:1.3em;color:#333}label{display:block;margin:1.2em 0 .4em;font-size:.85em;color:#666}"
    "input,select{width:100%;padding:.7em;font-size:1em;border:1px solid #ddd;border-radius:6px;box-sizing:border-box}"
    "button{background:#2563eb;color:#fff;border:0;padding:.7em 1.5em;border-radius:6px;font-size:1em;cursor:pointer;margin-top:1.5em;width:100%}"
    ".muted{color:#999;font-size:.8em;margin-top:.3em;display:block}"
    "h2 img{height:.8em;margin-right:.4em;vertical-align:middle}"
    "</style></head><body>"
    "<h2>Widscan58 Relay Setup</h2>"
    "<p class='muted'>Connected to the relay's setup Wi-Fi. Fill in your home Wi-Fi and the server address, then tap Save.</p>"
    "<label>Wi-Fi Name (SSID)</label>"
    "<select id='ssid'><option value=''>— select —</option></select>"
    "<input id='ssid_manual' placeholder='Or type Wi-Fi name here'>"
    "<label>Wi-Fi Password</label>"
    "<input id='pass' type='password' placeholder='Password'>"
    "<label>Server Address</label>"
    "<input id='host' placeholder='e.g. 192.168.1.100:8080'>"
    "<p class='muted'>Just the address — the relay auto-discovers your server.</p>"
    "<button id='save'>Save &amp; Connect</button>"
    "<div id='status' class='muted'></div>"
    "<script>\n"
    "const sSel=document.getElementById('ssid'),sMan=document.getElementById('ssid_manual'),"
    "pass=document.getElementById('pass'),host=document.getElementById('host'),"
    "st=document.getElementById('status');\n"
    "async function load(){try{const r=await fetch('/cfg');const j=await r.json();host.value=j.host||'';"
    "const s=j.aps||[];sSel.innerHTML=s.map(a=>`<option>${a}</option>`).join('');}catch(e){st.textContent='Scan failed';}};\n"
    "async function save(){\n"
    "  st.textContent='Connecting...';\n"
    "  /* urlencoded, NOT FormData: FormData sends multipart/form-data, which\n"
    "   * the relay's tiny parser cannot read. URLSearchParams also %-encodes\n"
    "   * every special character (':' becomes %3A), which the relay decodes. */\n"
    "  const body=new URLSearchParams();\n"
    "  body.append('ssid',(sMan.value||sSel.value).trim());\n"
    "  body.append('pass',pass.value);\n"
    "  body.append('host',host.value.trim());\n"
    "  try{const r=await fetch('/cfg',{method:'POST',"
    "headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()});\n"
    "  st.textContent=await r.text();}catch(e){\n"
    "  /* The relay drops its Wi-Fi right after replying, so the fetch often\n"
    "   * fails even though the save went through. */\n"
    "  st.textContent='Saved — the relay is switching to your Wi-Fi. You can close this page.';}\n"
    "};\n"
    "document.getElementById('save').onclick=save;\n"
    "load();\n"
    "</script></body></html>";

static bool ap_running(void) { return ap_up; }
bool softap_running(void) { return ap_running(); }

/* Start softAP mode with a CLEAN WiFi state.
 *
 * This is the SINGLE entry point for softAP — it handles WiFi init itself
 * because the relay image has a broken path through join_ap that leaves the
 * driver in a half-configured STA state (beaconing silently dies). We:
 *
 *   1. Deinit any previous WiFi driver (from join_ap or a prior softAP)
 *   2. Re-init the driver with AP+STA netifs
 *   3. Start in APSTA mode so the AP beacon is actually broadcast
 *
 * The operator connects to this AP, fills in Wi-Fi + server, POSTs /cfg,
 * and we stop the AP + hand control back to ws_link_start. */
bool softap_start(const char *ssid_suffix) {
    ap_lock();
    if (ap_up) { ap_unlock(); return true; }
    printf("SOFTAP: start (ssid_suffix=%s)\n", ssid_suffix ? ssid_suffix : "relay");

    /* Register WiFi event handler (once). */
    static bool evt_registered = false;
    if (!evt_registered) {
        esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, softap_wifi_event, NULL);
        evt_registered = true;
    }

    extern bool netif_built;    /* ws_link.c */
    extern bool wifi_inited;    /* ws_link.c */
    extern bool driver_up;      /* ws_link.c */

    if (!wifi_inited) {
        /* ---- PATH A: first WiFi bring-up on this boot (booted with no
         * saved creds, so join_ap never ran). Full clean init. ---- */
        printf("SOFTAP: PATH A full init\n");
        esp_netif_init();
        esp_event_loop_create_default();
        /* Create only the netifs that do not exist yet: creating a default
         * netif twice asserts in wifi_default.c. */
        if (!esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"))
            esp_netif_create_default_wifi_sta();
        if (!esp_netif_get_handle_from_ifkey("WIFI_AP_DEF"))
            esp_netif_create_default_wifi_ap();
        netif_built = true;
        {
            wifi_init_config_t wic = WIFI_INIT_CONFIG_DEFAULT();
            ESP_ERROR_CHECK(esp_wifi_init(&wic));
            wifi_inited = true;
        }
        wifi_init_done_by_softap = true;
    } else {
        /* ---- PATH B: the driver already ran (join_ap brought it up on
         * boot, or a prior softAP round). Deinit + destroy + recreate here
         * re-created the default STA netif over a stale wifi_default
         * pointer and asserted (wifi_default.c:422) in a reboot loop
         * (measured 2026-11-09). Reuse everything: quiet the STA, stop the
         * driver, and add the AP netif only if this boot never made one.
         * The mode flip happens below while the driver is stopped. ---- */
        printf("SOFTAP: PATH B reuse running driver\n");
        esp_wifi_disconnect();
        esp_wifi_stop();
        driver_up = false;
        if (!esp_netif_get_handle_from_ifkey("WIFI_AP_DEF")) {
            printf("SOFTAP: creating missing AP netif\n");
            esp_netif_create_default_wifi_ap();
        }
        netif_built = true;
    }

    /* ---- Configure + start the AP (driver is stopped in both paths) ---- */
    wifi_config_t wc = {0};
    snprintf((char *)wc.ap.ssid, sizeof(wc.ap.ssid), "Widscan58-%s", ssid_suffix ? ssid_suffix : "relay");
    wc.ap.ssid_len = strlen((char *)wc.ap.ssid);
    wc.ap.channel = 6;
    wc.ap.authmode = WIFI_AUTH_OPEN;
    wc.ap.max_connection = 4;

    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_mode(WIFI_MODE_APSTA);
    esp_wifi_set_config(WIFI_IF_AP, &wc);
    esp_err_t e = esp_wifi_start();
    printf("SOFTAP: wifi_start=%d SSID=%s\n", (int)e, (char *)wc.ap.ssid);
    if (e != ESP_OK) {
        ap_unlock();
        return false;
    }
    /* The driver is running again — join_ap() keys on this flag and would
     * otherwise call esp_wifi_start() a second time (ESP_ERR_WIFI_STATE). */
    driver_up = true;

    ap_up = true;

    /* Wait for AP interface to settle. */
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_netif_t *ap_netif = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
    if (!ap_netif) {
        printf("SOFTAP: ERROR — AP netif not found!\n");
        return false;
    }

    /* ---- CORRECT DHCP + DNS CONFIG (ESP-IDF 5.x official way) ----
     *
     * esp_netif_dhcps_option() DOES NOT accept raw IP addresses — it
     * accepts uint8_t offer-flags (OFFER_NONE/OFFER_DNS/OFFER_ROUTER/
     * OFFER_ALL). The actual DHCP-option contents are auto-populated by
     * lwIP from the netif's IP/DNS info that we set below.
     *
     * See ESP-IDF softap_sta example softap_set_dns_addr() — that's the
     * pattern this block follows. */
    esp_netif_dhcps_stop(ap_netif);

    esp_netif_ip_info_t ipinfo = {
        .ip = {.addr = 0x0104A8C0UL},      /* 192.168.4.1 */
        .netmask = {.addr = 0x00FFFFFFUL}, /* 255.255.255.0 */
        .gw = {.addr = 0x0104A8C0UL},      /* gateway = self */
    };
    esp_netif_set_ip_info(ap_netif, &ipinfo);

    /* Set the netif's DNS — DHCP server copies this into DHCP option 6. */
    esp_netif_dns_info_t dns_info = {0};
    dns_info.ip.u_addr.ip4.addr = ipinfo.ip.addr;
    dns_info.ip.type = ESP_IPADDR_TYPE_V4;
    esp_netif_set_dns_info(ap_netif, ESP_NETIF_DNS_MAIN, &dns_info);

    /* Offer-flags: OFFER_ALL = OFFER_ROUTER(0x02) | OFFER_DNS(0x02).
     * Tells lwIP DHCP server to include gateway (option 3) AND DNS (option 6)
     * in every DHCP OFFER/ACK — auto-filled from the netif's IP/DNS above. */
    uint8_t offer = 0x03; /* OFFER_ALL */
    esp_err_t dhr;
    dhr = esp_netif_dhcps_option(ap_netif, ESP_NETIF_OP_SET,
                                 ESP_NETIF_DOMAIN_NAME_SERVER,
                                 &offer, sizeof(offer));
    printf("SOFTAP: dhcps_opt(DNS_offer)=%d\n", (int)dhr);

    dhr = esp_netif_dhcps_start(ap_netif);
    printf("SOFTAP: dhcps_start=%d\n", (int)dhr);

    esp_netif_ip_info_t chk = {0};
    if (esp_netif_get_ip_info(ap_netif, &chk) == ESP_OK) {
        printf("SOFTAP: IP=%u.%u.%u.%u DHCP+DNS(captive portal) up\n",
               chk.ip.addr & 0xff, (chk.ip.addr >> 8) & 0xff,
               (chk.ip.addr >> 16) & 0xff, (chk.ip.addr >> 24) & 0xff);
    }
    ap_unlock();
    return true;
}

/* Stop the AP and hand the driver back to ws_link in a state join_ap can
 * pick up. Also stops the DNS server that the captive-portal probe relies
 * on. */
bool softap_stop(void) {
    ap_lock();
    if (!ap_up) { ap_unlock(); return true; }
    ap_up = false; /* first — observers must stop treating the AP as up */
    printf("SOFTAP: stopping AP → STA mode\n");
    /* Signal the DNS task to exit; its socket has a 2s recv timeout, so it
     * closes itself within ~2s. The task deletes ITSELF — never vTaskDelete()
     * a handle that may already have self-deleted (use-after-free). */
    dns_up = false;
    if (dns_task_h) {
        vTaskDelay(pdMS_TO_TICKS(2500)); /* wait for recv timeout + close */
        dns_task_h = NULL;
    }
    /* Crash-safe teardown (LoadProhibited, 2026-11-09): esp_wifi_set_mode
     * while a station is still associated faults. Stop the driver entirely
     * first, then flip the mode with everything down. join_ap() brings the
     * driver back — driver_up=false routes it down the full set_mode →
     * start → connect path. */
    esp_wifi_disconnect();
    esp_wifi_stop();
    esp_err_t me = esp_wifi_set_mode(WIFI_MODE_STA);
    {
        extern bool driver_up; /* ws_link.c */
        driver_up = false;
    }
    printf("SOFTAP: driver stopped, set_mode(STA)=%d\n", (int)me);
    ap_unlock();
    return true;
}

/* Scan AP list for the dropdown — run over AP's own STA interface. */
static int do_scan(char *out, size_t cap) {
    wifi_ap_record_t ap_records[20];
    uint16_t n = 20;
    /* Scanning requires we be in APSTA or STA mode — we are (APSTA). */
    if (esp_wifi_scan_start(NULL, true) != ESP_OK) return -1;
    if (esp_wifi_scan_get_ap_records(&n, ap_records) != ESP_OK) return -1;
    for (int i = 0; i < (int)n - 1; i++)
        for (int j = 0; j < (int)n - 1 - i; j++)
            if (ap_records[j].rssi < ap_records[j + 1].rssi) {
                wifi_ap_record_t t = ap_records[j]; ap_records[j] = ap_records[j + 1]; ap_records[j + 1] = t;
            }
    int off = snprintf(out, cap, "[");
    for (uint16_t i = 0; i < n && off < (int)cap - 10; i++) {
        if (i) out[off++] = ',';
        off += snprintf(out + off, cap - off, "\"%s\"", (char *)ap_records[i].ssid);
    }
    if (off < (int)cap - 2) snprintf(out + off, cap - off, "]");
    return 0;
}

static void http_reply(int fd, int status, const char *ctype, const char *body, size_t blen) {
    char head[128];
    int hlen = snprintf(head, sizeof(head),
        "HTTP/1.1 %d OK\r\nContent-Type: %s\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n",
        status, ctype ? ctype : "text/plain", blen);
    send(fd, head, hlen, 0);
    if (body && blen) send(fd, body, blen, 0);
    close(fd);
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* application/x-www-form-urlencoded decode, in place: %XX sequences and
 * '+' → space. Runs AFTER the key/value split, so an encoded '&' or '='
 * inside a value can no longer confuse the parser. Without this, a server
 * address typed as "192.168.8.156:8080" reached NVS as
 * "192.168.8.156%3A8080" and DNS resolution failed forever. */
static void url_decode(char *s) {
    char *o = s;
    for (char *p = s; *p; ) {
        if (*p == '%' && hexval(p[1]) >= 0 && hexval(p[2]) >= 0) {
            *o++ = (char)((hexval(p[1]) << 4) | hexval(p[2]));
            p += 3;
        } else if (*p == '+') {
            *o++ = ' ';
            p++;
        } else {
            *o++ = *p++;
        }
    }
    *o = 0;
}

/* Parse form body — ssid, pass, host (the three fields on the simplified page). */
static void parse_form(const char *body, size_t n,
                       char *ssid, size_t ss, char *pass, size_t ps,
                       char *host, size_t hs) {
    ssid[0] = pass[0] = host[0] = 0;
    const char *p = body, *end = body + n;
    while (p < end) {
        const char *eq = memchr(p, '=', end - p);
        if (!eq) break;
        const char *amp = memchr(eq + 1, '&', end - eq - 1);
        size_t vcap = amp ? (size_t)(amp - eq - 1) : (size_t)(end - eq - 1);
        char key[16] = {0};
        size_t klen = (size_t)(eq - p);
        if (klen >= sizeof(key)) klen = sizeof(key) - 1;
        memcpy(key, p, klen);
        char val[200] = {0};
        size_t vlen = vcap < sizeof(val) - 1 ? vcap : sizeof(val) - 1;
        memcpy(val, eq + 1, vlen);
        url_decode(val);
        if (!strcmp(key, "ssid")) strlcpy(ssid, val, ss);
        else if (!strcmp(key, "pass")) strlcpy(pass, val, ps);
        else if (!strcmp(key, "host")) strlcpy(host, val, hs);
        p = amp ? amp + 1 : end;
    }
}

/* Read the body of an HTTP request until Content-Length bytes or EOF. */
static size_t recv_body(int fd, char *buf, size_t cap, int cl) {
    size_t got = 0;
    if (cl <= 0) return 0;
    while ((int)got < cl && got + 1 < cap) {
        ssize_t r = recv(fd, buf + got, cl - got, 0);
        if (r <= 0) break;
        got += r;
    }
    buf[got] = 0;
    return got;
}

static void softap_http_task(void *arg) {
    (void)arg;
    printf("SOFTAP_HTTP: task starting stack=%u\n", (unsigned)configMINIMAL_STACK_SIZE);
    int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) { printf("SOFTAP_HTTP: socket failed\n"); vTaskDelete(NULL); return; }
    int opt = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(ap_port), .sin_addr.s_addr = htonl(INADDR_ANY)};
    /* Backlog 8: phones/macOS prefetch several parallel TCP connections
     * before sending any bytes; with the default tiny backlog the listener
     * overflows and lwIP RSTs every new client ("Connection refused"). */
    if (bind(server, (struct sockaddr *)&a, sizeof(a)) != 0 || listen(server, 8) != 0) {
        printf("SOFTAP_HTTP: bind/listen failed\n"); close(server); vTaskDelete(NULL); return;
    }
    printf("SOFTAP_HTTP: listening on :%u\n", ap_port);

    /* Buffer sizes — reduced from earlier 2KB req to avoid stack overflow. */
    char req[512];   /* HTTP headers */
    char body[512];  /* POST body — urlencoded triplets can triple a value's
                      * length (ssid 33 + pass 65 + host 200 → ~900 worst case;
                      * parse_form caps each field at 199 either way) */
    char out[512];   /* HTTP reply */

    for (;;) {
        int fd = accept(server, NULL, NULL);
        if (fd < 0) {
            printf("SOFTAP_HTTP: accept FAIL fd=%d errno=%d\n", fd, errno);
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        printf("SOFTAP_HTTP: accept fd=%d\n", fd);

        /* Read headers until \r\n\r\n or timeout. Clients (esp. phones and
         * macOS captive-portal probes) prefetch connections and send later,
         * so a long per-connection hold here starves the backlog — keep the
         * wait short and close promptly. */
        size_t have = 0;
        uint32_t waited = 0;
        while (have + 1 < sizeof(req) && waited < 1200) {
            ssize_t r = recv(fd, req + have, 1, 0);
            if (r == 1) { have++; waited = 0; if (have >= 4 && !memcmp(req + have - 4, "\r\n\r\n", 4)) break; continue; }
            vTaskDelay(pdMS_TO_TICKS(20)); waited += 20;
        }
        req[have] = 0;

        /* Parse method + path + Content-Length. */
        char method[8] = {0}, path[64] = {0};
        int cl = 0;
        if (sscanf(req, "%7s %63s", method, path) == 2) {
            /* strcasestr finds the header case-insensitively, but sscanf
             * format literals are CASE-SENSITIVE — "content-length: %d"
             * never matches "Content-Length: 51". Skip past the colon and
             * atoi the value instead. */
            const char *clh = strcasestr(req, "content-length:");
            if (clh) {
                clh += strlen("content-length:");
                while (*clh == ' ' || *clh == '\t') clh++;
                cl = atoi(clh);
            }
        }
        printf("SOFTAP_HTTP: %s %s (cl=%d)\n", method, path, cl);
        if (cl == 0 && !strcmp(method, "POST")) {
            printf("SOFTAP_HTTP: RAW req=[%s]\n", req); /* debug: why no CL? */
        }

        /* Read body. */
        recv_body(fd, body, sizeof(body), cl);

        /* ---- Dispatch ---- */
        if (!strcmp(method, "GET") && !strcmp(path, "/")) {
            http_reply(fd, 200, "text/html", AP_HTML, strlen(AP_HTML));

        } else if (!strcmp(method, "GET") && !strcmp(path, "/cfg")) {
            char aps[512] = {0};
            do_scan(aps, sizeof(aps));
            net_prefs_t prefs = {0};
            net_prefs_load(&prefs);
            /* Return host as pure host (what user typed); prefs.url may
             * internally be the full ws:// URL later but the form only
             * stores/reads the host portion. */
            snprintf(out, sizeof(out), "{\"host\":\"%s\",\"aps\":%s}",
                     prefs.url, aps);
            http_reply(fd, 200, "application/json", out, strlen(out));

        } else if (!strcmp(method, "POST") && !strcmp(path, "/cfg")) {
            char ssid[33] = {0}, pass[65] = {0}, host[200] = {0};
            parse_form(body, strlen(body), ssid, sizeof(ssid), pass, sizeof(pass), host, sizeof(host));
            if (!ssid[0] || !host[0]) {
                snprintf(out, sizeof(out), "ERR: Wi-Fi name and server address are required.");
                http_reply(fd, 400, "text/plain", out, strlen(out));
            } else {
                net_prefs_t prefs = {0};
                net_prefs_load(&prefs);
                strlcpy(prefs.ssid, ssid, sizeof(prefs.ssid));
                strlcpy(prefs.pass, pass, sizeof(prefs.pass));
                /* Store pure host — ws_link_start will internally build the
                 * ws:// URL when it dials. */
                strlcpy(prefs.url, host, sizeof(prefs.url));
                prefs.on = true;
                net_prefs_store(&prefs);
                net_prefs_set_on(true);

                /* Reply BEFORE tearing the AP down — once the AP interface is
                 * gone the response can never reach the phone and its browser
                 * hangs on "Connecting..." forever. */
                snprintf(out, sizeof(out),
                         "Saved! Connecting to %s...\nYou can close this page.",
                         ssid);
                http_reply(fd, 200, "text/plain", out, strlen(out));

                printf("SOFTAP: saved ssid=%s host=%s, restarting STA\n", ssid, host);
                /* Zero the dial task's stale fallback counters BEFORE opening
                 * the stop window — otherwise "STA down 10 s" (accumulated on
                 * the OLD credentials) fires bring_up_softap mid-handoff. */
                ws_link_reset_patience();
                softap_stop();
                ws_link_start(&prefs);
            }
        } else {
            /* Captive-portal detection paths — 302 redirect to force phone to
             * open our portal page. iOS/Android check specific URLs and pop
             * the captive portal if they get anything other than expected
             * (200 + "Success" for iOS, 204 for Android). */
            const char *loc = "Location: /\r\nConnection: close\r\n\r\n";
            if (!strncmp(path, "/library/test/", 14) ||
                !strncmp(path, "/hotspot-detect", 15) ||
                !strncmp(path, "/generate_204", 13) ||
                !strncmp(path, "/kindle-wifi/", 13) ||
                !strncmp(path, "/ncsi.txt", 9) ||
                !strncmp(path, "/test", 5) ||
                !strncmp(path, "/canonical.html", 15) ||
                !strncmp(path, "/connecttest", 12) ||
                !strncmp(path, "/fwlink/", 8)) {
                printf("SOFTAP_HTTP: → 302 captive portal redirect\n");
                const char *h302 = "HTTP/1.1 302 Found\r\n";
                send(fd, h302, strlen(h302), 0);
                send(fd, loc, strlen(loc), 0);
            } else {
                /* Everything else — e.g. user typing an arbitrary domain —
                 * serves the config page. */
                http_reply(fd, 200, "text/html", AP_HTML, strlen(AP_HTML));
            }
        }

        /* One request per connection — always close. Without this every
         * request leaks a socket fd and lwIP eventually refuses clients.
         * Drain unread bytes first: close() with pending rx data makes
         * lwIP send RST, which also discards the queued response. */
        int drain;
        do {
            drain = recv(fd, req, sizeof(req), MSG_DONTWAIT);
        } while (drain > 0);
        close(fd);
    }
}

bool softap_start_task(const char *ssid_suffix) {
    if (!softap_start(ssid_suffix)) return false;
    /* DNS server — 4KB stack enough for 512-byte UDP buffers + lwIP. */
    dns_up = false;
    if (!dns_task_h && xTaskCreate(softap_dns_task, "softap_dns", 4096, NULL, 4, &dns_task_h) != pdPASS) {
        printf("SOFTAP: DNS task create failed\n");
    } else {
        /* Wait briefly for bind before starting HTTP. */
        vTaskDelay(pdMS_TO_TICKS(200));
    }
    /* 8KB stack — task uses ~4KB of locals (req[512] + body[256] + out[512]
     * + do_scan buffer) plus lwIP/syscalls overhead. */
    if (!softap_task && xTaskCreate(softap_http_task, "softap_http", 8192, NULL, 4, &softap_task) != pdPASS) {
        softap_stop();
        return false;
    }
    return true;
}

/* ---- USB command handlers: NET SCAN, NET AP on/off ---- */
bool softap_cmd_scan(char *out, size_t cap) {
    if (!ap_up && !softap_start_task(NULL)) return false;
    return do_scan(out, cap) == 0;
}

bool softap_cmd_ap(bool enable, char *suffix) {
    if (enable) return softap_start_task(suffix ? suffix : NULL);
    softap_stop();
    return true;
}
