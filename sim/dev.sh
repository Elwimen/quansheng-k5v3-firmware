#!/usr/bin/env bash
#
# One command for the GUI loop: rebuild the firmware, restart the simulator on the
# fresh ELF, wait until the radio actually answers, and leave the web viewer up.
#
#   ./sim/dev.sh            # build + restart + wait
#   ./sim/dev.sh --no-build # just restart the sim on the current ELF
#
# Then open http://localhost:8088/ and click Connect. Leave the page open across
# runs: when the simulator restarts, reconnect it (the bridge keeps its port).
set -euo pipefail

cd "$(dirname "$0")/.."

PRESET=Fusion
ELF="build/${PRESET}/f4hwn.fusion.elf"
PTY=/tmp/ttyUV0
MONITOR_PORT=4567
HTTP_PORT=8088
MIPS=${MIPS:-10}          # see PerformanceInMips note below; raise for timing fidelity
LOG_DIR="${TMPDIR:-/tmp}/uvk5-sim"
mkdir -p "$LOG_DIR"

step() { printf '  %-24s' "$1"; }
ok()   { printf 'ok%s\n' "${1:+ ($1)}"; }

if [[ "${1:-}" != "--no-build" ]]; then
    step "building ${PRESET}"
    start=$SECONDS
    [[ -d "build/${PRESET}" ]] || cmake --preset "$PRESET" > "$LOG_DIR/cmake.log" 2>&1
    if ! cmake --build --preset "$PRESET" -j > "$LOG_DIR/build.log" 2>&1; then
        printf 'FAILED\n\n'
        tail -25 "$LOG_DIR/build.log"
        exit 1
    fi
    ok "$((SECONDS - start))s"
fi
[[ -f "$ELF" ]] || { echo "no firmware at $ELF — build first"; exit 1; }

step "restarting renode"
pkill -f "renode.*run.resc" 2>/dev/null || true
# pkill only signals: wait for the old instance to actually go, or it still owns the
# monitor port -- and drop the stale symlink so we can't mistake it for the new PTY.
for _ in $(seq 30); do
    pgrep -f "renode.*run.resc" > /dev/null || break
    sleep 0.5
done
rm -f "$PTY"
# Renode's monitor exits on EOF, so hold its stdin open with a pipe that never closes.
# The peripheral tags log a warning per access, which is megabytes a minute: errors only.
#
# PerformanceInMips trades emulated CPU speed for wall-clock speed: virtual time
# advances as instructions/MIPS, so a *lower* value gets more simulated time out of
# the same host work. At the default 100 the firmware's 2.55s boot screen costs ~60s
# of real time; at 10 it costs ~2s, and a CHIRP round trip drops from minutes to
# seconds. The emulated core is then slower relative to its timers than the real
# PY32 -- fine for UI work (screen, keys and a 122-channel CHIRP round trip all
# verified), but use run.resc directly if you are chasing a timing-sensitive bug.
nohup sh -c "tail -f /dev/null | renode --disable-xwt --port ${MONITOR_PORT} \
    -e 'include @sim/scripts/run.resc; logLevel 3; cpu PerformanceInMips ${MIPS}; start'" \
    > "$LOG_DIR/renode.log" 2>&1 &
for _ in $(seq 90); do
    [[ -e "$PTY" ]] && break
    sleep 1
done
[[ -e "$PTY" ]] || { echo "FAILED — no $PTY"; tail -20 "$LOG_DIR/renode.log"; exit 1; }
ok

# Renode compiles the C# models at load and then runs slower than real time, so the
# firmware's 2.55s boot screen takes tens of seconds of wall clock. Poll rather than
# guess: a host tool that opens the port too early just sees a dead UART.
step "waiting for boot"
start=$SECONDS
python3 - "$PTY" <<'PY' || { echo "FAILED — radio never answered"; exit 1; }
import sys, time, serial

SUSTAINED = 2.0   # seconds of unbroken streaming before we call it booted
GAP       = 3.0   # a silence longer than this means it was not the real thing

port = sys.argv[1]
deadline = time.time() + 240
with serial.Serial(port, 38400, timeout=0.3) as ser:
    # The firmware streams briefly during its boot screen and then goes quiet for the
    # rest of the boot, so the first frame proves nothing: wait for streaming that
    # *stays*. Handing a still-booting radio to the viewer leaves it blank.
    since = None
    last = 0.0
    while True:
        now = time.time()
        if now > deadline:
            sys.exit(1)
        ser.write(b"\x55\xAA\x00\x00")          # keepalive
        if ser.read(2048):
            last = now
            if since is None:
                since = now
            elif now - since >= SUSTAINED:
                break
        elif since is not None and now - last > GAP:
            since = None                        # that was the boot screen; keep waiting
        time.sleep(0.3)

    # The firmware only sends a full frame when it sees a *new* host (screenshot.c:
    # !wasConnected -> force), and only diffs after that. Our keepalives above made
    # it consider us the host, so hand over cleanly: go quiet until it stops
    # streaming (keepAlive lapses, wasConnected=false), and the viewer that connects
    # next gets a full frame instead of diffs against a screen it never received.
    ser.timeout = 0.5
    while ser.read(512):
        pass
PY
ok "$((SECONDS - start))s"

# Always restart the bridge: it holds a serial fd to the PTY the simulator just
# destroyed, and a stale one leaves the viewer "connected" to nothing.
step "starting viewer bridge"
pkill -f "sim/webviewer/bridge.py" 2>/dev/null || true
nohup python3 -u sim/webviewer/bridge.py > "$LOG_DIR/bridge.log" 2>&1 &
sleep 1
ok

echo
echo "  viewer:   http://localhost:${HTTP_PORT}/   (click Connect)"
echo "  monitor:  telnet localhost ${MONITOR_PORT}   serial: ${PTY}"
echo "  logs:     ${LOG_DIR}"
