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
 * We use a GPIO bit-bang SPI (bb_spi.c) rather than esp_driver_spi because
 * that driver's DMA desc pool + queues push the C5's static RAM past the
 * 0x40820000 RF-dump-bank guard. Bit-bang on 240 MHz does ~20 MHz effective,
 * well within W5500's 80 MHz spec.
 *
 * GPL-3.0-or-later. */
#ifdef RX_W5500
#include "eth_w5500.h"
#include "bb_spi.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "link.h"    /* link_printf — bypasses CONFIG_LOG_NONE */
#include <arpa/inet.h>
#include <stdio.h>
#include <string.h>

static const char *TAG = "eth_w5500";

/* ========================= W5500 SPI primitives ========================= */
/* Control byte layout: [7: R/W=0 wr/1 rd][6:4 SPI mode][3:0 socket] */
#define W5500_MODE_COMMON    0x00
#define W5500_MODE_SOCK      0x08
#define W5500_MODE_SOCK_TX   0x10
#define W5500_MODE_SOCK_RX   0x18

/* Common registers */
#define REG_MR          0x0000   /* Mode Register */
#define REG_GAR         0x0001
#define REG_SUBR        0x0005
#define REG_SHAR        0x0009
#define REG_SIPR        0x000F
#define REG_RTR         0x0019
#define REG_RCR         0x001B
#define REG_PSTAT       0x0035
#define REG_PHYCFGR     0x002E   /* PHY Config Register 1: [7]RST [5]LPBK [4]SWOFF [1]SPD(1=100M/0=10M) [0]DUP(1=full) */
#define REG_PHYCFGR1    0x002F   /* PHY Config Register 2 */

/* Socket 0 registers */
#define S0_MR           0x0400
#define S0_CR           0x0401
#define S0_IR           0x0402
#define S0_SR           0x0403
#define S0_PORTR        0x040A   /* Source Port Register (local bind) */
#define S0_DPORTR       0x0404   /* Destination Port (remote) */
#define S0_DIPR         0x040C   /* Destination IP (remote, 4 bytes) */
#define S0_RXBUF_SIZE   0x041E
#define S0_TXBUF_SIZE   0x041F
#define S0_RX_FSR       0x0420
#define S0_RD_POINTER   0x0422
#define S0_WR_POINTER   0x0424
#define S0_TX_FSR       0x0428
#define S0_TX_RD_POINTER 0x042A
#define S0_TX_WR_POINTER 0x042C

/* Socket buffer physical addresses (W5500 datasheet §3.3) */
#define SOCK_RX_BASE(sock)  (0x600000 + (sock) * 0x10000)
#define SOCK_TX_BASE(sock)  (0x200000 + (sock) * 0x10000)
#define SOCK_BUF_SIZE       0x4000

/* Commands */
#define SOCK_CMD_OPEN       0x01
#define SOCK_CMD_LISTEN     0x02
#define SOCK_CMD_DISCON     0x04
#define SOCK_CMD_CLOSE      0x05
#define SOCK_CMD_SEND       0x10
#define SOCK_CMD_RECV       0x20

/* Modes */
#define SOCKMR_TCP          0x21

/* Status */
#define SOCK_SR_INIT        0x13
#define SOCK_SR_LISTEN      0x14
#define SOCK_SR_ESTAB       0x17
#define SOCK_SR_CLOSE_WAIT  0x1B
#define SOCK_SR_CLOSED      0x00

/* Interrupt bits */
#define SOCK_IR_SENDOK      0x10
#define SOCK_IR_TIMEOUT     0x08
#define SOCK_IR_RECV        0x04
#define SOCK_IR_DISCON      0x02

/* ---------- common register helpers ---------- */
static esp_err_t wr8(uint16_t addr, uint8_t v)  { return bb_spi_write(W5500_MODE_COMMON, addr, &v, 1); }
static esp_err_t rd8(uint16_t addr, uint8_t *v) { return bb_spi_read (W5500_MODE_COMMON, addr, v, 1); }
static esp_err_t wr16(uint16_t addr, uint16_t v){ uint8_t b[2]={(uint8_t)(v>>8),(uint8_t)(v&0xff)}; return bb_spi_write(W5500_MODE_COMMON, addr, b, 2); }
static esp_err_t rd16(uint16_t addr, uint16_t *v){ uint8_t b[2]; ESP_RETURN_ON_ERROR(bb_spi_read(W5500_MODE_COMMON, addr, b, 2), TAG, ""); *v=(b[0]<<8)|b[1]; return ESP_OK; }

