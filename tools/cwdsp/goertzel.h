/* Goertzel single-bin tone detector for CW.
 *
 * A CW note is a single audio tone (the beat/sidetone, ~500-900 Hz) keyed on/off.
 * The current firmware measures BROADBAND AF amplitude (BK4819 REG_6F), which
 * integrates the tone AND all the band noise, so it can't tell a keyed note from
 * a burst of hiss. The Goertzel is a one-bin DFT: a matched filter of bandwidth
 * ~= Fs/N centred on the tone. Narrowing that filter (larger N) rejects noise
 * outside the CW pitch, exactly like the ear does.
 *
 * NOISE REJECTION BY INTEGRATION: the block of N samples is coherently summed,
 * so the effective noise bandwidth is Fs/N. Against a flat-noise wideband
 * envelope of bandwidth B, the processing gain is ~10*log10(B / (Fs/N)).
 * The only limit is time resolution: the window T = N/Fs must stay well under
 * one CW "dit" (dit_ms ~= 1200/wpm), or fast keying smears. Rule of thumb:
 * T <= dit/3.  e.g. 20 wpm -> dit 60ms -> T<=20ms; at Fs=8kHz that's N<=160.
 *
 * Pure C, no FPU needed conceptually (kept float here for the reference/sim; a
 * fixed-point port is a mechanical follow-up for the Cortex-M0+). One multiply-
 * add per input sample.
 */
#ifndef CWDSP_GOERTZEL_H
#define CWDSP_GOERTZEL_H

#include <stdint.h>
#include <stdbool.h>

typedef struct {
    float    coeff;     /* 2*cos(2*pi*k/N) */
    float    s1, s2;    /* IIR state */
    uint16_t n;         /* block length (window) */
    uint16_t count;     /* samples accumulated in the current block */
} goertzel_t;

/* Configure for tone_hz at sample_rate, integrating over window_n samples. */
void  goertzel_init(goertzel_t *g, float sample_rate, float tone_hz, uint16_t window_n);

/* Push one sample. Returns true once per N samples, writing the block's tone
 * POWER (magnitude^2, arbitrary units) to *power_out. */
bool  goertzel_push(goertzel_t *g, float sample, float *power_out);


/* ------------------------------------------------------------------ */
/* Tone-present detector: Goertzel + adaptive noise floor + hysteresis */
/* ------------------------------------------------------------------ */

typedef struct {
    goertzel_t g;
    float   floor;          /* tracked noise-floor power */
    float   snr_on, snr_off;/* hysteresis thresholds, linear power ratios */
    float   floor_up, floor_dn; /* floor tracker coefficients */
    bool    tone;           /* current decision */
    uint16_t block_rate;    /* Fs/N, informational (blocks per second) */
} cw_det_t;

/* on_db/off_db: turn the tone ON above floor*10^(on_db/10), OFF below off_db. */
void  cw_det_init(cw_det_t *d, float sample_rate, float tone_hz,
                  uint16_t window_n, float on_db, float off_db);

/* Push one sample; returns true once per block, writing the boolean tone
 * decision to *tone_out (and optionally the raw power / floor for logging). */
bool  cw_det_push(cw_det_t *d, float sample, bool *tone_out,
                  float *power_out, float *floor_out);

#endif /* CWDSP_GOERTZEL_H */
