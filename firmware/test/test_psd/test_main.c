/* Host golden tests for psd_compute: the Welch PSD pipeline (mean removal, IQ
 * correction, Hann window, block-floating-point S16 FFT, bin scaling, validity
 * mask) against an independent double-precision reference written here from the
 * definitions, never from psd.c's internals.
 *
 * COVERAGE, plainly: only PSD_FFT_S16R runs on the host. It is the C5 default
 * and its kernel (fft_r4_sc16.h) is plain C. F32_R2, F32_R4 and S16 call into
 * esp-dsp, which this build does not link -- the shims in this directory let
 * their init succeed but abort the transform, so a test aimed at them crashes
 * rather than passing on a stub. The region path (psd keeping its tables in
 * radio_dsp_region(), what the C5 does) is also NOT covered: the stub returns
 * NULL, so every buffer comes from the heap, the branch the S3 takes.
 * pio test -e native. GPL-3.0-or-later. */
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unity.h>

/* Lock the C5 contract: PSD_MAX_FFT 1024, as in the shipping image. Without it
 * psd.h would say 2048, which fft_r4_sc16.h cannot do (its twiddles stop at
 * FFT_TWIDDLE_N 1024) and which the C5 never asks for. */
#define RX_TARGET_C5 1
#include "../../src/radio/radio.h"
#include "../../src/dsp/psd.c"

/* psd keeps its scratch out of the heap on the C5; the host has no such bank, so
 * psd_init takes the heap branch. See the coverage note above. */
void *radio_dsp_region(size_t *bytes) {
    if (bytes) *bytes = 0;
    return NULL;
}

void setUp(void) { TEST_ASSERT_EQUAL_INT(ESP_OK, psd_init()); }
void tearDown(void) {}

/* ------------------------------------------------------------------ fixtures */

static uint32_t pack10(int i, int q) { return ((uint32_t)i & 0x3FFu) | (((uint32_t)q & 0x3FFu) << 10); }

static uint32_t rng_state = 1;
static void rng_seed(uint32_t s) { rng_state = s ? s : 1; }
static uint32_t rng_next(void) {
    uint32_t x = rng_state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return (rng_state = x);
}
/* Symmetric integer noise in [-amp, amp]. */
static int rng_noise(int amp) { return (int)(rng_next() % (uint32_t)(2 * amp + 1)) - amp; }

#define MAX_N 1024
#define MAX_SEGMENTS_WANTED 65 /* one more than PSD_MAX_SEGMENTS, to hit the cap */
static uint32_t words[MAX_SEGMENTS_WANTED * MAX_N];

/* One capture: bin-centred tone(s) plus independent noise, packed the way
 * radio.h words them. d1/d2 are bin offsets from DC (0 = off); qgain/qphase
 * error the Q arm, which is what grows an image. Each segment starts at the same
 * tone phase, so the tone power is identical in every segment and only the noise
 * varies -- which is what makes the reference comparisons below stable. */
static unsigned fill_capture(unsigned nfft, unsigned segments, double a1, int d1, double a2, int d2,
                             double qgain, double qphase_rad, int amp_iq) {
    for (unsigned s = 0; s < segments; s++)
        for (unsigned j = 0; j < nfft; j++) {
            double th = 2.0 * M_PI * (double)j / (double)nfft;
            double i = 0, q = 0;
            if (d1) {
                i += a1 * cos(th * d1);
                q += a1 * qgain * sin(th * d1 + qphase_rad);
            }
            if (d2) {
                i += a2 * cos(th * d2);
                q += a2 * qgain * sin(th * d2 + qphase_rad);
            }
            int ci = (int)lround(i) + rng_noise(amp_iq), cq = (int)lround(q) + rng_noise(amp_iq);
            if (ci > 511) ci = 511;
            if (ci < -512) ci = -512;
            if (cq > 511) cq = 511;
            if (cq < -512) cq = -512;
            words[s * nfft + j] = pack10(ci, cq);
        }
    return nfft * segments;
}

static double hann(unsigned j, unsigned n) { return 0.5 - 0.5 * cos(2.0 * M_PI * j / n); }

/* The reference Welch estimate, in the units psd_result_t promises: p[k] *
 * scale is the power of bin k relative to a full-scale (RADIO_FULL_SCALE)
 * complex tone. Built from the definitions only: periodic Hann, per-segment mean
 * removed, DFT scaled by 1/n (the contract fft_r4_sc16.h and esp-sdr's kernel
 * both honour), averaged over segments, placed at the fftshifted index psd uses.
 * O(n^2) per segment, so keep nfft and segments small. */
