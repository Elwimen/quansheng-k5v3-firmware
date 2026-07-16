#include "goertzel_fix.h"
#include <math.h>   /* only gfix_coeff_q13() uses this; not on the hot path */

int32_t gfix_coeff_q13(float sample_rate, float tone_hz, uint16_t window_n)
{
    int   k = (int)(0.5f + (window_n * tone_hz) / sample_rate);
    float w = (2.0f * (float)M_PI * k) / window_n;
    return (int32_t)lrintf(2.0f * cosf(w) * (float)(1 << GFIX_Q));
}

void gfix_init(gfix_t *g, int32_t coeff_q13, uint16_t window_n)
{
    g->coeff = coeff_q13;
    g->s1 = g->s2 = 0;
    g->n = window_n;
    g->count = 0;
}

int gfix_push(gfix_t *g, int16_t x, int64_t *power_out)
{
    /* s0 = x + coeff*s1 - s2   (coeff is Q13) */
    int32_t s0 = (int32_t)x + (int32_t)(((int64_t)g->coeff * g->s1) >> GFIX_Q) - g->s2;
    g->s2 = g->s1;
    g->s1 = s0;

    if (++g->count < g->n)
        return 0;

    /* power = s1^2 + s2^2 - coeff*s1*s2   (raw; /N cancels in the ratio) */
    int32_t m = (int32_t)(((int64_t)g->coeff * g->s1) >> GFIX_Q);
    int64_t p = (int64_t)g->s1 * g->s1 + (int64_t)g->s2 * g->s2 - (int64_t)m * g->s2;

    g->s1 = g->s2 = 0;
    g->count = 0;
    if (power_out) *power_out = p;
    return 1;
}


void cwfix_init(cwfix_t *d, int32_t coeff_q13, uint16_t window_n,
                uint8_t on_sh, uint8_t off_sh)
{
    gfix_init(&d->g, coeff_q13, window_n);
    d->floor = 0;
    d->on_sh = on_sh;
    d->off_sh = off_sh;
    d->up_sh = 6;   /* rise ~1/64 */
    d->dn_sh = 3;   /* fall ~1/8  */
    d->tone = 0;
}

int cwfix_push(cwfix_t *d, int16_t x, int *tone_out,
               int64_t *power_out, int64_t *floor_out)
{
    int64_t p;
    if (!gfix_push(&d->g, x, &p))
        return 0;

    if (d->floor <= 0)
        d->floor = p;

    /* Track the floor only while the tone is off, so signal doesn't inflate it. */
    if (!d->tone) {
        int64_t diff = p - d->floor;
        d->floor += (diff >= 0) ? (diff >> d->up_sh) : (diff >> d->dn_sh);
    }

    int64_t ref = d->floor > 0 ? d->floor : 1;
    if (!d->tone && p >= (ref << d->on_sh))       d->tone = 1;
    else if (d->tone && p <= (ref << d->off_sh))  d->tone = 0;

    if (tone_out)  *tone_out  = d->tone;
    if (power_out) *power_out = p;
    if (floor_out) *floor_out = d->floor;
    return 1;
}
