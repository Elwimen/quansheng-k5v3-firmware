#!/usr/bin/env bash
#
# Debug the firmware in GDB: breakpoints, stepping, registers, memory.
#
#   ./sim/debug.sh                     # build, load the sim, attach GDB
#   ./sim/debug.sh --no-build
#   ./sim/debug.sh -ex "break APP_Update" -ex "continue"   # extra GDB commands
#
# GDB drives the emulation. The machine is loaded but deliberately *not* started:
# Renode's GDB server is set up with autostartEmulation, so GDB starts it on attach and
# owns the CPU. Attach to an already-running machine instead (as ./sim/dev.sh leaves it)
# and GDB reports "target is running" and refuses to break or step, because Renode never
# halts the core for it. That is why this is a separate entry point.
#
# `continue` runs the radio; Ctrl-C halts it. The web viewer is served as usual, so you
# can watch the screen freeze on a breakpoint -- but note the firmware's serial stops
# being answered while the CPU is halted.
set -euo pipefail

cd "$(dirname "$0")/.."

PRESET=Fusion
ELF="build/${PRESET}/f4hwn.fusion.elf"
PTY=/tmp/ttyUV0
MONITOR_PORT=4567
GDB_PORT=3333
MIPS=${MIPS:-10}
# Same radio image as dev.sh, so GDB debugs the radio you have been using -- not a
# different one (override with FLASH_IMAGE=, as the tests do).
FLASH_IMAGE=$(realpath -m "${FLASH_IMAGE:-sim/data/spi_PY25Q16.bin}")
LOG_DIR="${TMPDIR:-/tmp}/uvk5-sim"
GDB=${GDB:-arm-none-eabi-gdb}
mkdir -p "$LOG_DIR"

BUILD=1
GDB_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-build) BUILD=0; shift ;;
        -ex) GDB_ARGS+=(-ex "$2"); shift 2 ;;
        *) echo "unknown option: $1"; exit 1 ;;
    esac
done

command -v "$GDB" > /dev/null || {
    echo "$GDB not found — install arm-none-eabi-gdb (Arch: pacman -S arm-none-eabi-gdb)"
    exit 1
}

if [[ $BUILD -eq 1 ]]; then
    printf '  %-24s' "building ${PRESET}"
    [[ -d "build/${PRESET}" ]] || cmake --preset "$PRESET" > "$LOG_DIR/cmake.log" 2>&1
    if ! cmake --build --preset "$PRESET" -j > "$LOG_DIR/build.log" 2>&1; then
        printf 'FAILED\n\n'; tail -25 "$LOG_DIR/build.log"; exit 1
    fi
    printf 'ok\n'
fi
[[ -f "$ELF" ]] || { echo "no firmware at $ELF — build first"; exit 1; }

printf '  %-24s' "loading simulator"
pkill -f "renode.*run.resc" 2>/dev/null || true
for _ in $(seq 30); do
    pgrep -f "renode.*run.resc" > /dev/null || break
    sleep 0.5
done
rm -f "$PTY"
# No `start`: GDB starts the emulation when it attaches (see run.resc).
nohup sh -c "tail -f /dev/null | renode --disable-xwt --port ${MONITOR_PORT} \
    -e '\$flashImage=@${FLASH_IMAGE}; include @sim/scripts/run.resc; logLevel 3; cpu PerformanceInMips ${MIPS}'" \
    > "$LOG_DIR/renode.log" 2>&1 &
for _ in $(seq 60); do
    [[ -e "$PTY" ]] && break
    sleep 1
done
[[ -e "$PTY" ]] || { echo "FAILED"; tail -20 "$LOG_DIR/renode.log"; exit 1; }
printf 'ok\n'

if ! pgrep -f "sim/webviewer/bridge.py" > /dev/null; then
    nohup python3 -u sim/webviewer/bridge.py > "$LOG_DIR/bridge.log" 2>&1 &
fi

echo
echo "  GDB drives the emulation: 'continue' runs the radio, Ctrl-C halts it."
echo "  viewer: http://localhost:8088/   monitor: telnet localhost ${MONITOR_PORT}"
echo
exec "$GDB" -q "$ELF" \
    -ex "target remote :${GDB_PORT}" \
    "${GDB_ARGS[@]}"