static double ref_pow[MAX_N];
static double ref_enbw;
static void welch_reference(unsigned nfft, unsigned segments, bool conjugate) {
    static double w[MAX_N], ct[MAX_N], st[MAX_N], re[MAX_N], im[MAX_N];
    double wsum = 0, s2 = 0;
    for (unsigned j = 0; j < nfft; j++) {
        w[j] = hann(j, nfft);
        wsum += w[j];
        s2 += w[j] * w[j];
        ct[j] = cos(2.0 * M_PI * j / nfft);
        st[j] = sin(2.0 * M_PI * j / nfft);
    }
    /* A full-scale tone has |X/N| = RADIO_FULL_SCALE * wsum/nfft in its bin. */
    const double norm = RADIO_FULL_SCALE * wsum / nfft;
    ref_enbw = nfft * s2 / (wsum * wsum);
    const double qsign = conjugate ? -1.0 : 1.0;
    memset(ref_pow, 0, nfft * sizeof(ref_pow[0]));
    for (unsigned s = 0; s < segments; s++) {
        const uint32_t *seg = words + s * nfft;
        double si = 0, sq = 0;
        for (unsigned j = 0; j < nfft; j++) {
            si += radio_word_i(seg[j]);
            sq += radio_word_q(seg[j]);
        }
        const double mi = si / nfft, mq = sq / nfft;
        for (unsigned j = 0; j < nfft; j++) {
            re[j] = (radio_word_i(seg[j]) - mi) * w[j];
            im[j] = qsign * (radio_word_q(seg[j]) - mq) * w[j];
        }
        for (unsigned k = 0; k < nfft; k++) {
            double zr = 0, zi = 0;
            for (unsigned j = 0; j < nfft; j++) {
                unsigned m = (unsigned)(((unsigned long long)k * j) % nfft);
                zr += re[j] * ct[m] + im[j] * st[m];
                zi += im[j] * ct[m] - re[j] * st[m];
            }
            unsigned idx = (k + nfft / 2) & (nfft - 1);
            ref_pow[idx] += (zr * zr + zi * zi) / ((double)nfft * nfft * norm * norm);
        }
    }
    for (unsigned k = 0; k < nfft; k++) ref_pow[k] /= segments;
}

/* The S16R path's own noise floor, in the units p*scale promises: -65 dBFS.
 * Measured on the host with noiseless tones -- the loudest bin more than two
 * away from the tone's Hann triple reads -68.7 dBFS at amplitude 511, -70.3 at
 * 128 and -71.0 at 16. It tracks the block-scaled signal rather than the
 * amplitude, because block floating point normalises every segment to ~2^13, so
 * one number covers every level. For scale: a 10-bit ADC's own quantisation
 * floor is ~-62 dBFS, so at -65 dBFS psd adds nothing the radio did not put
 * there already. This is also the dynamic range the scanner can resolve, which
 * is why no comparison below asks for anything better.
 *
 * Amplitudes 3 and 1 are the exception, and not for reasons worth fixing: the
 * signal there is round(a cos t), a 3-level waveform, so its own quantisation
 * harmonics sit far above this floor. They are still compared against the
 * reference, which sees exactly the same waveform -- just not against a floor. */
#define FP_FLOOR 3.2e-7 /* -65 dBFS */

/* Compare every bin against the reference:
 *     |p*scale - ideal| <= abs_cap * scale + rel_cap * ideal + FP_FLOOR.
 * Three terms, because three errors add, and the bound is built from the
 * reference plus separately measured properties -- never from the value under
 * test:
 *   abs_cap  the accumulator is an integer sum of integer powers, so a bin
 *            cannot be nearer than one count, plus the headroom bits psd.c gives
 *            up once more than four segments share it (headroom_counts).
 *   rel_cap  what has to hold at every level: block-float bookkeeping (the
 *            per-segment shift, acc_exp, power_shift) shows up as a scale error,
 *            and a scale error is relative.
 *   FP_FLOOR the term that makes the comparison mean anything at the bottom of
 *            the range. A floor bin holds a few counts, where the fixed-point
 *            path's rounding noise is orders of magnitude larger than any
 *            relative bound, so a purely relative tolerance is the wrong shape
 *            for a power estimate -- it fails on noise, not on bugs.
 * Bins are never skipped, so a spurious peak above the floor in a null is as
 * loud as a wrong tone. */
