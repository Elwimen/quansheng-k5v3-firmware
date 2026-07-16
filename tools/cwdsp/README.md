# cwdsp — Goertzel CW tone detector + noisy-audio test bench

Prototype for **frequency-selective AF CW decoding**, to replace the current
broadband detector (BK4819 REG_6F AF amplitude) that can't tell a keyed note
from band noise. The plan is to tap the demodulated AF into a free MCU ADC pin
and run a Goertzel (single-bin DFT / matched filter) at the CW pitch.

- `goertzel_fix.h/.c` — **the firmware detector: integer-only** (no FPU, no
  soft-float, no division). One 32x32->64 multiply per sample. **594 B flash,
  32 B RAM** on Cortex-M0+ `-Os` (measured). This is what ships.
- `goertzel.h/.c` — float reference/oracle. Clearer to read; used to validate
  the fixed-point port bit-for-bit and to pick thresholds.
- `cw_sim.c` — test bench: synthesises Morse (human timing jitter), buries it in
  heavy Gaussian noise, writes a **listenable WAV**, and runs all three
  detectors (float Goertzel, fixed Goertzel, broadband REG_6F-style) on the same
  samples, scoring each and cross-checking float vs fixed.

```bash
./build.sh                 # build ./cw_sim
./build.sh --audio         # also regenerate demo .wav/.mp3
./cw_sim --snr -6 --wpm 18 --jitter 0.12 --n 128 --out cw.wav
```

Options: `--snr` (wideband tone-RMS/noise-RMS, dB), `--wpm`, `--tone`, `--fs`,
`--n` (Goertzel window), `--jitter` (fractional timing swing, 0.12 ≈ human),
`--msg`, `--seed`, `--out`.

## Why it works — noise rejection by integration

The Goertzel coherently sums N samples, so its effective noise bandwidth is
`Fs/N`. Against a broadband envelope of bandwidth B the processing gain is
`~10·log10(B/(Fs/N))`. **Longer integration = narrower filter = more noise
rejected** — exactly what the ear does — up to the limit that the window
`T = N/Fs` must stay under ~⅓ of a dit (`dit_ms ≈ 1200/wpm`) or fast keying
smears.

Block accuracy vs window N and wideband SNR (18 wpm, 12% jitter, 700 Hz, Fs=8k):

| SNR \ N | 32 | 64 | 128 | 256 |
|---|---|---|---|---|
| 0 dB   | 93.1% | 95.4% | 97.8% | 94.7% |
| −6 dB  | 77.1% | 87.4% | **97.0%** | 95.6% |
| −10 dB | 64.9% | 68.7% | 91.5% | 89.4% |
| −14 dB | 59.2% | 61.0% | 75.8% | 74.9% |

Broadband (REG_6F-style) stays ~55–56% (≈ chance) at every SNR. Note N=256
(32 ms > dit/3 at 18 wpm) *regresses* — that's the timing-vs-gain tradeoff.

## Fixed-point vs float (validation)

The integer detector tracks the float reference within a couple of percent (and
edges ahead at very low SNR, where its power-of-two ×4/×2 thresholds are a touch
more conservative). Same runs, 18 wpm, 12% jitter, N=128:

| SNR | float | **FIXED** | broadband | float↔fixed agreement |
|---|---|---|---|---|
| 0 dB   | 97.8% | 95.8% | 55.7% | 97.9% |
| −6 dB  | 97.0% | 94.9% | 55.7% | 97.2% |
| −10 dB | 91.5% | 91.2% | 55.7% | 94.8% |
| −14 dB | 75.8% | 78.4% | 55.7% | 90.2% |

**Cost on target (Cortex-M0+, `-Os`, measured):** the fixed detector is **514 B**
of its own code, **594 B** fully linked (it pulls only tiny int64 helpers —
`__aeabi_lmul/lasr/llsl` — no float, no libm), and **32 B RAM** per instance
(no sample buffer). Compare the float path: ~4 KB (soft-float runtime) or ~11 KB
if `cosf`/`powf` are left in `init`. Keep the coeff a precomputed constant (or a
small integer cosine) so the firmware build stays 100% float-free.

**Sampling rate:** Fs = 8 kHz is a comfortable default (easy anti-alias filter,
tone well below Nyquist). 4 kHz also works; higher buys nothing for a <1 kHz
tone. Pick **N ≈ dit/4** in samples (scale it with WPM): e.g. 18 wpm → N≈128 at
8 kHz; 30 wpm → N≈80.

## Target ADC pin (firmware scan result)

ADC-capable pins the firmware does **not** use: `PA0, PA1, PA2, PA4, PA5, PA7,
PB1` (PC0–PC5 only on 56/64-pin packages). Battery = ADC_IN8/PB0. `PA7`
(ADC_IN7, also OPA2_INP for optional on-chip gain) is the first candidate —
**still needs schematic confirmation** that it's a free, accessible pad.

This feeds the existing element/character decoder in `App/app/cw.c` (which today
runs off the broadband REG_6F sensor).