/* ---------- socket register helpers ---------- */
#define s_wr8(a,v)  bb_spi_write(W5500_MODE_SOCK, a, &((uint8_t){v}), 1)
#define s_rd8(a,v)  bb_spi_read (W5500_MODE_SOCK, a, v, 1)
static esp_err_t s_wr16(uint16_t a, uint16_t v){ uint8_t b[2]={(uint8_t)(v>>8),(uint8_t)(v&0xff)}; return bb_spi_write(W5500_MODE_SOCK, a, b, 2); }
static esp_err_t s_rd16(uint16_t a, uint16_t *v){ uint8_t b[2]; ESP_RETURN_ON_ERROR(bb_spi_read(W5500_MODE_SOCK, a, b, 2), TAG, ""); *v=(b[0]<<8)|b[1]; return ESP_OK; }

/* ---------- socket buffer I/O ---------- */
static esp_err_t tx_buf_write(const void *data, uint16_t offset, size_t n) {
    return bb_spi_write(W5500_MODE_SOCK_TX, SOCK_TX_BASE(0) + offset, data, n);
}
static esp_err_t rx_buf_read(void *data, uint16_t offset, size_t n) {
    return bb_spi_read(W5500_MODE_SOCK_RX, SOCK_RX_BASE(0) + offset, data, n);
}

/* ========================= W5500 init ================================= */

static volatile bool s_connected;
static volatile bool s_server_started;

static esp_err_t reset_w5500(void) {
    gpio_config_t cfg = { .pin_bit_mask = (1ULL << ETH_PIN_RST), .mode = GPIO_MODE_OUTPUT };
    ESP_RETURN_ON_ERROR(gpio_config(&cfg), TAG, "gpio RST");
    gpio_set_level(ETH_PIN_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(50));
    gpio_set_level(ETH_PIN_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(300));
    return ESP_OK;
}

