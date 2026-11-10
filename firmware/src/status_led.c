/* Board status LED: shared WS2812 RMT driver + two backends. See the header
 * for the contract. GPL-3.0-or-later. */
#include "status_led.h"
#include "sdkconfig.h"

#if defined(STATUS_LED_GPIO)
/* ========================================================================
 * Shared WS2812 RMT driver. C5-Zero (GP27) and S3-Zero (GP21) boards both
 * carry a single WS2812; any future S3 variant with one wired elsewhere just
 * adds a different STATUS_LED_GPIO value in its build_flags.
 *
 * A frame is 24 RMT symbols, 1 bit each (high 0.4/0.8 us, low for the rest
 * of 1.2 us at 10 MHz). The peripheral keeps the timing — bit-banging would
 * hold interrupts off for 30 us in the middle of the scan loop.
 * ======================================================================== */
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include "driver/gpio.h"
#include "driver/rmt_tx.h"
#include "esp_heap_caps.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/gpio_ll.h"
#include "soc/gpio_struct.h"
#include "soc/soc_caps.h"

#define LED_RES_HZ 10000000 /* 0.1 us ticks */
#define LED_TICKS 12        /* 1.2 us per bit */
#define LED_T0H 4           /* 0.4 us, a '0' */
#define LED_T1H 8           /* 0.8 us, a '1' */
#define LED_BITS 24         /* one WS2812 frame = 24 RMT symbol words */
#define POLL_MS 50          /* both backends' timer tick — cheap enough */

typedef struct { uint8_t r, g, b; } color_t;

static rmt_channel_handle_t chan;
static rmt_encoder_handle_t enc;
static rmt_symbol_word_t *frame;
static bool ws2812_ready;

/* Drive the line only while lit: high-Z with pull-down otherwise. Keeps the
 * WS2812's black-frame latched (and a plain LED on the same pin off) without
 * depending on the RMT idle level. The LL output-enable writes only leave
 * the RMT route in the GPIO matrix alone — gpio_set_direction() would swap
 * the pin back to plain GPIO. */
static void ws2812_oe(bool on) {
    if (on) {
        gpio_set_pull_mode(STATUS_LED_GPIO, GPIO_FLOATING);
        gpio_ll_output_enable(&GPIO, STATUS_LED_GPIO);
    } else {
        gpio_ll_output_disable(&GPIO, STATUS_LED_GPIO);
        gpio_set_pull_mode(STATUS_LED_GPIO, GPIO_PULLDOWN_ONLY);
    }
}

/* Send one WS2812 frame (GRB order). Latch parameter kept for API
 * symmetry but now does nothing — we never tri-state the GPIO. RMT stays
 * driving the pin continuously; frame-to-frame gaps (50 ms in the task,
 * 800 ms in selftest) give a ~millisecond idle low that is plenty for
 * the WS2812's 50 us latch requirement.
 *
 * WHY NO oe() CONTROL?
 * rmt_transmit() with trans_queue_depth=2 only waits for the PREVIOUS
 * frame to finish before enqueueing the current one — it does NOT wait
 * for the current frame to finish transmitting. So calling oe(false)
 * immediately after rmt_transmit() (even with a 100 us delay) can yank
 * the GPIO low while the frame is still mid-transit on the wire, which
 * the WS2812 interprets as a reset and keeps whatever color it latched
 * earlier (in our case, the red from init). Letting RMT run freely avoids
 * this race entirely. */
static void ws2812_send(color_t c, bool latch) {
    (void)latch; /* no-op — see comment above */
    if (!ws2812_ready) return;
    const uint32_t grb = ((uint32_t)c.g << 16) | ((uint32_t)c.r << 8) | (uint32_t)c.b;
    for (unsigned b = 0; b < LED_BITS; b++) {
        const bool one = (grb >> (LED_BITS - 1 - b)) & 1;
        frame[b].level0 = 1;
        frame[b].duration0 = one ? LED_T1H : LED_T0H;
        frame[b].level1 = 0;
        frame[b].duration1 = LED_TICKS - (one ? LED_T1H : LED_T0H);
    }
    ws2812_oe(true);
    rmt_transmit(chan, enc, frame, LED_BITS * sizeof(frame[0]),
                 &(rmt_transmit_config_t){.loop_count = 0});
    /* No oe(false)! RMT keeps driving. Next call will either be 50 ms from
     * now (relay task) or 800 ms from now (selftest) — more than enough
     * idle-low time for the WS2812 to latch this frame. */
}