static unsigned bins_checked;
static double worst_ratio;
static unsigned worst_bin;
static double bound_rel(const psd_result_t *r, unsigned k, double rel_cap, double abs_cap) {
    return abs_cap * r->scale + rel_cap * ref_pow[k] + FP_FLOOR;
}
static void assert_psd_matches_reference(const psd_result_t *r, double rel_cap, double abs_cap) {
    bins_checked = 0;
    worst_ratio = 0;
    worst_bin = 0;
    const double lsb = (double)r->scale;
    for (unsigned k = 0; k < r->nfft; k++) {
        bins_checked++;
        double cap = bound_rel(r, k, rel_cap, abs_cap);
        double ratio = fabs((double)r->p[k] * lsb - ref_pow[k]) / cap;
        if (ratio > worst_ratio) {
            worst_ratio = ratio;
            worst_bin = k;
        }
    }
    char msg[192];
    snprintf(msg, sizeof(msg),
             "bin %u off by %.2f x its bound (ideal %.3e, got %.3e, bound %.3e); %u bins checked", worst_bin,
             worst_ratio, ref_pow[worst_bin], (double)r->p[worst_bin] * lsb,
             bound_rel(r, worst_bin, rel_cap, abs_cap), bins_checked);
    TEST_ASSERT_TRUE_MESSAGE(worst_ratio <= 1.0, msg);
}

/* The headroom allowance, in counts, for a run of `segments` segments: psd.c
 * shifts each segment's power down by headroom bits once the sums would not fit
 * 32 bits, and every shift truncates. Four segments need none, which is why the
 * scanner default is the accurate case. */
static double headroom_counts(unsigned segments) {
    if (segments <= 4) return 0;
    unsigned headroom = 0, s = segments - 1;
    while (s) {
        headroom++;
        s >>= 1;
    }
    headroom = 32 - headroom - 2; /* same as psd.c, written without __builtin_clz */
    return (double)segments * ((1u << headroom) - 1u);
}

/* -------------------------------------------------------------- the pipeline */

/* The word format the fixture assumes is radio.h's, not ours: pin it, or every
 * level number below silently measures a different signal. */
static void test_fixture_packs_samples_the_way_radio_does(void) {
    const int vals[] = {0, 1, -1, 300, -300, 511, -512, 100, -100};
    for (unsigned a = 0; a < sizeof(vals) / sizeof(vals[0]); a++)
        for (unsigned b = 0; b < sizeof(vals) / sizeof(vals[0]); b++) {
            uint32_t w = pack10(vals[a], vals[b]);
            TEST_ASSERT_EQUAL_INT_MESSAGE(vals[a], radio_word_i(w), "I field");
            TEST_ASSERT_EQUAL_INT_MESSAGE(vals[b], radio_word_q(w), "Q field");
        }
}

static void test_fft_type_names(void) {
    psd_set_fft(PSD_FFT_S16R);
    TEST_ASSERT_EQUAL_STRING("S16R", psd_fft_name(psd_get_fft()));
    TEST_ASSERT_EQUAL_STRING("R2", psd_fft_name(PSD_FFT_F32_R2));
    TEST_ASSERT_EQUAL_STRING("S16", psd_fft_name(PSD_FFT_S16));
}

/* psd_compute's own argument checks, the ones commands.c leans on when it
 * clamps FFT and CAPN. */
static void test_rejects_bad_sizes(void) {
    psd_result_t r;
    unsigned n = fill_capture(1024, 4, 100, 40, 0, 0, 1.0, 0.0, 2);
    TEST_ASSERT_EQUAL_INT(ESP_ERR_INVALID_ARG, psd_compute(words, n, 32, 4, 8e7f, false, 3.8e7f, 1, NULL, &r));
    TEST_ASSERT_EQUAL_INT(ESP_ERR_INVALID_ARG, psd_compute(words, n, 1000, 4, 8e7f, false, 3.8e7f, 1, NULL, &r));
    /* The C5 image caps at 1024; 2048 is the S3's number, not this target's. */
    TEST_ASSERT_EQUAL_INT(ESP_ERR_INVALID_ARG, psd_compute(words, n, 2048, 4, 8e7f, false, 3.8e7f, 1, NULL, &r));
    TEST_ASSERT_EQUAL_UINT(1024, PSD_MAX_FFT);
    /* Not one full segment of samples. */
    TEST_ASSERT_EQUAL_INT(ESP_ERR_INVALID_SIZE, psd_compute(words, 1023, 1024, 4, 8e7f, false, 3.8e7f, 1, NULL, &r));
    /* ... and SEGMENTS 0 would ask for the same thing through the other door. */
    TEST_ASSERT_EQUAL_INT(ESP_ERR_INVALID_SIZE, psd_compute(words, n, 1024, 0, 8e7f, false, 3.8e7f, 1, NULL, &r));
}