static esp_err_t config_common_regs(void) {
    ESP_RETURN_ON_ERROR(wr8(REG_MR, 0x10), TAG, "MR");
    uint8_t mac[6] = { 0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x55 };
    ESP_RETURN_ON_ERROR(bb_spi_write(W5500_MODE_COMMON, REG_SHAR, mac, 6), TAG, "SHAR");

    /* IP / gateway / netmask — either from compile-time macros or the
     * original link-local defaults. We use inet_aton() so the platformio.ini
     * -DETH_IP_ADDR='"192.168.0.98"' strings are actually parsed. */
    uint8_t sip[4] = {169,254,1,2};
    uint8_t gar[4] = {10,0,0,1};
    uint8_t sub[4] = {255,255,255,0};
#if ETH_USE_STATIC_IP
    struct in_addr tmp;
    if (inet_aton(ETH_IP_ADDR, &tmp)) memcpy(sip, &tmp.s_addr, 4);
    if (inet_aton(ETH_IP_GATEWAY, &tmp)) memcpy(gar, &tmp.s_addr, 4);
    if (inet_aton(ETH_IP_NETMASK, &tmp)) memcpy(sub, &tmp.s_addr, 4);
#endif
    link_printf("[eth_w5500] IP %u.%u.%u.%u GW %u.%u.%u.%u SUB %u.%u.%u.%u\n",
             sip[0],sip[1],sip[2],sip[3],
             gar[0],gar[1],gar[2],gar[3],
             sub[0],sub[1],sub[2],sub[3]);
    ESP_RETURN_ON_ERROR(bb_spi_write(W5500_MODE_COMMON, REG_SIPR, sip, 4), TAG, "SIPR");
    ESP_RETURN_ON_ERROR(bb_spi_write(W5500_MODE_COMMON, REG_GAR,  gar, 4), TAG, "GAR");
    ESP_RETURN_ON_ERROR(bb_spi_write(W5500_MODE_COMMON, REG_SUBR, sub, 4), TAG, "SUBR");
    ESP_RETURN_ON_ERROR(wr16(REG_RTR, 0x07D0), TAG, "RTR");
    ESP_RETURN_ON_ERROR(wr8(REG_RCR, 8),       TAG, "RCR");
    ESP_RETURN_ON_ERROR(wr8(0x0016, 0xFF), TAG, "IMR mask");  /* INT mask */
    ESP_RETURN_ON_ERROR(wr8(0x0018, 0xFF), TAG, "SIMR mask");
    /* Leave PHY on autoneg (PHYCFGR=0x00, PHYCFGR1.ANEGEN=1). The host NIC
     * (AX88179A in our case) sometimes hangs at 1000baseT even when the
     * W5500 PHY advertises 100base-TX only — but after a unplug/replug or a
     * link flap it usually renegotiates down correctly. Do NOT force speed
     * here; doing so disables ANEG and makes an already-failing host worse. */
    ESP_RETURN_ON_ERROR(wr8(REG_PHYCFGR, 0x00),  TAG, "PHYCFGR autoneg");
    ESP_RETURN_ON_ERROR(wr8(REG_PHYCFGR1, 0x20), TAG, "PHYCFGR1 ANEGEN=1");
    /* Read back all common regs to verify — boot log will show these. */
    uint8_t r_sip[4], r_gar[4], r_sub[4], r_shar[6], mr, phy0, phy1;
    bb_spi_read(W5500_MODE_COMMON, REG_SIPR,  r_sip, 4);
    bb_spi_read(W5500_MODE_COMMON, REG_GAR,   r_gar, 4);
    bb_spi_read(W5500_MODE_COMMON, REG_SUBR,  r_sub, 4);
    bb_spi_read(W5500_MODE_COMMON, REG_SHAR,  r_shar, 6);
    rd8(REG_MR, &mr); rd8(REG_PHYCFGR, &phy0); rd8(REG_PHYCFGR1, &phy1);
    link_printf("[eth_w5500] VERIFY SIP=%u.%u.%u.%u GAR=%u.%u.%u.%u SUB=%u.%u.%u.%u MAC=%02X:%02X:%02X:%02X:%02X:%02X MR=0x%02x PHY0=0x%02x PHY1=0x%02x\n",
             r_sip[0],r_sip[1],r_sip[2],r_sip[3],
             r_gar[0],r_gar[1],r_gar[2],r_gar[3],
             r_sub[0],r_sub[1],r_sub[2],r_sub[3],
             r_shar[0],r_shar[1],r_shar[2],r_shar[3],r_shar[4],r_shar[5],
             mr, phy0, phy1);
    return ESP_OK;
}