static esp_err_t ws2812_init(void) {
    rmt_tx_channel_config_t cc = {
        .gpio_num = STATUS_LED_GPIO,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = LED_RES_HZ,
        .mem_block_symbols = SOC_RMT_MEM_WORDS_PER_CHANNEL,
        .trans_queue_depth = 2,
    };
    esp_err_t err = rmt_new_tx_channel(&cc, &chan);
    if (err != ESP_OK) return err;
    rmt_copy_encoder_config_t ec = {0};
    if ((err = rmt_new_copy_encoder(&ec, &enc)) != ESP_OK ||
        (err = rmt_enable(chan)) != ESP_OK) goto fail;
    if (!(frame = heap_caps_malloc(LED_BITS * sizeof(*frame), MALLOC_CAP_INTERNAL))) {
        err = ESP_ERR_NO_MEM;
        goto fail;
    }
    ws2812_ready = true;
    ws2812_send((color_t){0, 0, 0}, true); /* start dark */
    return ESP_OK;
fail:
    if (frame) free(frame), frame = NULL;
    if (enc)   rmt_del_encoder(enc), enc = NULL;
    if (chan)  rmt_disable(chan), rmt_del_channel(chan), chan = NULL;
    ws2812_oe(false);
    return err;
}

/* Shared palette — both relay and scan backends need at least C_OFF.
 * MAX_LEVEL caps WS2812 steady-state brightness so it doesn't run warm.
 * Compound literals work in both initializers and assignments, unlike bare
 * brace lists which are only valid on declaration. */
#define MAX_LEVEL 96
#define PERIOD_MS 2000
#define SLOW_BLINK_MS 600
#define FAST_BLINK_MS 150
#define C_RED  ((color_t){MAX_LEVEL, 0,              0})
#define C_AMBR ((color_t){MAX_LEVEL, (MAX_LEVEL * 3)/4, 0})
#define C_GRN  ((color_t){0,         MAX_LEVEL,       0})
#define C_OFF  ((color_t){0,         0,               0})

/* ----------------------------------------------------------------
 * BACKEND 1: relay state machine (RX_WIFI_RELAY).
 *
 * Every 50 ms the timer recomputes what to show from the current state:
 *   SL_BOOT          →  slow blink     off→red
 *   SL_AP_SETUP      →  breath          red fading up/down
 *   SL_AP_CLIENT     →  solid           red
 *   SL_STA_CONNECTING→  slow blink       amber
 *   SL_STA_GOT_IP    →  solid           amber
 *   SL_WS_CONNECTING →  fast blink       green
 *   SL_WS_OK         →  solid           green
 *   SL_ERROR_RED     →  fast blink       red
 *   SL_OFF           →  off
 * ---------------------------------------------------------------- */
#ifdef RX_WIFI_RELAY

static volatile status_led_state_t cur;   /* set_state() writes, task reads */
static uint32_t boot_tick;                /* init tick count — for SL_BOOT slow blink */

static const char *state_name(status_led_state_t s) {
    switch (s) {
        case SL_BOOT: return "BOOT";
        case SL_AP_SETUP: return "AP_SETUP";
        case SL_AP_CLIENT: return "AP_CLIENT";
        case SL_STA_CONNECTING: return "STA_CONNECTING";
        case SL_STA_GOT_IP: return "STA_GOT_IP";
        case SL_WS_CONNECTING: return "WS_CONNECTING";
        case SL_WS_OK: return "WS_OK";
        case SL_ERROR_RED: return "ERROR_RED";
        case SL_OFF: return "OFF";
    }
    return "?";
}