/* floor(n/nfft), then max_segments, then PSD_MAX_SEGMENTS, in that order. */
static void test_segment_count(void) {
    psd_result_t r;
    unsigned n = fill_capture(1024, 8, 100, 40, 0, 0, 1.0, 0.0, 2);
    struct {
        unsigned want, n, max_segments;
    } cases[] = {{1, 1024, 8}, {3, 3500, 8}, {3, 8192, 3}, {8, 8192, 64}, {64, 65 * 1024, 100}};
    for (unsigned c = 0; c < sizeof(cases) / sizeof(cases[0]); c++) {
        esp_err_t err = psd_compute(words, cases[c].n, 1024, cases[c].max_segments, 8e7f, false, 3.8e7f, 1, NULL, &r);
        TEST_ASSERT_EQUAL_INT_MESSAGE(ESP_OK, err, "compute failed");
        char m[64];
        snprintf(m, sizeof(m), "case %u: want %u segments", c, cases[c].want);
        TEST_ASSERT_EQUAL_UINT_MESSAGE(cases[c].want, r.segments, m);
    }
    /* The cap is what protects the accumulator's headroom; 64 segments of a
     * strong tone is the worst case the sums are sized for. */
    TEST_ASSERT_EQUAL_UINT(64, PSD_MAX_SEGMENTS);
    n = fill_capture(1024, 16, 300, 40, 0, 0, 1.0, 0.0, 0);
    welch_reference(1024, 16, false);
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 16, 8e7f, false, 3.8e7f, 1, NULL, &r));
    assert_psd_matches_reference(&r, 0.005, 8 + headroom_counts(16));
}

/* The documented contract in one number: a full-scale complex tone puts 1.0 in
 * its bin. 511 is the largest positive a 10-bit word holds, so what the ADC can
 * actually deliver is (511/512)^2. */
static void test_full_scale_tone_normalises_to_one(void) {
    psd_result_t r;
    unsigned n = fill_capture(1024, 4, 511, 32, 0, 0, 1.0, 0.0, 0);
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 4, 8e7f, false, 3.8e7f, 1, NULL, &r));
    double want = (511.0 / 512.0) * (511.0 / 512.0);
    double main = (double)r.p[512 + 32] * r.scale;
    char msg[96];
    snprintf(msg, sizeof(msg), "tone bin %.8f vs %.8f", main, want);
    TEST_ASSERT_TRUE_MESSAGE(fabs(main / want - 1.0) < 0.005, msg);
    /* A periodic Hann has exactly three DFT bins, 1/2, -1/4, -1/4, so a
     * bin-centred tone reads -6.02 dB one bin either side and nothing beyond
     * that but the accumulator's own granularity. */
    for (int side = -1; side <= 1; side += 2) {
        double nb = (double)r.p[512 + 32 + side] * r.scale;
        snprintf(msg, sizeof(msg), "neighbour %+d: %.8f (want %.8f)", side, nb, want * 0.25);
        TEST_ASSERT_TRUE_MESSAGE(fabs(nb / (want * 0.25) - 1.0) < 0.01, msg);
    }
    double farthest = 0;
    unsigned far_bin = 0;
    for (unsigned k = 0; k < 1024; k++) {
        if (k >= 512 + 30 && k <= 512 + 34) continue;
        double v = (double)r.p[k] * r.scale;
        if (v > farthest) {
            farthest = v;
            far_bin = k;
        }
    }
    /* Everything outside the tone's three Hann bins is this path's own noise, and
     * FP_FLOOR (-65 dBFS) is the limit measured in front of it -- the worst bin
     * here has been -68.7 dBFS. 65 dB is well past what the 10-bit front end can
     * ask for, and a leak that climbs through it (window wrong, shift mis-applied,
     * digit-reversal broken) is a real regression rather than rounding. */
    snprintf(msg, sizeof(msg), "loudest far bin %u at %.1f dBFS, %.1f dB below the tone (floor %.1f dBFS)", far_bin,
             10.0 * log10(farthest), 10.0 * log10(farthest / main), 10.0 * log10(FP_FLOOR));
    TEST_ASSERT_TRUE_MESSAGE(farthest < FP_FLOOR, msg);
}

/* Block floating point is the part of psd.c most likely to be wrong and least
 * likely to be noticed: the per-segment shift, acc_exp and power_shift must
 * cancel exactly, or the same signal reads a different level at a different
 * amplitude. The shift each amplitude lands on here (bound * 2^shift <= 16383)
 * is 511 -> 5, 128 -> 6, 16 -> 9 and 3 and 1 -> 12, the cap; 300 with 8 segments
 * adds the headroom path. */
