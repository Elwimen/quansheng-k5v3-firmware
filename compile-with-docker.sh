#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------
# Usage:
#   ./compile-with-docker.sh [Preset]
# Example:
#   ./compile-with-docker.sh Custom
#   ./compile-with-docker.sh Bandscope
#   ./compile-with-docker.sh Broadcast
#   ./compile-with-docker.sh Basic
#   ./compile-with-docker.sh RescueOps
#   ./compile-with-docker.sh Game
# Default preset: "Custom"
# ---------------------------------------------

IMAGE=uvk1-uvk5v3
PRESET=${1:-Custom}

# ---------------------------------------------
# Validate preset name
# ---------------------------------------------
if [[ ! "$PRESET" =~ ^(Custom|Bandscope|Broadcast|Basic|RescueOps|Game)$ ]]; then
  echo "❌ Unknown preset: '$PRESET'"
  echo "Valid presets are: Custom, Bandscope, Broadcast, Basic, RescueOps, Game"
  exit 1
fi

# ---------------------------------------------
# Build the Docker image (only needed once)
# ---------------------------------------------
docker build -t "$IMAGE" .

# ---------------------------------------------
# Clean existing CMake cache to ensure toolchain reload
# ---------------------------------------------
rm -rf build

# ---------------------------------------------
# Run CMake inside the container with the selected preset
# ---------------------------------------------
docker run --rm -it -v "$PWD":/src -w /src "$IMAGE" \
  bash -c "which arm-none-eabi-gcc && arm-none-eabi-gcc --version && \
           echo '=== Building preset: ${PRESET} ===' && \
           cmake --preset ${PRESET} && \
           cmake --build --preset ${PRESET} -j"