static void relay_task(void *arg) {
    (void)arg;
    /* FreeRTOS task (NOT esp_timer): rmt_transmit() needs task context to
     * wait for the previous frame via an internal semaphore. An esp_timer
     * callback runs from a high-priority timer context where that wait
     * cannot be serviced, so frames are silently dropped.
     *
     * Stack 4096 because ESP-IDF 5.5 rmt_transmit's internal call chain
     * (copy encoder + wait) is deeper than earlier versions — empirically
     * 2048 → stack overflow, 4096 → OK. */

    static color_t last = {0, 0, 0};
    while (1) {
        boot_tick++;
        const uint32_t phase_ms = boot_tick * POLL_MS;
        const status_led_state_t s = cur;
        color_t base = C_OFF;
        uint32_t cycle_ms = PERIOD_MS;
        uint32_t on_ms = PERIOD_MS / 2;

        switch (s) {
            case SL_BOOT:          base = C_RED;    cycle_ms = SLOW_BLINK_MS * 2;  on_ms = SLOW_BLINK_MS;  break;
            case SL_AP_SETUP:      base = C_RED;    cycle_ms = PERIOD_MS;          on_ms = 0;              break;
            case SL_AP_CLIENT:     base = C_RED;    cycle_ms = PERIOD_MS;          on_ms = PERIOD_MS;      break;
            case SL_STA_CONNECTING:base = C_AMBR;   cycle_ms = SLOW_BLINK_MS * 2;  on_ms = SLOW_BLINK_MS;  break;
            case SL_STA_GOT_IP:    base = C_AMBR;   cycle_ms = PERIOD_MS;          on_ms = PERIOD_MS;      break;
            case SL_WS_CONNECTING: base = C_GRN;    cycle_ms = FAST_BLINK_MS * 2;  on_ms = FAST_BLINK_MS;  break;
            case SL_WS_OK:         base = C_GRN;    cycle_ms = PERIOD_MS;          on_ms = PERIOD_MS;      break;
            case SL_ERROR_RED:     base = C_RED;    cycle_ms = FAST_BLINK_MS * 2;  on_ms = FAST_BLINK_MS;  break;
            case SL_OFF: default:  base = C_OFF;    cycle_ms = PERIOD_MS;          on_ms = 0;              break;
        }

        color_t out = C_OFF;
        if (on_ms >= cycle_ms) {
            out = base;
        } else if (s == SL_AP_SETUP) {
            uint32_t p = phase_ms % PERIOD_MS;
            uint8_t lvl = p < (PERIOD_MS / 2)
                ? (uint8_t)((MAX_LEVEL * p) / (PERIOD_MS / 2))
                : (uint8_t)(MAX_LEVEL - (MAX_LEVEL * (p - PERIOD_MS / 2)) / (PERIOD_MS / 2));
            out = (color_t){
                (uint8_t)((base.r * lvl) / MAX_LEVEL),
                (uint8_t)((base.g * lvl) / MAX_LEVEL),
                (uint8_t)((base.b * lvl) / MAX_LEVEL),
            };
        } else {
            uint32_t p = phase_ms % cycle_ms;
            bool on = p < on_ms;
            out = on ? base : C_OFF;
        }

        if (out.r == last.r && out.g == last.g && out.b == last.b) {
            if ((boot_tick & 0x3F) == 0) {
                printf("[LED] hb state=%s out=(%d,%d,%d)\n",
                       state_name(s), out.r, out.g, out.b);
            }
            vTaskDelay(pdMS_TO_TICKS(POLL_MS));
            continue;
        }
        printf("[LED] tick %lu state=%s → out=(%d,%d,%d)\n",
               (unsigned long)boot_tick, state_name(s), out.r, out.g, out.b);
        last = out;
        ws2812_send(out, true);
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
    }
}