static void test_level_is_independent_of_block_shift(void) {
    psd_result_t r;
    const int amps[] = {511, 128, 16, 3, 1};
    for (unsigned c = 0; c < sizeof(amps) / sizeof(amps[0]); c++) {
        unsigned n = fill_capture(1024, 4, amps[c], 40, 0, 0, 1.0, 0.0, 0);
        welch_reference(1024, 4, false);
        TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 4, 8e7f, false, 3.8e7f, 1, NULL, &r));
        char m[64];
        snprintf(m, sizeof(m), "amp %d", amps[c]);
        assert_psd_matches_reference(&r, 0.005, 8);
        TEST_ASSERT_EQUAL_UINT_MESSAGE(1024, bins_checked, m);
        /* And the tone still reads its own level in absolute terms: (a/512)^2
         * is what the scanner would turn into dBFS. */
        double got = (double)r.p[512 + 40] * r.scale;
        double ref = (double)ref_pow[512 + 40];
        snprintf(m, sizeof(m), "%s: %.6f vs reference %.6f", m, got, ref);
        TEST_ASSERT_TRUE_MESSAGE(fabs(got / ref - 1.0) < 0.01, m);
    }
    /* 8 segments is where headroom bits start being reserved. */
    unsigned n = fill_capture(1024, 8, 300, 40, 120, 150, 1.0, 0.0, 3);
    welch_reference(1024, 8, false);
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 8, 8e7f, false, 3.8e7f, 1, NULL, &r));
    TEST_ASSERT_EQUAL_UINT(8, r.segments);
    assert_psd_matches_reference(&r, 0.005, 8 + headroom_counts(8));
}

/* The whole pipeline against the reference: a tone pair over a noise floor at
 * the shipping default size. */
static void test_matches_double_welch_reference(void) {
    psd_result_t r;
    unsigned n = fill_capture(1024, 4, 100, 40, 60, 150, 1.0, 0.0, 3);
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 4, 8e7f, false, 3.8e7f, 1, NULL, &r));
    welch_reference(1024, 4, false);
    /* rel_cap 0.5 %: fft_r4_sc16's own error is ~0.33 LSB rms (test_dsp measures
     * that), which is far under it at these levels. abs_cap 8 counts is the
     * accumulator's own granularity, and it is what keeps a bin the reference
     * puts at ~0 from being failed for one LSB. */
    assert_psd_matches_reference(&r, 0.005, 8);
    TEST_ASSERT_TRUE_MESSAGE(bins_checked == 1024, "not every bin compared");
    TEST_ASSERT_EQUAL_UINT(4, r.segments);
    /* The reference's own ENBW, from the Hann sums it already made. */
    TEST_ASSERT_TRUE(fabs(r.enbw_bins - ref_enbw) < 1e-4);
    /* The frequency axis, which every consumer multiplies bin indices by. Exact
     * equality is not a flourish: 8e7 is 78125 * 2^10, so every bin_hz here is
     * representable in float without rounding. */
    TEST_ASSERT_TRUE_MESSAGE((double)r.bin_hz == 8e7 / 1024.0, "bin_hz != rate/nfft");
}

/* ENBW is what turns per-bin power into a channel power; 1.5 bins for Hann is
 * the analytic value and metrics.c divides RSSI by it. */
static void test_enbw_is_hann(void) {
    psd_result_t r;
    unsigned n = fill_capture(1024, 4, 100, 40, 0, 0, 1.0, 0.0, 2);
    for (unsigned nfft = 64; nfft <= 1024; nfft *= 4) {
        double s1 = 0, s2 = 0;
        for (unsigned j = 0; j < nfft; j++) {
            double w = hann(j, nfft);
            s1 += w;
            s2 += w * w;
        }
        TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, nfft, 4, 8e7f, false, 3.8e7f, 1, NULL, &r));
        TEST_ASSERT_EQUAL_UINT(nfft, r.nfft);
        TEST_ASSERT_TRUE(fabs(r.enbw_bins - nfft * s2 / (s1 * s1)) < 1e-4);
        TEST_ASSERT_TRUE(fabs(r.enbw_bins - 1.5) < 1e-3);
        char m[64];
        snprintf(m, sizeof(m), "nfft %u: bin_hz %g", nfft, (double)r.bin_hz);
        TEST_ASSERT_TRUE_MESSAGE((double)r.bin_hz == 8e7 / (double)nfft, m);
    }
}

/* The validity mask is all the scanner has for "is this LO leakage, or outside
 * the RX filter"; metrics.c refuses to sum a bin the mask clears. */
static void test_valid_mask(void) {
    psd_result_t r;
    unsigned n = fill_capture(1024, 4, 100, 40, 0, 0, 1.0, 0.0, 2);
    /* bin_hz 78125 exactly, usable 3906250 Hz = 50 bins, guard 2 -> |rel| 3..50. */
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 4, 8e7f, false, 3906250.0f, 2, NULL, &r));
    unsigned want_usable = 0;
    for (unsigned k = 0; k < 1024; k++) {
        unsigned rel = (unsigned)abs((int)k - 512);
        unsigned want = rel > 2 && rel <= 50;
        char m[48];
        snprintf(m, sizeof(m), "bin %u (rel %u)", k, rel);
        TEST_ASSERT_EQUAL_UINT_MESSAGE(want, r.valid[k] != 0, m);
        want_usable += want;
    }
    TEST_ASSERT_EQUAL_UINT(96, want_usable);
    /* A guard wider than the usable span leaves nothing, and that is reported
     * as an empty mask rather than an error: the RSSI windows simply read the
     * floor. */
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 4, 8e7f, false, 1000.0f, 2000, NULL, &r));
    unsigned usable = 0;
    for (unsigned k = 0; k < 1024; k++) usable += r.valid[k] != 0;
    TEST_ASSERT_EQUAL_UINT(0, usable);
    /* usable_hz beyond Nyquist keeps every bin but DC. */
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 4, 8e7f, false, 1e12f, 0, NULL, &r));
    usable = 0;
    for (unsigned k = 0; k < 1024; k++) usable += r.valid[k] != 0;
    TEST_ASSERT_EQUAL_UINT(1023, usable);
}