esp_err_t eth_init(void) {
    link_printf("[eth_w5500] >>> eth_init START\n");
    link_printf("[eth_w5500] reset W5500 RST=%d\n", ETH_PIN_RST);
    ESP_RETURN_ON_ERROR(reset_w5500(), TAG, "reset");
    link_printf("[eth_w5500] bb_spi_init SCLK=%d MOSI=%d MISO=%d CS=%d\n", ETH_PIN_SCLK, ETH_PIN_MOSI, ETH_PIN_MISO, ETH_PIN_CS);
    ESP_RETURN_ON_ERROR(bb_spi_init(ETH_PIN_SCLK, ETH_PIN_MOSI, ETH_PIN_MISO, ETH_PIN_CS), TAG, "bb_spi");
    /* Probe W5500 — read VERSION register (0x0020), expect 0x04. */
    uint8_t ver = 0;
    bb_spi_read(W5500_MODE_COMMON, 0x0020, &ver, 1);
    link_printf("[eth_w5500] VERSION reg = 0x%02x (expect 0x04 for W5500)\n", ver);
    if (ver != 0x04) {
        link_printf("[eth_w5500] >>> SPI PROBE FAILED! Wiring issue? SCLK=%d MOSI=%d MISO=%d CS=%d\n",
                    ETH_PIN_SCLK, ETH_PIN_MOSI, ETH_PIN_MISO, ETH_PIN_CS);
    }
    link_printf("[eth_w5500] config_common_regs\n");
    ESP_RETURN_ON_ERROR(config_common_regs(), TAG, "common_regs");
    link_printf("[eth_w5500] after config_common_regs\n");
    /* Wait for PHY auto-negotiation (up to 2 s). */
    for (int i = 0; i < 40; i++) {
        uint8_t pstat;
        ESP_RETURN_ON_ERROR(rd8(REG_PSTAT, &pstat), TAG, "pstat");
        if (pstat & 0x01) { link_printf("[eth_w5500] PHY up, PSTAT=0x%02x\n", pstat); break; }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    link_printf("[eth_w5500] W5500 init done\n");
    return ESP_OK;
}

/* ========================= TCP socket 0 =============================== */

static bool wait_sr(uint8_t expected, uint32_t timeout_ms) {
    uint32_t waited = 0;
    uint8_t sr;
    while (waited < timeout_ms) {
        if (s_rd8(S0_SR, &sr) == ESP_OK && sr == expected) return true;
        vTaskDelay(pdMS_TO_TICKS(5));
        waited += 5;
    }
    return false;
}

static uint8_t sock_sr(void) { uint8_t v = 0; s_rd8(S0_SR, &v); return v; }
static uint8_t sock_ir(void) { uint8_t v = 0; s_rd8(S0_IR, &v); return v; }
static void    sock_clear_ir(uint8_t b) { s_wr8(S0_IR, b); }

static void sock_cmd(uint8_t cmd) { s_wr8(S0_CR, cmd); }

void eth_listen(void) {
    if (s_server_started) return;
    s_wr8(S0_MR, SOCKMR_TCP);
    sock_cmd(SOCK_CMD_OPEN);
    if (!wait_sr(SOCK_SR_INIT, 200)) {
        ESP_LOGE(TAG, "OPEN failed, SR=0x%02x", sock_sr());
        return;
    }
    s_wr16(S0_PORTR, ETH_TCP_PORT);  /* local bind port */
    sock_cmd(SOCK_CMD_LISTEN);
    if (!wait_sr(SOCK_SR_LISTEN, 200)) {
        ESP_LOGE(TAG, "LISTEN failed, SR=0x%02x", sock_sr());
        return;
    }
    s_server_started = true;
    ESP_LOGI(TAG, "TCP server listening port %d", ETH_TCP_PORT);
}

bool eth_accept(uint32_t timeout_ms) {
    if (!s_server_started) return false;
    uint32_t waited = 0;
    while (waited < timeout_ms) {
        uint8_t sr = sock_sr();
        if (sr == SOCK_SR_ESTAB) {
            s_connected = true;
            sock_clear_ir(0xFF);
            ESP_LOGI(TAG, "client connected");
            return true;
        }
        if (sr != SOCK_SR_LISTEN) {
            ESP_LOGW(TAG, "SR=0x%02x — re-listen", sr);
            sock_cmd(SOCK_CMD_CLOSE);
            vTaskDelay(pdMS_TO_TICKS(10));
            s_wr8(S0_MR, SOCKMR_TCP);
            sock_cmd(SOCK_CMD_OPEN);
            wait_sr(SOCK_SR_INIT, 200);
            sock_cmd(SOCK_CMD_LISTEN);
            wait_sr(SOCK_SR_LISTEN, 200);
        }
        vTaskDelay(pdMS_TO_TICKS(10));
        waited += 10;
    }
    return false;
}

void eth_disconnect(void) {
    sock_cmd(SOCK_CMD_DISCON);
    vTaskDelay(pdMS_TO_TICKS(20));
    sock_cmd(SOCK_CMD_CLOSE);
    s_connected = false;
    s_server_started = false;
}

bool eth_connected(void) { return s_connected; }

int eth_recv(uint8_t *buf, size_t cap, uint32_t timeout_ms) {
    if (!s_connected) return 0;
    uint32_t waited = 0;
    while (waited < timeout_ms) {
        uint8_t sr = sock_sr();
        uint8_t ir = sock_ir();
        if (sr == SOCK_SR_CLOSED || (sr == SOCK_SR_LISTEN && ir)) {
            if (ir & SOCK_IR_DISCON) {
                ESP_LOGI(TAG, "peer disconnected");
                sock_clear_ir(0xFF);
                sock_cmd(SOCK_CMD_CLOSE);
                s_connected = false;
                return 0;
            }
        }

        uint16_t rx_fsr = 0;
        s_rd16(S0_RX_FSR, &rx_fsr);
        if (rx_fsr == 0) {
            vTaskDelay(pdMS_TO_TICKS(5));
            waited += 5;
            continue;
        }

        uint16_t rd_ptr = 0;
        s_rd16(S0_RD_POINTER, &rd_ptr);
        size_t to_read = rx_fsr < cap ? rx_fsr : cap;
        ESP_RETURN_ON_FALSE(rx_buf_read(buf, rd_ptr, to_read) == ESP_OK, 0, TAG, "rx_buf");
        s_wr16(S0_RD_POINTER, (uint16_t)((rd_ptr + to_read) & (SOCK_BUF_SIZE - 1)));
        sock_cmd(SOCK_CMD_RECV);
        ir = sock_ir();
        if (ir & SOCK_IR_RECV) sock_clear_ir(SOCK_IR_RECV);
        return (int)to_read;
    }
    return 0;
}

bool eth_send(const void *data, size_t n) {
    if (!s_connected || n == 0) return false;
    uint16_t wr_ptr = 0;
    s_rd16(S0_TX_WR_POINTER, &wr_ptr);
    ESP_RETURN_ON_FALSE(tx_buf_write(data, wr_ptr, n) == ESP_OK, false, TAG, "tx_buf");
    s_wr16(S0_TX_WR_POINTER, (uint16_t)((wr_ptr + n) & (SOCK_BUF_SIZE - 1)));
    sock_cmd(SOCK_CMD_SEND);

    uint32_t waited = 0;
    while (waited < 200) {
        uint8_t ir = sock_ir();
        if (ir & SOCK_IR_SENDOK) { sock_clear_ir(ir); return true; }
        if (ir & SOCK_IR_TIMEOUT) { sock_clear_ir(ir); return false; }
        if (ir & SOCK_IR_DISCON) { sock_clear_ir(ir); s_connected = false; return false; }
        vTaskDelay(pdMS_TO_TICKS(2));
        waited += 2;
    }
    return false;
}

void eth_status(char *out, size_t cap) {
    uint8_t pstat = 0;
    rd8(REG_PSTAT, &pstat);
    if (!(pstat & 0x01)) { snprintf(out, cap, "DOWN (no link)"); return; }
    const char *speed = (pstat & 0x80) ? "10Mbps" : "100Mbps";
    const char *full = (pstat & 0x02) ? "full" : "half";
    snprintf(out, cap, "UP %s %s %sconnected", speed, full, s_connected ? "" : "not ");
}

/* Read remote client IP + port into a string like "192.168.1.50:54321".
 * Returns 0 while no client is connected (caller should also check eth_connected()). */
int eth_remote(char *ip_buf, size_t cap) {
    if (!s_connected) { if (ip_buf && cap) ip_buf[0] = '\0'; return 0; }
    uint8_t ip[4] = {0};
    uint16_t port = 0;
    if (bb_spi_read(W5500_MODE_SOCK, S0_DIPR, ip, 4) != ESP_OK) { if (ip_buf && cap) ip_buf[0] = '\0'; return 0; }
    s_rd16(S0_DPORTR, &port);
    int n = snprintf(ip_buf, cap, "%u.%u.%u.%u:%u", ip[0], ip[1], ip[2], ip[3], port);
    return n > 0 ? n : 0;
}

/* Read local IP of the W5500 interface. */
int eth_local(char *ip_buf, size_t cap) {
    if (bb_spi_read(W5500_MODE_COMMON, REG_SIPR, (uint8_t*)ip_buf, 4) != ESP_OK) { if (ip_buf && cap) ip_buf[0] = '\0'; return 0; }
    /* BUG: above writes raw bytes into the buffer — fix properly. */
    uint8_t ip[4] = {0};
    bb_spi_read(W5500_MODE_COMMON, REG_SIPR, ip, 4);
    int n = snprintf(ip_buf, cap, "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    return n > 0 ? n : 0;
}

#endif /* RX_W5500 */
