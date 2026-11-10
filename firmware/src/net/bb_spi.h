/* GPIO bit-bang SPI API (W5500 transport). GPL-3.0-or-later. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "driver/gpio.h"
#include "esp_err.h"

#if !defined(RX_W5500)
/* Stub — never called without the flag, keeps headers consistent. */
#else

esp_err_t bb_spi_init(gpio_num_t sclk, gpio_num_t mosi, gpio_num_t miso, gpio_num_t cs);
esp_err_t bb_spi_write(uint8_t mode, uint16_t addr, const void *data, size_t n);
esp_err_t bb_spi_read (uint8_t mode, uint16_t addr, void       *data, size_t n);

#endif
