/* GPL-3.0-or-later. */
#include "driver/gpio.h"
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "link.h"
#include "uart_link.h"

#define UART_LINK_PORT UART_NUM_0

#if RX_TARGET_C5
#define UART_LINK_TX 11 /* the C5-Zero's U0TXD, silk "TX" */
#define UART_LINK_RX 12 /* U0RXD, silk "RX" */
#elif RX_WIFI_RELAY
#define UART_LINK_TX 43 /* ESP32-S3 U0TXD; NET UART overrides it at runtime */
#define UART_LINK_RX 44 /* U0RXD. 19/20 are this board's native USB console and
                         * 26..32 the SPI flash, so those are refused outright. */
#endif

#if RX_TARGET_C5 || RX_WIFI_RELAY
#ifndef UART_LINK_BAUD
#define UART_LINK_BAUD 115200 /* the Pi's RotorHazard line; -D raises it */
#endif
#endif

#if RX_TARGET_C5
/* The reader polls every 20 ms, so 1 KB rides out 80 ms of delay at line rate.
 * The C5's heap cannot afford more: see commands_task_start in commands.c. */
#define UART_LINK_RX_BYTES 1024
#define UART_LINK_TX_BYTES 1024
static bool installed;
/* The UART carries replies straight from the caller (the C5 has no heap for a
 * queue plus a writer task). Command and scanner replies can overlap there, so
 * serialize whole writes. */
static SemaphoreHandle_t write_lock;

bool uart_link_init(void) {
    const uart_config_t cfg = {
        .baud_rate = UART_LINK_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    write_lock = xSemaphoreCreateMutex();
    if (!write_lock) return false;
    if (uart_param_config(UART_LINK_PORT, &cfg) != ESP_OK) return false;
    if (uart_set_pin(UART_LINK_PORT, UART_LINK_TX, UART_LINK_RX, -1, -1) != ESP_OK) return false;
    if (uart_driver_install(UART_LINK_PORT, UART_LINK_RX_BYTES, UART_LINK_TX_BYTES, 0, NULL, 0) != ESP_OK)
        return false;
    /* The Pi's TX floats while it is off; without the pull-up the RX line
     * fills the buffer with noise. */
    gpio_pullup_en(UART_LINK_RX);
    installed = true;
    return true;
}

bool uart_link_ready(void) { return installed; }

int uart_link_write(const void *data, size_t n) {
    if (!installed) return -1;
    xSemaphoreTake(write_lock, portMAX_DELAY);
    int written = uart_write_bytes(UART_LINK_PORT, data, n);
    xSemaphoreGive(write_lock);
    return written;
}

int uart_link_read(uint8_t *buf, size_t capacity, uint32_t timeout_ms) {
    if (!installed) return -1;
    return uart_read_bytes(UART_LINK_PORT, buf, capacity,
                           timeout_ms == UINT32_MAX ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms));
}

uint32_t uart_link_line_rate(void) { return UART_LINK_BAUD; }
unsigned uart_link_tx_ring_bytes(void) { return UART_LINK_TX_BYTES; }

#elif RX_WIFI_RELAY
/* The relay's sweep-head link. The rings are bigger than the C5's because this
 * chip has the RAM (72 KiB link queue already proves it) and because a head at
 * 921600 puts ~90 KB/s on the line: 4 KiB is ~45 ms of absorption, which is
 * what a short WS stall costs us instead of bytes on the floor. */
#define UART_LINK_RX_BYTES 4096
#define UART_LINK_TX_BYTES 2048
static bool installed;
static int pin_tx = UART_LINK_TX;
static int pin_rx = UART_LINK_RX;

static bool head_pin_ok(int p) {
    if (p < 0 || p >= SOC_GPIO_PIN_COUNT) return false;
    if (p == 19 || p == 20) return false; /* native USB-Serial/JTAG: our console */
    if (p >= 26 && p <= 32) return false; /* SPI flash / PSRAM */
    return true;
}

