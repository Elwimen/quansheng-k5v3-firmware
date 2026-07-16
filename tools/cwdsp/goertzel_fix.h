/* Fixed-point Goertzel CW tone detector — the firmware version.
 *
 * Integer-only hot path (no FPU, no soft-float, no division): one 32x32->64
 * multiply per sample. The block power feeds an integer noise-floor tracker;
 * the ON/OFF decision is a ratio (power vs floor), so the 1/N normalisation
 * cancels and is skipped entirely.
 *
 * Input: int16 AF samples (e.g. a 12-bit ADC reading, sign-centred). Keep the
 * amplitude within ~+/-2048 so the resonant state stays well inside int32.
 */
#ifndef CWDSP_GOERTZEL_FIX_H
#define CWDSP_GOERTZEL_FIX_H

#include <stdint.h>

#define GFIX_Q 13   /* coeff fixed-point fraction bits: coeff = 2*cos(w) << 13 */

typedef struct {
    int32_t  coeff;     /* Q13: round(2*cos(2*pi*k/N) * 2^13) */
    int32_t  s1, s2;    /* IIR state */
    uint16_t n, count;
} gfix_t;

void gfix_init(gfix_t *g, int32_t coeff_q13, uint16_t window_n);
/* one sample; returns 1 once per N samples, writing raw block power (int64). */
int  gfix_push(gfix_t *g, int16_t x, int64_t *power_out);

/* Coeff helper. Uses float cos() — call once at init (or precompute a constant
 * for a fixed pitch to keep the build 100% float-free). */
int32_t gfix_coeff_q13(float sample_rate, float tone_hz, uint16_t window_n);


typedef struct {
    gfix_t  g;
    int64_t floor;      /* tracked noise-floor power */
    uint8_t on_sh, off_sh;  /* ON when power >= floor<<on_sh (2 ~ +6dB) */
    uint8_t up_sh, dn_sh;   /* floor EMA: += diff>>up (rise) / >>dn (fall) */
    int     tone;
} cwfix_t;

/* on_sh/off_sh in power-of-two steps (~3dB each): 2/1 ≈ 6dB on, 3dB off. */
void cwfix_init(cwfix_t *d, int32_t coeff_q13, uint16_t window_n,
                uint8_t on_sh, uint8_t off_sh);
int  cwfix_push(cwfix_t *d, int16_t x, int *tone_out,
                int64_t *power_out, int64_t *floor_out);

#endif /* CWDSP_GOERTZEL_FIX_H */
