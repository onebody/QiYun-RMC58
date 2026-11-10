/* GPIO bit-bang SPI — avoids esp_driver_spi (which pulls a fat BSS pool that
 * trips the C5's RF dump bank guard). W5500 SPI is simple enough that 10 MHz
 * is achievable in bit-bang on a 240 MHz core, and we do not need DMA for
 * the small registers/buffers W5500 exposes (3-byte cmd + 1..16384 byte buf).
 *
 * GPL-3.0-or-later. */
#ifdef RX_W5500

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "bb_spi";

static gpio_num_t s_sclk, s_mosi, s_miso, s_cs;
static uint32_t   s_delay_ns;  /* per-bit delay */

/* ---------- platform timing ---------- */
#if defined(__riscv) && ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
#include "esp_rom_sys.h"
static inline void bb_delay_ns(uint32_t ns) { if (ns) esp_rom_delay_us((ns + 999) / 1000); }
#else
static inline void bb_delay_ns(uint32_t ns) {
    /* Fallback — busy-loop calibrated for 240 MHz; one iteration is ~6 ns. */
    volatile uint32_t i;
    for (i = 0; i < (ns / 6); i++) { __asm__ volatile("nop"); }
}
#endif

static inline void bb_cs_low(void)  { gpio_set_level(s_cs, 0); }
static inline void bb_cs_high(void) { gpio_set_level(s_cs, 1); }
static inline void bb_sclk_low(void) { gpio_set_level(s_sclk, 0); bb_delay_ns(s_delay_ns); }
static inline void bb_sclk_high(void){ gpio_set_level(s_sclk, 1); bb_delay_ns(s_delay_ns); }

static void bb_spi_write_byte(uint8_t b) {
    for (int bit = 7; bit >= 0; bit--) {
        gpio_set_level(s_mosi, (b >> bit) & 1);
        bb_sclk_high();
        bb_sclk_low();
    }
}

static uint8_t bb_spi_read_byte(void) {
    uint8_t b = 0;
    for (int bit = 7; bit >= 0; bit--) {
        bb_sclk_high();
        int v = gpio_get_level(s_miso);
        bb_sclk_low();
        b |= (uint8_t)(v & 1) << bit;
    }
    return b;
}

/* ---------- public API ---------- */

esp_err_t bb_spi_init(gpio_num_t sclk, gpio_num_t mosi, gpio_num_t miso, gpio_num_t cs) {
    s_sclk = sclk; s_mosi = mosi; s_miso = miso; s_cs = cs;
    s_delay_ns = 100;  /* ~5 MHz; conservative until wiring is proven */

    gpio_config_t out_cfg = {
        .pin_bit_mask = (1ULL << s_sclk) | (1ULL << s_mosi) | (1ULL << s_cs),
        .mode         = GPIO_MODE_OUTPUT,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&out_cfg), TAG, "output gpio");

    gpio_config_t in_cfg = {
        .pin_bit_mask = (1ULL << s_miso),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_ENABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&in_cfg), TAG, "input gpio");

    bb_cs_high();
    bb_sclk_low();
    ESP_LOGI(TAG, "bit-bang SPI init OK SCLK=%d MOSI=%d MISO=%d CS=%d", s_sclk, s_mosi, s_miso, s_cs);
    return ESP_OK;
}

esp_err_t bb_spi_write(uint8_t mode, uint16_t addr, const void *data, size_t n) {
    const uint8_t *p = (const uint8_t *)data;
    bb_cs_low();
    bb_spi_write_byte((uint8_t)(0x80 | mode));
    bb_spi_write_byte((uint8_t)(addr >> 8));
    bb_spi_write_byte((uint8_t)(addr & 0xff));
    for (size_t i = 0; i < n; i++) bb_spi_write_byte(p[i]);
    bb_cs_high();
    return ESP_OK;
}

esp_err_t bb_spi_read(uint8_t mode, uint16_t addr, void *data, size_t n) {
    uint8_t *p = (uint8_t *)data;
    bb_cs_low();
    bb_spi_write_byte(mode);  /* MSB = 0 means read */
    bb_spi_write_byte((uint8_t)(addr >> 8));
    bb_spi_write_byte((uint8_t)(addr & 0xff));
    for (size_t i = 0; i < n; i++) p[i] = bb_spi_read_byte();
    bb_cs_high();
    return ESP_OK;
}

#endif /* RX_W5500 */
