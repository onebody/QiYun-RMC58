/* Host tests for the UART pacing arithmetic. The numbers here are pinned to
 * what the 115200 path measured on real hardware (F1: 9000 B/s budget, spectra
 * clamped to 512 bins), so a parameterisation that changes them is a behaviour
 * change on the Pi's RotorHazard line, not an improvement.
 * pio test -e native. GPL-3.0-or-later. */
#include <stdint.h>
#include <unity.h>
#include "../../src/proto/line_pace.h"

void setUp(void) {}
void tearDown(void) {}

static void test_budget_at_115200_is_today_number(void) {
    TEST_ASSERT_EQUAL_UINT32(9000, pace_budget(115200));
}

static void test_budget_at_921600(void) {
    /* 921600 8N1 = 92160 B/s line; the same 25/32 share F1 used. */
    TEST_ASSERT_EQUAL_UINT32(72000, pace_budget(921600));
}

static void test_bins_cap_at_115200_is_512(void) {
    /* The clamp commands.c wrote as a literal until now. */
    TEST_ASSERT_EQUAL_UINT16(512, pace_spec_bins(115200, 1024));
}

static void test_bins_cap_at_921600_is_ring_bound(void) {
    /* At 921600 the line no longer binds: the C5's 1024 B TX ring does. */
    TEST_ASSERT_EQUAL_UINT16(992, pace_spec_bins(921600, 1024));
    /* A bigger ring runs into link.c's FRAME_UART_MAX (1024 bins) instead. */
    TEST_ASSERT_EQUAL_UINT16(1024, pace_spec_bins(921600, 2048));
}

static void test_line_bytes_is_8n1(void) {
    TEST_ASSERT_EQUAL_UINT32(11520, pace_line_bytes(115200));
    TEST_ASSERT_EQUAL_UINT32(92160, pace_line_bytes(921600));
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_budget_at_115200_is_today_number);
    RUN_TEST(test_budget_at_921600);
    RUN_TEST(test_bins_cap_at_115200_is_512);
    RUN_TEST(test_bins_cap_at_921600_is_ring_bound);
    RUN_TEST(test_line_bytes_is_8n1);
    return UNITY_END();
}
