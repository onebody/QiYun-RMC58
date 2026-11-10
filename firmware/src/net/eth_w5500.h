/* W5500 raw SPI + socket API driver for XIAO ESP32-C5.
 *
 * Wiring (XIAO ESP32-C5 -> W5500 module):
 *   3V3-OUT -> 3.3V
 *   GND     -> GND
 *   D8      -> SCLK  (GPIO8)
 *   D9      -> MISO  (GPIO9)
 *   D10     -> MOSI  (GPIO10)
 *   D2      -> CS    (GPIO1)
 *   D6      -> RESET (GPIO12)
 *   D5      -> INT   (GPIO24, optional — we poll)
 *
 * This driver uses W5500's built-in TCP/IP stack directly (no lwIP, no esp_eth).
 * It brings up a single TCP server on socket 0 that the host connects to.
 *
 * GPL-3.0-or-later. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

/* ------- Pinout (override at compile time) ------- */
#ifndef ETH_SPI_HOST
#define ETH_SPI_HOST        SPI2_HOST
#endif
#ifndef ETH_SPI_CLK_MHZ
#define ETH_SPI_CLK_MHZ     20
#endif

#ifndef ETH_PIN_SCLK
#define ETH_PIN_SCLK        8
#endif
#ifndef ETH_PIN_MISO
#define ETH_PIN_MISO        9
#endif
#ifndef ETH_PIN_MOSI
#define ETH_PIN_MOSI        10
#endif
#ifndef ETH_PIN_CS
#define ETH_PIN_CS          1
#endif
#ifndef ETH_PIN_RST
#define ETH_PIN_RST         12
#endif

#ifndef ETH_USE_STATIC_IP
#define ETH_USE_STATIC_IP   0
#endif
#if ETH_USE_STATIC_IP
#ifndef ETH_IP_ADDR
#define ETH_IP_ADDR         "192.168.1.100"
#endif
#ifndef ETH_IP_GATEWAY
#define ETH_IP_GATEWAY      "192.168.1.1"
#endif
#ifndef ETH_IP_NETMASK
#define ETH_IP_NETMASK      "255.255.255.0"
#endif
#endif

#ifndef ETH_TCP_PORT
#define ETH_TCP_PORT        12345
#endif

/* Bring up SPI + configure W5500 IP (static — DHCP on W5500 would take extra
 * ROM; use static 192.168.1.100 if you don't change the defaults). */
esp_err_t eth_init(void);

/* Open socket 0 as TCP, bind to ETH_TCP_PORT, transition to LISTEN.
 * Returns immediately — call eth_accept() in a loop to catch connections. */
void eth_listen(void);

/* One-shot accept — blocks up to timeout_ms, returns true on accept. */
bool eth_accept(uint32_t timeout_ms);

/* Connection status. */
bool eth_connected(void);
void eth_disconnect(void);

/* Synchronous send/recv — return count sent/recv'd (0 = disconnect/timeout). */
int  eth_recv(uint8_t *buf, size_t cap, uint32_t timeout_ms);
bool eth_send(const void *data, size_t n);

/* Human-readable "UP 100Mbps full 192.168.1.100" / "DOWN". */
void eth_status(char *out, size_t cap);

/* Get remote client "IP:port" or empty string if disconnected. */
int eth_remote(char *ip_buf, size_t cap);

/* Get local W5500 IP address. */
int eth_local(char *ip_buf, size_t cap);
