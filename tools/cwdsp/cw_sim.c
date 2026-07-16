/* CW-in-noise test bench for the Goertzel detector.
 *
 * Synthesises Morse for a message (raised-cosine keying, no clicks), buries it in
 * heavy Gaussian noise at a chosen WIDEBAND SNR, writes a listenable WAV, and
 * runs two detectors on the SAME samples:
 *   1. Goertzel narrow-bin tone detector (goertzel.c)  -- the proposed AF path
 *   2. a broadband AF-envelope detector                -- mimics BK4819 REG_6F
 * It scores each against ground-truth keying and decodes the Goertzel stream to
 * text, so you can both LISTEN (does it sound like copyable CW?) and SEE whether
 * the narrow filter recovers it where the broadband envelope drowns.
 *
 *   cc -O2 -o cw_sim cw_sim.c goertzel.c -lm
 *   ./cw_sim --snr -6 --wpm 18 --tone 700 --out cw_test.wav
 */
#include "goertzel.h"
#include "goertzel_fix.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdint.h>

/* ---- Morse table (index by A-Z, 0-9) ---- */
static const char *MORSE[] = {
 ".-","-...","-.-.","-..",".","..-.","--.","....","..",".---","-.-",".-..","--",
 "-.","---",".--.","--.-",".-.","...","-","..-","...-",".--","-..-","-.--","--..",
 "-----",".----","..---","...--","....-",".....","-....","--...","---..","----."};

static const char *morse_of(char c) {
    if (c >= 'a' && c <= 'z') c -= 32;
    if (c >= 'A' && c <= 'Z') return MORSE[c - 'A'];
    if (c >= '0' && c <= '9') return MORSE[26 + (c - '0')];
    return NULL; /* space / unknown */
}
static char char_of(const char *sym) {
    for (int i = 0; i < 36; i++)
        if (strcmp(MORSE[i], sym) == 0)
            return i < 26 ? 'A' + i : '0' + (i - 26);
    return '?';
}

/* ---- gaussian noise (Box-Muller) ---- */
static float randn(void) {
    float u1 = (rand() + 1.0f) / (RAND_MAX + 1.0f);
    float u2 = (rand() + 1.0f) / (RAND_MAX + 1.0f);
    return sqrtf(-2.0f * logf(u1)) * cosf(2.0f * (float)M_PI * u2);
}

/* ---- tiny WAV writer (16-bit PCM mono) ---- */
static void write_wav(const char *path, const float *x, int n, int fs) {
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); exit(1); }
    int data = n * 2, riff = 36 + data;
    uint16_t ch = 1, bits = 16, fmt = 1; uint32_t byterate = fs * 2; uint16_t align = 2;
    fwrite("RIFF", 1, 4, f); fwrite(&riff, 4, 1, f); fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f); uint32_t sz = 16; fwrite(&sz, 4, 1, f);
    fwrite(&fmt, 2, 1, f); fwrite(&ch, 2, 1, f); uint32_t r = fs; fwrite(&r, 4, 1, f);
    fwrite(&byterate, 4, 1, f); fwrite(&align, 2, 1, f); fwrite(&bits, 2, 1, f);
    fwrite("data", 1, 4, f); fwrite(&data, 4, 1, f);
    for (int i = 0; i < n; i++) {
        float v = x[i]; if (v > 1) v = 1; if (v < -1) v = -1;
        int16_t s = (int16_t)lrintf(v * 32767.0f);
        fwrite(&s, 2, 1, f);
    }
    fclose(f);
}

/* De-glitch a block-decision stream (drop runs shorter than half a dit) and
 * decode it to text using the known unit. Writes into out[]. */
static void decode_stream(unsigned char *dec, long blocks, int bpd, char *out, int outsz) {
    int minrun = bpd / 2; if (minrun < 1) minrun = 1;
    for (int pass = 0; pass < 2; pass++) {
        long i = 0;
        while (i < blocks) {
            long j = i; while (j < blocks && dec[j] == dec[i]) j++;
            if (j - i < minrun && i > 0 && j < blocks)
                for (long k = i; k < j; k++) dec[k] = dec[i-1];
            i = j;
        }
    }
    char sym[16]; int sl = 0, tl = 0;
    long run = 0; int cur = dec[0];
    for (long i = 1; i <= blocks; i++) {
        int v = (i < blocks) ? dec[i] : -1;
        run++;
        if (v != cur) {
            float units = (float)run / bpd;
            if (cur) { if (sl < 15) sym[sl++] = units >= 2.0f ? '-' : '.'; }
            else {
                if (units >= 5.0f) { if (sl){sym[sl]=0; if(tl<outsz-1)out[tl++]=char_of(sym); sl=0;} if(tl<outsz-1)out[tl++]=' '; }
                else if (units >= 2.0f) { if (sl){sym[sl]=0; if(tl<outsz-1)out[tl++]=char_of(sym); sl=0;} }
            }
            cur = v; run = 0;
        }
    }
    if (sl) { sym[sl]=0; if(tl<outsz-1) out[tl++]=char_of(sym); }
    out[tl] = 0;
}