esp_err_t status_led_init(void) {
    esp_err_t err = ws2812_init();
    if (err != ESP_OK) return err;

    /* ---- Startup selftest: red → green → blue → black, 800 ms each.
     * This confirms the WS2812 on STATUS_LED_GPIO is physically reachable,
     * the GRB color order is right, and the RMT timing is in spec. If the
     * user sees only some of the four colors, we fix the color order or
     * timing; if all four are black, the pin is wrong or has no WS2812. */
    printf("[LED] selftest: R→G→B→black on GPIO %d\n", STATUS_LED_GPIO);
    const color_t selftest_seq[] = {
        {MAX_LEVEL, 0, 0},              /* red   — R channel only */
        {0, MAX_LEVEL, 0},              /* green — G channel only */
        {0, 0, MAX_LEVEL},              /* blue  — B channel only */
        {0, 0, 0},                      /* black */
    };
    for (int i = 0; i < 4; i++) {
        ws2812_send(selftest_seq[i], true); /* latch between colors */
        printf("[LED] selftest: step %d = (%d,%d,%d)\n",
               i, selftest_seq[i].r, selftest_seq[i].g, selftest_seq[i].b);
        esp_rom_delay_us(800000); /* 800 ms */
    }
    printf("[LED] selftest done → SL_BOOT slow blink\n");

    boot_tick = 0;
    cur = SL_BOOT;
    /* FreeRTOS task (NOT esp_timer): rmt_transmit() needs task context to
     * wait for the previous frame via an internal semaphore. An esp_timer
     * callback runs from a high-priority timer context where that wait
     * cannot be serviced, so frames are silently dropped and the WS2812
     * keeps showing whatever color was last latched.
     *
     * Stack is 4096 because rmt_transmit's internal path (copy encoder +
     * wait) uses a non-trivial amount of stack — 2048 was enough for
     * earlier versions but the current ESP-IDF 5.5 rmt_tx stack depth is
     * higher. We confirmed this empirically: 2048 → stack overflow, 4096 → ok. */
    BaseType_t rc = xTaskCreate(relay_task, "sl_led", 4096, NULL, 3, NULL);
    return rc == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

void status_led_signal(void) { /* no-op in relay backend */ }

void status_led_set_state(status_led_state_t s) {
    cur = s;
}

#else  /* !RX_WIFI_RELAY — scan backend or nothing */

/* ----------------------------------------------------------------
 * BACKEND 2: scan signal-driven (C5 head + S3 head firmware).
 *
 * Solid green for the first second a channel is heard, blinking while it
 * stays, dark 2 s after silence. One atomic store from the IRAM scan loop
 * (heard_ms); a 50 ms timer polls it cheaply. GPIO is STATUS_LED_GPIO from
 * build_flags (C5=27, S3 head=21).
 * ---------------------------------------------------------------- */
#define LED_LEVEL 128     /* green of 255, readable in daylight */
#define SOLID_MS 1000     /* the first second of a signal stays lit */
#define HOLD_MS 2000      /* no signal this long: dark */
#define BLINK_ON_MS 100
#define BLINK_PERIOD_MS 400

static volatile uint32_t heard_ms;   /* ms since boot, wraps in 49 days */
static bool active, lit;
static uint32_t start_ms;

static void scan_task(void *arg) {
    (void)arg;
    while (1) {
        const uint32_t now = (uint32_t)(esp_timer_get_time() / 1000);
        bool want = false;
        if (now - heard_ms <= HOLD_MS) {
            if (!active) { active = true; start_ms = now; }
            const uint32_t since = now - start_ms;
            want = since < SOLID_MS || (since - SOLID_MS) % BLINK_PERIOD_MS < BLINK_ON_MS;
        } else {
            active = false;
        }
        if (want != lit) {
            lit = want;
            const color_t col = want ? (color_t){0, LED_LEVEL, 0} : C_OFF;
            ws2812_send(col, !want); /* latch when going dark */
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
    }
}

esp_err_t status_led_init(void) {
    esp_err_t err = ws2812_init();
    if (err != ESP_OK) return err;
    heard_ms = (uint32_t)(esp_timer_get_time() / 1000) - HOLD_MS - 1; /* idle at boot */
    BaseType_t rc = xTaskCreate(scan_task, "sl_led", 1024, NULL, 3, NULL);
    return rc == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

/* Called from the IRAM scan loop — no flash access, no heap, no printf.
 * A 32-bit aligned store is atomic on both targets. */
IRAM_ATTR void status_led_signal(void) {
    heard_ms = (uint32_t)(esp_timer_get_time() / 1000);
}

#endif  /* RX_WIFI_RELAY */

#else  /* !STATUS_LED_GPIO — board has no LED defined */

esp_err_t status_led_init(void) { return ESP_ERR_NOT_SUPPORTED; }
void status_led_signal(void) {}
#ifdef RX_WIFI_RELAY
void status_led_set_state(status_led_state_t s) { (void)s; }
#endif

#endif  /* STATUS_LED_GPIO */