/* conjugate flips the spectrum: the scanner always conjugates on the C5, so the
 * whole band lands mirrored and every consumer assumes psd did it.
 *
 * The one hazard psd.h does not say: p and valid point at psd.c's single static
 * buffers, so a second psd_compute overwrites the first result. Snapshot before
 * recomputing -- anything that holds two results at once has to do the same. */
static void test_conjugate_mirrors_bins(void) {
    psd_result_t r;
    static uint32_t plain_p[MAX_N];
    static uint8_t plain_valid[MAX_N];
    double plain_scale;
    unsigned n = fill_capture(1024, 4, 200, 100, 0, 0, 1.0, 0.0, 2);
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 4, 8e7f, false, 3.9e7f, 1, NULL, &r));
    memcpy(plain_p, r.p, r.nfft * sizeof(plain_p[0]));
    memcpy(plain_valid, r.valid, r.nfft);
    plain_scale = r.scale;
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 1024, 4, 8e7f, true, 3.9e7f, 1, NULL, &r));
    double main_plain = (double)plain_p[512 + 100] * plain_scale;
    double main_conj = (double)r.p[512 - 100] * r.scale;
    double wrong_conj = (double)r.p[512 + 100] * r.scale;
    char msg[128];
    snprintf(msg, sizeof(msg), "%.6f vs %.6f, unmirrored bin %.4f dB below", main_plain, main_conj,
             10.0 * log10(wrong_conj / main_conj));
    TEST_ASSERT_TRUE_MESSAGE(fabs(main_conj / main_plain - 1.0) < 0.01, msg);
    TEST_ASSERT_TRUE_MESSAGE(10.0 * log10(wrong_conj / main_conj) < -50.0, msg);
    welch_reference(1024, 4, true);
    assert_psd_matches_reference(&r, 0.005, 8);
    /* The mask does not move with conjugation. */
    TEST_ASSERT_EQUAL_UINT8_ARRAY_MESSAGE(plain_valid, r.valid, 1024, "mask moved with conjugation");
}

/* saturated and dc_i/dc_q are the two things the scanner shows about the front
 * end itself; both are plain counts of the raw words. */
static void test_saturation_and_dc_are_raw_counts(void) {
    psd_result_t r;
    const unsigned nfft = 256, segments = 4;
    /* -512 and 511 are both rails; 510 is not. The mean is a known offset. */
    for (unsigned s = 0; s < segments; s++)
        for (unsigned j = 0; j < nfft; j++) {
            int i = 511, q = -512;
            if (j % 4) {
                i = 510;
                q = 7;
            }
            if (j == 200) {
                i = -512;
                q = 511;
            }
            words[s * nfft + j] = pack10(i, q);
        }
    unsigned want_sat = 0;
    long want_si = 0, want_sq = 0;
    for (unsigned s = 0; s < segments; s++)
        for (unsigned j = 0; j < nfft; j++) {
            int i = radio_word_i(words[s * nfft + j]), q = radio_word_q(words[s * nfft + j]);
            want_sat += (i == 511 || i == -512 || q == 511 || q == -512);
            want_si += i;
            want_sq += q;
        }
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, nfft * segments, nfft, segments, 8e7f, false, 3e7f, 1, NULL, &r));
    TEST_ASSERT_EQUAL_UINT_MESSAGE(want_sat, r.saturated, "rail count");
    TEST_ASSERT_TRUE(fabs(r.dc_i - (double)want_si / (nfft * segments)) < 1e-3);
    TEST_ASSERT_TRUE(fabs(r.dc_q - (double)want_sq / (nfft * segments)) < 1e-3);
}

/* iq->ii, iq->qq and iq->iq are, by definition, the variances and covariance of
 * the raw samples in a segment, averaged over segments. Straight from that
 * definition here. */