static bool head_install(void) {
    const uart_config_t cfg = {
        .baud_rate = UART_LINK_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t e;
    /* Reset the pins first: USB Serial/JTAG or earlier UART usage may leave
     * them in a mux state that blocks RX even after uart_set_pin(). */
    gpio_reset_pin(pin_tx);
    gpio_reset_pin(pin_rx);
    gpio_pullup_dis(pin_rx);
    e = uart_param_config(UART_LINK_PORT, &cfg);
    if (e != ESP_OK) { printf("[UART] param_config err=%s\n", esp_err_to_name(e)); return false; }
    e = uart_set_pin(UART_LINK_PORT, pin_tx, pin_rx, -1, -1);
    if (e != ESP_OK) { printf("[UART] set_pin err=%s\n", esp_err_to_name(e)); return false; }
    e = uart_driver_install(UART_LINK_PORT, UART_LINK_RX_BYTES, UART_LINK_TX_BYTES, 0, NULL, 0);
    if (e != ESP_OK) { printf("[UART] driver_install err=%s\n", esp_err_to_name(e)); return false; }
    /* Same reason as the C5 branch: an unpowered head leaves the line floating
     * and the ring fills with noise that the uplink would pay to carry. */
    gpio_pullup_en(pin_rx);
    installed = true;
    printf("[UART] installed port=%d tx=%d rx=%d baud=%u\n", UART_LINK_PORT, pin_tx, pin_rx, (unsigned)UART_LINK_BAUD);
    return true;
}

/* link_init() does not open this on the relay: the pins live in NVS, and NVS is
 * only read later in main.c. So uart_link_init() is the "compiled defaults"
 * entry point main.c never uses, kept only so the S3 scanner env still finds a
 * symbol if anything ever calls it. */
bool uart_link_init(void) { return false; }

bool uart_link_open(int tx, int rx) {
    if (tx == 0) tx = pin_tx;
    if (rx == 0) rx = pin_rx;
    if (!head_pin_ok(tx) || !head_pin_ok(rx)) return false;
    /* Only an *armed* link on this pair is a no-op. Written as `return installed`
     * it also answered false to the first open at boot - which asks for exactly
     * the default pair, so the relay would never install the driver at all. */
    if (installed && tx == pin_tx && rx == pin_rx) return true;
    if (installed) {
        uart_driver_delete(UART_LINK_PORT);
        installed = false;
    }
    pin_tx = tx;
    pin_rx = rx;
    return head_install();
}

void uart_link_pins(int *tx, int *rx) {
    *tx = pin_tx;
    *rx = pin_rx;
}

bool uart_link_ready(void) { return installed; }

int uart_link_write(const void *data, size_t n) {
    if (!installed) return -1;
    return uart_write_bytes(UART_LINK_PORT, data, n);
}

int uart_link_read(uint8_t *buf, size_t capacity, uint32_t timeout_ms) {
    if (!installed) return -1;
    return uart_read_bytes(UART_LINK_PORT, buf, capacity,
                           timeout_ms == UINT32_MAX ? portMAX_DELAY : pdMS_TO_TICKS(timeout_ms));
}

uint32_t uart_link_line_rate(void) { return UART_LINK_BAUD; }
unsigned uart_link_tx_ring_bytes(void) { return UART_LINK_TX_BYTES; }

#else
/* The esp32s3 scanner image has no head and no Pi: it must keep building with
 * no UART sink, and it must not gain a driver on 43/44 - that would change an
 * image whose bytes are a recorded baseline. */
bool uart_link_init(void) { return false; }
bool uart_link_ready(void) { return false; }
bool uart_link_open(int tx, int rx) {
    (void)tx;
    (void)rx;
    return false;
}
void uart_link_pins(int *tx, int *rx) {
    *tx = -1;
    *rx = -1;
}
int uart_link_write(const void *data, size_t n) {
    (void)data;
    (void)n;
    return -1;
}
int uart_link_read(uint8_t *buf, size_t capacity, uint32_t timeout_ms) {
    (void)buf;
    (void)capacity;
    (void)timeout_ms;
    return -1;
}
uint32_t uart_link_line_rate(void) { return 0; }
unsigned uart_link_tx_ring_bytes(void) { return 0; }
#endif
