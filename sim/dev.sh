#!/usr/bin/env bash
#
# One command for the GUI loop: rebuild the firmware, get it running in the
# simulator, wait until the radio actually answers, and leave the web viewer up.
#
#   ./sim/dev.sh             # build, then reload a running sim (or start one)
#   ./sim/dev.sh --no-build   # skip the build
#   ./sim/dev.sh --restart    # force a cold start even if a sim is running
#
# Reload keeps the same Renode process, so the PTY survives and the browser stays
# connected: edit, run this, watch the screen come back. Open http://localhost:8088/
# and click Connect once.
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

# The radio's configuration -- channels, settings, calibration, everything CHIRP writes --
# lives in the SPI flash image, and the simulator mirrors the firmware's writes back into
# it, so the radio remembers across restarts. Point these elsewhere for a throwaway radio
# (the UI tests do, so they get a pristine one and never touch your configuration).
FLASH_IMAGE=$(realpath -m "${FLASH_IMAGE:-sim/data/spi_PY25Q16.bin}")
EEPROM_IMAGE=$(realpath -m "${EEPROM_IMAGE:-sim/data/eeprom.bin}")

step() { printf '  %-24s' "$1"; }
ok()   { printf 'ok%s\n' "${1:+ ($1)}"; }

BUILD=1; FORCE_RESTART=0; VIEWER=1
for arg in "$@"; do
    case "$arg" in
        --no-build)  BUILD=0 ;;
        --restart)   FORCE_RESTART=1 ;;
        --no-viewer) VIEWER=0 ;;   # headless: CI drives the radio with uvctl, not a browser
        *) echo "unknown option: $arg"; exit 1 ;;
    esac
done

# Talk to the monitor of an already-running sim.
monitor() {
    python3 - "$MONITOR_PORT" "$@" <<'PY'
import socket, sys, time
port, cmds = int(sys.argv[1]), sys.argv[2:]
try:
    s = socket.create_connection(("127.0.0.1", port), timeout=2)
except OSError:
    sys.exit(1)
# Read until the monitor prints its prompt again rather than draining on a timeout:
# every command is near-instant, and waiting one timeout each made a reload take 22s.
s.settimeout(15)
PROMPT = "(uvk5v3)"
for c in ["mach set 0"] + cmds:
    s.sendall((c + "\n").encode())
    seen = ""
    while PROMPT not in seen:
        try:
            data = s.recv(65536)
        except OSError:
            break
        if not data:
            break
        seen += data.decode(errors="replace")
s.close()
PY
}

# A sim we can reload: monitor answering *and* its PTY still there.
sim_is_live() {
    [[ -e "$PTY" ]] && monitor "version" 2>/dev/null
}

# Does the bridge currently hold the port open, i.e. is a viewer watching? If so we
# must not open the port ourselves -- two readers would split the byte stream between
# them and the viewer would lose the frames it needs.
viewer_attached() {
    local pid
    pid=$(pgrep -f "sim/webviewer/bridge.py" | head -1) || return 1
    [[ -n "$pid" ]] && ls -l "/proc/$pid/fd" 2>/dev/null | grep -q "pts/"
}

if [[ $BUILD -eq 1 ]]; then
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

RELOADED=0
if [[ $FORCE_RESTART -eq 0 ]] && sim_is_live; then
    # Fast path: keep the Renode process. Its reset macro re-runs LoadELF, so the
    # machine comes back on the freshly built binary -- and because the PTY belongs to
    # the process, not the machine, it survives: the bridge keeps its port and the
    # browser stays connected across the reload.
    step "reloading firmware"
    start=$SECONDS
    # Memory does not clear on reset (MappedMemory.Reset is a no-op), so re-seed the
    # stores by hand; otherwise a reload inherits whatever the last run wrote to flash.
    monitor "pause" \
            "machine Reset" \
            "sysbus LoadBinary @${FLASH_IMAGE} 0x90000000" \
            "sysbus LoadBinary @${EEPROM_IMAGE} 0x90200000" \
            "start"
    RELOADED=1
    ok "$((SECONDS - start))s"
else
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
    -e '\$flashImage=@${FLASH_IMAGE}; \$eepromImage=@${EEPROM_IMAGE}; include @sim/scripts/run.resc; logLevel 3; cpu PerformanceInMips ${MIPS}; start'" \
    > "$LOG_DIR/renode.log" 2>&1 &
    for _ in $(seq 90); do
        [[ -e "$PTY" ]] && break
        sleep 1
    done
    [[ -e "$PTY" ]] || { echo "FAILED — no $PTY"; tail -20 "$LOG_DIR/renode.log"; exit 1; }
    ok
fi

step "waiting for boot"
start=$SECONDS
if viewer_attached; then
    # The bridge owns the port on behalf of a watching browser. Opening it here would
    # split the byte stream between two readers and steal the frames the viewer needs,
    # so let the viewer be the one that talks to the radio: it re-syncs on its own,
    # because after the reset the firmware treats the next keepalive as a new host and
    # answers with a full frame.
    sleep 4
    ok "$((SECONDS - start))s, viewer kept"
else
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
fi

if [[ $RELOADED -eq 0 && $VIEWER -eq 1 ]]; then
    # Restart the bridge with the sim: it holds a serial fd to the PTY the restart just
    # destroyed, and a stale one leaves the viewer "connected" to nothing. A reload
    # keeps the PTY, so the bridge (and the browser) stay as they are.
    step "starting viewer bridge"
    pkill -f "sim/webviewer/bridge.py" 2>/dev/null || true
    nohup python3 -u sim/webviewer/bridge.py > "$LOG_DIR/bridge.log" 2>&1 &
    sleep 1
    ok
fi

echo
[[ $VIEWER -eq 1 ]] && echo "  viewer:   http://localhost:${HTTP_PORT}/   (click Connect)"
echo "  monitor:  telnet localhost ${MONITOR_PORT}   serial: ${PTY}   gdb: :3333"
echo "  logs:     ${LOG_DIR}"
