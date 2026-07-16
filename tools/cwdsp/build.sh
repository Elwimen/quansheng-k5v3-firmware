#!/usr/bin/env bash
# Build the CW Goertzel test bench and (optionally) regenerate the demo audio.
set -e
cd "$(dirname "$0")"

cc -O2 -Wall -o cw_sim cw_sim.c goertzel.c goertzel_fix.c -lm
echo "built ./cw_sim"

if [ "$1" = "--audio" ]; then
    ./cw_sim --snr  -6 --wpm 18 --jitter 0.12 --n 128 --out cw_demo_-6dB.wav
    ./cw_sim --snr -10 --wpm 18 --jitter 0.15 --n 128 --out cw_demo_-10dB.wav
    ./cw_sim --snr   0 --wpm 18 --jitter 0.12 --n 96  --out cw_demo_0dB.wav
    if command -v lame >/dev/null; then
        for f in cw_demo_-6dB cw_demo_-10dB cw_demo_0dB; do
            lame --quiet --preset medium "$f.wav" "$f.mp3"
        done
        echo "wrote demo .wav + .mp3"
    fi
fi
