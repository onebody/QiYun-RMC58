/* Widscan58: ESP32 FPV channel scanner using the esp-sdr raw I/Q path.
 * GPL-3.0-or-later. Boot sequence follows esp-sdr's receivers. */
#include "driver/usb_serial_jtag.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#if CONFIG_IDF_TARGET_ESP32C5
#include "hal/uart_ll.h"
#include "soc/pcr_reg.h"
#endif
#include "commands.h"
#include "gpio_out.h"
#include "link.h"
#include "radio.h"
#include "rh.h"
#include "scanner.h"
#include "status_led.h"
#if RX_WIFI_RELAY
#include "net_prefs.h"
#include "relay_pipe.h"
#include "uart_link.h"
#include "ws_link.h"
#endif
#ifdef RX_W5500
#include "eth_w5500.h"
#include "eth_server.h"
#endif

/* Required by the stock RF test archive; no shell is exposed. */
int cmd_parse(char *cmd, char *name, int *argc, char **argv) {
    (void)cmd; (void)name; (void)argc; (void)argv;
    return -1;
}

/* After link_init, report a failed boot step over the link and stay up (an
 * abort would reboot-loop the USB device with nothing printed). */
#define BOOT_CHECK(x)                                                                                    \
    do {                                                                                                 \
        esp_err_t err_ = (x);                                                                            \
        if (err_ != ESP_OK) boot_failed(#x, err_);                                                       \
    } while (0)

static void boot_failed(const char *what, esp_err_t err) {
    /* Boot is silent (commands.c), so report only once a host speaks. */
    bool host = false;
    for (;;) {
        /* Nothing else reads the port; drain it so host writes don't stall. */
        uint8_t sink[64];
        while (usb_serial_jtag_read_bytes(sink, sizeof(sink), 0) > 0) host = true;
        if (host)
            link_printf("ERR boot %s %s heap=%u largest=%u\n", what, esp_err_to_name(err),
                        (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT),
                        (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

/* esp_wifi_start can leave a dual-band target on the 5 GHz band — the C5-Zero comes
 * up 5G-only, channel 36 — and channel 1 is not a valid primary there, so the
 * expected esp_wifi_set_channel(1, …) fails the boot. All this channel has to do is
 * be one the band the driver chose accepts; the scanned frequency comes later from
 * the PHY. Single-band targets keep channel 1. */
static esp_err_t wifi_boot_channel(void) {
    uint8_t chan = 1;
#if CONFIG_SOC_WIFI_SUPPORT_5G
    wifi_band_t band = WIFI_BAND_2G;
    if (esp_wifi_get_band(&band) == ESP_OK && band == WIFI_BAND_5G) chan = 36;
#endif
    return esp_wifi_set_channel(chan, WIFI_SECOND_CHAN_NONE);
}

void app_main(void) {
#if CONFIG_IDF_TARGET_ESP32C5
    /* C5 rev <= 1.0: startup gates UART0's clocks when it isn't the console,
     * and then a USB Serial/JTAG (RTS) reset leaves the ROM looping with USB
     * dead: no esptool auto-reset, and closing the port could wedge the board.
     * IDF only skips the gating when USB Serial/JTAG is the console
     * (IDFGH-17050, esp-idf#18089); ours is off, so undo it here. */
    uart_ll_enable_bus_clock(UART_NUM_0, true);
    uart_ll_sclk_enable(&UART0);
    /* Gate the CPU clock in WFI (the idle task's wait, e.g. the scanner's
     * rotation throttle). Its reset default keeps the clock running, and IDF
     * only clears it on older chips (rtc_init, cpu_waiti_clk_gate). Costs only
     * a JTAG debugger's memory access while the CPU waits. */
    REG_CLR_BIT(PCR_CPU_WAITI_CONF_REG, PCR_CPU_WAIT_MODE_FORCE_ON);
#endif
    esp_log_level_set("*", ESP_LOG_NONE);
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        e = nvs_flash_init();
    }
    ESP_ERROR_CHECK(e);
    gpio_out_init(); /* the saved board outputs (e.g. an antenna switch), before the radio */
    {
        esp_err_t led_err = status_led_init();
        printf("STATUS_LED init: err=%d (SL_BOOT red slow blink)\n", (int)led_err);
    }
    link_init();
    BOOT_CHECK(esp_event_loop_create_default());
#if RX_WIFI_RELAY
    /* One PHY, one master: a station must sit on its AP's channel to catch
     * beacons, and the esp-sdr path below would retune it out from under the
     * association. So a relay host never becomes a receiver - and it does not
     * even call scanner_init(), because that task's first apply_config() sets
     * filter, gain and recalibration on the radio (scanner.c:186-188). What a
     * relay forwards comes from the C5 over the UART (M2).
     * ws_link.c owns esp_wifi_init and the STA mode, so no esp_wifi_* here. */
    net_prefs_t prefs = {0};
    printf("BOOTSTEP 1 pre-prefs\n");
    if (net_prefs_load(&prefs) != ESP_OK) link_printf("ERR net_prefs\n");
    printf("BOOTSTEP 2 prefs on=%d ssid='%s' url='%s'\n", (int)prefs.on, prefs.ssid, prefs.url);
    link_printf("PROBE pre_wifi heap=%u largest=%u\n",
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    BOOT_CHECK(rh_init());
    printf("BOOTSTEP 3 rh_init done\n");
    /* The ring first: the command task reads LINK_WS from boot, whether or not
     * the uplink ever gets armed. Returns esp_err_t, so ESP_OK is the success -
     * `if (!...)` would report a working ring as a failed one. */
    if (ws_link_open() != ESP_OK) link_printf("ERR net_ring\n");
    /* The head link after the prefs: NET UART says which pair the wire is
     * actually on, and 0/0 keeps U0TXD/U0RXD. Failing here is not fatal - the
     * relay still uplinks its own replies, and NET STATUS shows head_ready. */
    if (!uart_link_open(prefs.tx, prefs.rx)) link_printf("ERR head_uart\n");
#if RX_WIFI_RELAY
    /* LOOPBACK self-test: if GP43(TX) shorted to GP44(RX) on this board,
     * this PING will echo back and pipe_task sees it immediately. */
    {
        const char *ping = "PING\n";
        int w = uart_link_write((const uint8_t *)ping, 5);
        printf("[PING] wrote %d bytes to UART (TX→RX short?)\n", w);
    }
#endif
    relay_pipe_start();
    commands_task_start();
    printf("BOOTSTEP 4 cmd task started\n");
    link_printf("PROBE pre_dial heap=%u largest=%u\n",
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    printf("BOOTSTEP 5 calling ws_link_start\n");
    /* Always arm the uplink — even with blank creds. ws_link_start brings up
     * the Wi-Fi driver, and dial_task_fn chooses STA (with creds) or softAP
     * (blank creds, or STA long down, or dial exhausted) on its own. */
    if (!ws_link_start(&prefs))
        link_printf("ERR net_start\n"); /* NET STATUS carries the reason */
    return;
#endif
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    BOOT_CHECK(esp_wifi_init(&cfg));
    BOOT_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    BOOT_CHECK(esp_wifi_set_mode(WIFI_MODE_NULL));
    BOOT_CHECK(esp_wifi_start());
    BOOT_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    BOOT_CHECK(esp_wifi_set_promiscuous(true));
    BOOT_CHECK(wifi_boot_channel());
    BOOT_CHECK(radio_init());
    BOOT_CHECK(rh_init()); /* before the scanner can feed seats */
    BOOT_CHECK(scanner_init());
#ifdef RX_W5500
    /* W5500 SPI Ethernet — optional: hardware may be absent, so a failed
     * bring-up only logs and the firmware keeps scanning. lwIP is not needed
     * here: we use W5500's built-in TCP/IP stack directly (see eth_w5500.c).
     * NOTE: order matters — link_eth_start() must come AFTER scanner_init()
     * because it allocates an 8 KB heap queue, and scanner_init() also needs
     * ~20 KB for its IQ buffer. link_init() deliberately no longer creates
     * the ETH queue early; see link.c:link_eth_start(). */
    {
        esp_err_t e = eth_init();
        if (e == ESP_OK) {
            link_eth_start();
            eth_listen();
            eth_server_start();
            link_printf("ETH OK W5500 TCP %d\n", ETH_TCP_PORT);
        } else {
            link_printf("ETH ERR eth_init=%s\n", esp_err_to_name(e));
        }
    }
#endif
    commands_task_start(); /* silent from here on until a host speaks (commands.c) */
}
