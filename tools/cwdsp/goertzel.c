#include "goertzel.h"
#include <math.h>

void goertzel_init(goertzel_t *g, float sample_rate, float tone_hz, uint16_t window_n)
{
    /* Snap the tone to the nearest DFT bin so the block is coherent. */
    int   k = (int)(0.5f + (window_n * tone_hz) / sample_rate);
    float w = (2.0f * (float)M_PI * k) / window_n;
    g->coeff = 2.0f * cosf(w);
    g->s1 = g->s2 = 0.0f;
    g->n = window_n;
    g->count = 0;
}

bool goertzel_push(goertzel_t *g, float sample, float *power_out)
{
    float s0 = sample + g->coeff * g->s1 - g->s2;
    g->s2 = g->s1;
    g->s1 = s0;

    if (++g->count < g->n)
        return false;

    /* End of block: magnitude^2 without the final complex twiddle. */
    float power = g->s1 * g->s1 + g->s2 * g->s2 - g->coeff * g->s1 * g->s2;
    /* Normalise by N so the level is independent of window length. */
    power /= (float)g->n;

    g->s1 = g->s2 = 0.0f;
    g->count = 0;
    if (power_out) *power_out = power;
    return true;
}


/* ------------------------------------------------------------------ */

static float db_to_ratio(float db) { return powf(10.0f, db / 10.0f); }

void cw_det_init(cw_det_t *d, float sample_rate, float tone_hz,
                 uint16_t window_n, float on_db, float off_db)
{
    goertzel_init(&d->g, sample_rate, tone_hz, window_n);
    d->floor   = 0.0f;
    d->snr_on  = db_to_ratio(on_db);
    d->snr_off = db_to_ratio(off_db);
    /* Floor tracker: rise slowly, and only track DOWN fast when the tone is off
     * so a long dah doesn't drag the floor up into the signal. Asymmetric,
     * single-pole per block. */
    d->floor_up = 0.02f;
    d->floor_dn = 0.10f;
    d->tone = false;
    d->block_rate = (uint16_t)(sample_rate / window_n + 0.5f);
}

bool cw_det_push(cw_det_t *d, float sample, bool *tone_out,
                 float *power_out, float *floor_out)
{
    float power;
    if (!goertzel_push(&d->g, sample, &power))
        return false;

    /* Seed the floor on the first block. */
    if (d->floor <= 0.0f)
        d->floor = power;

    /* Update the noise floor only while we believe the tone is OFF, so signal
     * energy doesn't inflate it. Track down quickly, up slowly. */
    if (!d->tone) {
        float a = (power < d->floor) ? d->floor_dn : d->floor_up;
        d->floor += a * (power - d->floor);
    }

    float ref = (d->floor > 1e-9f) ? d->floor : 1e-9f;
    float ratio = power / ref;
    if (!d->tone && ratio >= d->snr_on)  d->tone = true;
    else if (d->tone && ratio <= d->snr_off) d->tone = false;

    if (tone_out)  *tone_out  = d->tone;
    if (power_out) *power_out = power;
    if (floor_out) *floor_out = d->floor;
    return true;
}