static void test_iq_state_holds_second_moments(void) {
    psd_result_t r;
    psd_iq_t iq;
    memset(&iq, 0, sizeof(iq));
    unsigned n = fill_capture(256, 4, 120, 40, 90, 100, 0.9, 0.14, 3);
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 256, 4, 8e7f, false, 3e7f, 1, &iq, &r));
    TEST_ASSERT_TRUE(iq.init);
    double ii = 0, qq = 0, iqm = 0;
    for (unsigned s = 0; s < 4; s++) {
        double si = 0, sq = 0, pii = 0, pqq = 0, piq = 0;
        for (unsigned j = 0; j < 256; j++) {
            double i = radio_word_i(words[s * 256 + j]), q = radio_word_q(words[s * 256 + j]);
            si += i;
            sq += q;
            pii += i * i;
            pqq += q * q;
            piq += i * q;
        }
        ii += pii / 256 - (si / 256) * (si / 256);
        qq += pqq / 256 - (sq / 256) * (sq / 256);
        iqm += piq / 256 - (si / 256) * (sq / 256);
    }
    ii /= 4;
    qq /= 4;
    iqm /= 4;
    char msg[128];
    snprintf(msg, sizeof(msg), "ii %.1f/%.1f qq %.1f/%.1f iq %.1f/%.1f", iq.ii, ii, iq.qq, qq, iq.iq, iqm);
    TEST_ASSERT_TRUE_MESSAGE(fabs(iq.ii / ii - 1.0) < 0.01, msg);
    TEST_ASSERT_TRUE_MESSAGE(fabs(iq.qq / qq - 1.0) < 0.01, msg);
    TEST_ASSERT_TRUE_MESSAGE(fabs(iq.iq / iqm - 1.0) < 0.01, msg);

    /* A stronger capture then moves the state toward the new moments without
     * reaching them: one noisy hop must not retune the correction outright. */
    n = fill_capture(256, 4, 400, 40, 300, 100, 0.9, 0.14, 3);
    double want = 0;
    for (unsigned s = 0; s < 4; s++) {
        double si = 0, pii = 0;
        for (unsigned j = 0; j < 256; j++) {
            double i = radio_word_i(words[s * 256 + j]);
            si += i;
            pii += i * i;
        }
        want += pii / 256 - (si / 256) * (si / 256);
    }
    want /= 4;
    float before = iq.ii;
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 256, 4, 8e7f, false, 3e7f, 1, &iq, &r));
    snprintf(msg, sizeof(msg), "%.1f -> %.1f, target %.1f", before, iq.ii, want);
    TEST_ASSERT_TRUE_MESSAGE(iq.ii > before && iq.ii < want, msg);
    for (int c = 0; c < 80; c++)
        TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 256, 4, 8e7f, false, 3e7f, 1, &iq, &r));
    snprintf(msg, sizeof(msg), "converged %.1f vs %.1f", iq.ii, want);
    TEST_ASSERT_TRUE_MESSAGE(fabs(iq.ii / want - 1.0) < 0.02, msg);
}

/* Image rejection is the reason the correction exists. With Q's arm carrying a
 * gain g and a phase error phi, one tone of amplitude a is
 *     i + j q = (a/2) [ (1 + g e^{j phi}) e^{j th} + (1 - g e^{-j phi}) e^{-j th} ],
 * so its mirror sits 10 log10(|1 - g e^{j phi}|^2 / |1 + g e^{j phi}|^2) below it.
 * For g = 0.9 and phi = 8 degrees that is -21.2 dB, not the -25.6 dB the gain
 * error alone would give -- the phase error accounts for 4.4 dB of the leak, and
 * a correction that only equalised the gains would leave that much standing.
 * Two tones because psd.h requires a proper complex signal (one tone is not). */