int main(int argc, char **argv) {
    /* defaults */
    int   fs = 8000, wpm = 18, N = 64;
    float tone = 700.0f, snr_db = -6.0f;   /* wideband tone-RMS / noise-RMS */
    float jitter = 0.12f;                   /* human timing swing (fractional std dev) */
    const char *msg = "CQ CQ DE N0CALL K";
    const char *out = "cw_test.wav";
    unsigned seed = 1;

    for (int i = 1; i < argc - 1; i++) {
        if      (!strcmp(argv[i], "--snr"))  snr_db = atof(argv[++i]);
        else if (!strcmp(argv[i], "--wpm"))  wpm = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--tone")) tone = atof(argv[++i]);
        else if (!strcmp(argv[i], "--fs"))   fs = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--n"))    N = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--jitter")) jitter = atof(argv[++i]);
        else if (!strcmp(argv[i], "--msg"))  msg = argv[++i];
        else if (!strcmp(argv[i], "--out"))  out = argv[++i];
        else if (!strcmp(argv[i], "--seed")) seed = atoi(argv[++i]);
    }
    srand(seed);

    int dit = (int)(fs * 1.2f / wpm + 0.5f);      /* samples per dit (nominal) */
    float A = 0.30f;                               /* tone peak amplitude */

    /* ---- build a segment list {on, length}, applying human timing jitter ---- */
    /* Each element/gap length is scaled by (1 + jitter*N(0,1)), plus a slow
     * speed drift across the message -- an imperfect "fist" rather than a
     * machine. jitter=0 gives perfect keying. */
    typedef struct { int on; int len; } seg_t;
    seg_t *seg = malloc(sizeof(seg_t) * (strlen(msg) * 12 + 8));
    int   ns = 0;
    #define JIT(nom) ( (int)((nom) * (1.0f + jitter*randn()             \
                       + 0.15f*jitter*sinf(6.28f*seg_phase)) + 0.5f) )
    float seg_phase = 0.0f, seg_dphase = 0.7f / (strlen(msg) + 1);
    for (const char *p = msg; *p; p++, seg_phase += seg_dphase) {
        if (*p == ' ') { int L = JIT(7*dit); if (L<dit) L=dit; seg[ns++] = (seg_t){0, L}; continue; }
        const char *m = morse_of(*p); if (!m) continue;
        for (const char *e = m; *e; e++) {
            int L = JIT((*e == '-' ? 3 : 1) * dit); if (L < dit/3) L = dit/3;
            seg[ns++] = (seg_t){1, L};
            if (e[1]) { int g = JIT(dit); if (g<dit/3) g=dit/3; seg[ns++] = (seg_t){0, g}; }
        }
        char nxt = p[1];
        if (nxt && nxt != ' ') { int g = JIT(3*dit); if (g<dit) g=dit; seg[ns++] = (seg_t){0, g}; }
    }

    long total = 2 * (long)fs;                     /* 1s lead-in + 1s tail of noise */
    for (int i = 0; i < ns; i++) total += seg[i].len;
    float *key = calloc(total, sizeof(float));
    long pos = fs;                                 /* start after lead-in */
    int ramp = (int)(fs * 0.005f);                 /* 5ms raised-cosine edge */
    for (int i = 0; i < ns; i++) {
        int len = seg[i].len;
        if (seg[i].on) {
            for (int j = 0; j < len; j++) {
                float g = 1.0f;
                if (j < ramp)            g = 0.5f - 0.5f * cosf((float)M_PI * j / ramp);
                else if (j > len - ramp) g = 0.5f - 0.5f * cosf((float)M_PI * (len - j) / ramp);
                key[pos + j] = g;
            }
        }
        pos += len;
    }
    free(seg);

    /* ---- signal = key*tone + noise ---- */
    float sig_rms = A / sqrtf(2.0f);
    float noise_sigma = sig_rms / powf(10.0f, snr_db / 20.0f);
    float *x = malloc(total * sizeof(float));
    double ph = 0, dph = 2.0 * M_PI * tone / fs;
    for (long i = 0; i < total; i++) {
        x[i] = key[i] * A * (float)sin(ph) + noise_sigma * randn();
        ph += dph; if (ph > 2 * M_PI) ph -= 2 * M_PI;
    }
    write_wav(out, x, total, fs);

    /* effective in-band SNR the Goertzel sees (post-integration) */
    float gain_db = 10.0f * log10f((float)N / 2.0f);

    /* ---- run three detectors on the SAME samples, blockwise ---- */
    cw_det_t g;  cw_det_init(&g, fs, tone, N, 6.0f, 3.0f);          /* float Goertzel */
    cwfix_t  fx; cwfix_init(&fx, gfix_coeff_q13(fs, tone, N), N, 2, 1); /* FIXED Goertzel */
    /* broadband envelope (REG_6F-like): per-block mean power + same floor logic */
    float bb_floor = 0, bb_on = powf(10, 6.0f/10), bb_off = powf(10, 3.0f/10);
    int bb_tone = 0; double bb_acc = 0; int bb_cnt = 0;

    long nblk = total / N;
    unsigned char *g_dec = malloc(nblk), *f_dec = malloc(nblk),
                  *b_dec = malloc(nblk), *truth = malloc(nblk);
    long gi = 0, fi = 0, bi = 0, ti = 0;

    for (long i = 0; i < total; i++) {
        bool tg; float pw, fl;
        if (cw_det_push(&g, x[i], &tg, &pw, &fl)) g_dec[gi++] = tg;

        /* fixed path sees a 12-bit-style int16 sample, like the real ADC would */
        int tf; int64_t fp, ff;
        int16_t xi = (int16_t)lrintf(x[i] * 2047.0f);
        if (cwfix_push(&fx, xi, &tf, &fp, &ff)) f_dec[fi++] = tf;

        bb_acc += (double)x[i] * x[i]; bb_cnt++;
        if (bb_cnt == N) {
            float p = bb_acc / N; bb_acc = 0; bb_cnt = 0;
            if (bb_floor <= 0) bb_floor = p;
            if (!bb_tone) { float a = (p < bb_floor) ? 0.10f : 0.02f; bb_floor += a*(p-bb_floor); }
            float ratio = p / (bb_floor > 1e-9f ? bb_floor : 1e-9f);
            if (!bb_tone && ratio >= bb_on) bb_tone = 1;
            else if (bb_tone && ratio <= bb_off) bb_tone = 0;
            b_dec[bi++] = bb_tone;
        }
        if ((i % N) == N/2) { if (ti < nblk) truth[ti++] = key[i] > 0.25f; }
    }
    long blocks = gi;
    if (fi < blocks) blocks = fi;
    if (bi < blocks) blocks = bi;
    if (ti < blocks) blocks = ti;

    /* ---- score vs ground truth + float-vs-fixed agreement ---- */
    long g_ok = 0, f_ok = 0, b_ok = 0, agree = 0;
    for (long i = 0; i < blocks; i++) {
        if (g_dec[i] == truth[i]) g_ok++;
        if (f_dec[i] == truth[i]) f_ok++;
        if (b_dec[i] == truth[i]) b_ok++;
        if (g_dec[i] == f_dec[i]) agree++;
    }

    int bpd = dit / N; if (bpd < 1) bpd = 1;        /* blocks per dit */
    char tg_txt[256], tf_txt[256];
    /* decode_stream mutates its input (de-glitch), so decode float first from a
     * copy-free g_dec, then fixed from f_dec. */
    decode_stream(g_dec, blocks, bpd, tg_txt, sizeof tg_txt);
    decode_stream(f_dec, blocks, bpd, tf_txt, sizeof tf_txt);

    printf("message : \"%s\"\n", msg);
    printf("params  : fs=%d Hz  tone=%.0f Hz  wpm=%d  jitter=%.0f%%  N=%d "
           "(%.1f ms/block, ~%.0f Hz bin)\n",
           fs, tone, wpm, jitter*100, N, 1000.0f*N/fs, (float)fs/N);
    printf("noise   : wideband SNR = %+.1f dB   Goertzel processing gain ~ %+.1f dB"
           "  => in-filter SNR ~ %+.1f dB\n", snr_db, gain_db, snr_db + gain_db);
    printf("wav     : %s  (%.1f s, %d samples)\n", out, (float)total/fs, (int)total);
    printf("\n");
    printf("block accuracy vs truth:   float %5.1f%%   FIXED %5.1f%%   broadband(REG_6F) %5.1f%%\n",
           100.0*g_ok/blocks, 100.0*f_ok/blocks, 100.0*b_ok/blocks);
    printf("float vs fixed agreement:  %5.1f%%  (%ld/%ld blocks)\n",
           100.0*agree/blocks, agree, blocks);
    printf("decoded (float): \"%s\"\n", tg_txt);
    printf("decoded (FIXED): \"%s\"\n", tf_txt);

    free(key); free(x); free(g_dec); free(f_dec); free(b_dec); free(truth);
    return 0;
}