static void test_iq_correction_suppresses_images(void) {
    psd_result_t r;
    const unsigned nfft = 256;
    const int d1 = 40, d2 = 100;
    const double gain = 0.9, phase = 8.0 * M_PI / 180.0;
    const double a = 200;
    const double ge_re = gain * cos(phase), ge_im = gain * sin(phase);
    const double main_pow = (1.0 + ge_re) * (1.0 + ge_re) + ge_im * ge_im;
    const double image_pow = (1.0 - ge_re) * (1.0 - ge_re) + ge_im * ge_im;
    const double want_image_db = 10.0 * log10(image_pow / main_pow);
    unsigned n = fill_capture(nfft, 4, a, d1, a, d2, gain, phase, 1);
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, nfft, 4, 8e7f, false, 3e7f, 1, NULL, &r));
    double main_plain = (double)r.p[nfft / 2 + d1] * r.scale;
    double image_plain = (double)r.p[nfft / 2 - d1] * r.scale;
    double floor_bin = (double)r.p[nfft / 2 + 20] * r.scale; /* tone-free bin: the floor */
    double uncorrected_db = 10.0 * log10(image_plain / main_plain);
    /* Also the second tone's mirror, which must agree: the error is across the
     * band, not per channel. */
    double image2_db = 10.0 * log10((double)r.p[nfft / 2 - d2] * r.scale / ((double)r.p[nfft / 2 + d2] * r.scale));

    psd_iq_t iq;
    memset(&iq, 0, sizeof(iq));
    for (int c = 0; c < 80; c++) {
        rng_seed(7000 + c); /* fresh noise each capture, same imbalance */
        n = fill_capture(nfft, 4, a, d1, a, d2, gain, phase, 1);
        TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, nfft, 4, 8e7f, false, 3e7f, 1, &iq, &r));
    }
    double main_fixed = (double)r.p[nfft / 2 + d1] * r.scale;
    double image_fixed = (double)r.p[nfft / 2 - d1] * r.scale;
    double corrected_db = 10.0 * log10(image_fixed / main_fixed);
    char msg[200];
    snprintf(msg, sizeof(msg),
             "image %.1f dB -> %.1f dB (want %.1f dB), second tone %.1f dB, floor %.1f dB, main %.3e -> %.3e",
             uncorrected_db, corrected_db, want_image_db, image2_db,
             10.0 * log10(floor_bin / main_plain), main_plain, main_fixed);
    TEST_ASSERT_TRUE_MESSAGE(fabs(uncorrected_db - want_image_db) < 1.0, msg);
    TEST_ASSERT_TRUE_MESSAGE(fabs(image2_db - uncorrected_db) < 1.0, msg);
    TEST_ASSERT_TRUE_MESSAGE(uncorrected_db - corrected_db > 30.0, msg);
    /* Corrected: down at the noise floor of the same capture, not merely lower. */
    TEST_ASSERT_TRUE_MESSAGE(image_fixed <= floor_bin * 3.0, msg);
    /* And the corrected main bin reads the tone's true level: q' = c1 q + c2 i
     * is exact for this signal, so the mirror's power is gone rather than folded
     * into the tone. Uncorrected the tone itself reads main_pow/4 = -4.5 dB low,
     * which is the 11 % move in msg. */
    const double true_level = (a / RADIO_FULL_SCALE) * (a / RADIO_FULL_SCALE);
    double main2_fixed = (double)r.p[nfft / 2 + d2] * r.scale;
    snprintf(msg, sizeof(msg), "corrected main %.6f / %.6f vs true level %.6f", main_fixed, main2_fixed, true_level);
    TEST_ASSERT_TRUE_MESSAGE(fabs(main_fixed / true_level - 1.0) < 0.03, msg);
    TEST_ASSERT_TRUE_MESSAGE(fabs(main2_fixed / true_level - 1.0) < 0.03, msg);
}

/* psd_release_unused() is what commands.c calls after an FFT switch; it must not
 * free what the type in use still needs. */
static void test_release_unused_keeps_the_active_type_usable(void) {
    psd_result_t r;
    unsigned n = fill_capture(256, 4, 100, 40, 0, 0, 1.0, 0.0, 2);
    psd_set_fft(PSD_FFT_S16R);
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 256, 4, 8e7f, false, 3e7f, 1, NULL, &r));
    double before = (double)r.p[128 + 40] * r.scale;
    psd_release_unused();
    TEST_ASSERT_EQUAL_INT(ESP_OK, psd_compute(words, n, 256, 4, 8e7f, false, 3e7f, 1, NULL, &r));
    TEST_ASSERT_EQUAL_UINT(4, r.segments);
    char m[96];
    snprintf(m, sizeof(m), "%.9g before, %.9g after release", before, (double)r.p[128 + 40] * r.scale);
    TEST_ASSERT_TRUE_MESSAGE(before == (double)r.p[128 + 40] * r.scale, m);
    psd_set_fft(PSD_FFT_S16R);
}

int main(void) {
    UNITY_BEGIN();
    psd_set_fft(PSD_FFT_S16R);
    rng_seed(20261102);
    RUN_TEST(test_fixture_packs_samples_the_way_radio_does);
    RUN_TEST(test_fft_type_names);
    RUN_TEST(test_rejects_bad_sizes);
    RUN_TEST(test_segment_count);
    RUN_TEST(test_full_scale_tone_normalises_to_one);
    RUN_TEST(test_level_is_independent_of_block_shift);
    RUN_TEST(test_matches_double_welch_reference);
    RUN_TEST(test_enbw_is_hann);
    RUN_TEST(test_valid_mask);
    RUN_TEST(test_conjugate_mirrors_bins);
    RUN_TEST(test_saturation_and_dc_are_raw_counts);
    RUN_TEST(test_iq_state_holds_second_moments);
    RUN_TEST(test_iq_correction_suppresses_images);
    RUN_TEST(test_release_unused_keeps_the_active_type_usable);
    return UNITY_END();
}
