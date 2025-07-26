#!/bin/sh

IMAGE_NAME="uvk5"
FIRMWARE_DIR="${PWD}/compiled-firmware"

# Create firmware output directory if it doesn't exist
mkdir -p "$FIRMWARE_DIR"

# Clean previously compiled firmware files
rm -f "$FIRMWARE_DIR"/*

# Clean up old Docker artifacts
echo "🧽 Cleaning up old Docker artifacts..."
docker system prune -f --volumes >/dev/null 2>&1 || true

# Always rebuild the Docker image to ensure latest code changes
echo "⚙️ Rebuilding Docker image '$IMAGE_NAME'..."
docker rmi "$IMAGE_NAME" 2>/dev/null || true
if ! docker build --build-arg BUILDPLATFORM=linux/amd64 -t "$IMAGE_NAME" .; then
    echo "❌ Failed to build docker image"
    exit 1
fi

# ------------------ BUILD VARIANTS ------------------

custom() {
    echo "🔧 Custom compilation..."
    docker run --rm -v "$FIRMWARE_DIR:/app/compiled-firmware" "$IMAGE_NAME" /bin/bash -c "\
        rm -f ./compiled-firmware/* && cd /app && make -s \
        EDITION_STRING=Custom \
        TARGET=f4hwn.custom \
        && cp f4hwn.custom* compiled-firmware/"
}

standard() {
    echo "📦 Standard compilation..."
    docker run --rm -v "$FIRMWARE_DIR:/app/compiled-firmware" "$IMAGE_NAME" /bin/bash -c "\
        rm -f ./compiled-firmware/* && cd /app && make -s \
        ENABLE_SPECTRUM=0 \
        ENABLE_FMRADIO=0 \
        ENABLE_AIRCOPY=0 \
        ENABLE_NOAA=0 \
        EDITION_STRING=Standard \
        TARGET=f4hwn.standard \
        && cp f4hwn.standard* compiled-firmware/"
}

bandscope() {
    echo "📺 Bandscope compilation..."
    docker run --rm -v "$FIRMWARE_DIR:/app/compiled-firmware" "$IMAGE_NAME" /bin/bash -c "\
        rm -f ./compiled-firmware/* && cd /app && make -s \
        ENABLE_SPECTRUM=1 \
        ENABLE_FMRADIO=0 \
        ENABLE_VOX=0 \
        ENABLE_AIRCOPY=1 \
        ENABLE_FEAT_F4HWN_SCREENSHOT=1 \
        ENABLE_FEAT_F4HWN_GAME=0 \
        ENABLE_FEAT_F4HWN_PMR=1 \
        ENABLE_FEAT_F4HWN_GMRS_FRS_MURS=1 \
        ENABLE_NOAA=0 \
        ENABLE_FEAT_F4HWN_RESCUE_OPS=0 \
        EDITION_STRING=Bandscope \
        TARGET=f4hwn.bandscope \
        && cp f4hwn.bandscope* compiled-firmware/"
}

broadcast() {
    echo "📻 Broadcast compilation..."
    docker run --rm -v "$FIRMWARE_DIR:/app/compiled-firmware" "$IMAGE_NAME" /bin/bash -c "\
        cd /app && make -s \
        ENABLE_SPECTRUM=0 \
        ENABLE_FMRADIO=1 \
        ENABLE_VOX=1 \
        ENABLE_AIRCOPY=1 \
        ENABLE_FEAT_F4HWN_SCREENSHOT=1 \
        ENABLE_FEAT_F4HWN_GAME=0 \
        ENABLE_FEAT_F4HWN_PMR=1 \
        ENABLE_FEAT_F4HWN_GMRS_FRS_MURS=1 \
        ENABLE_NOAA=0 \
        ENABLE_FEAT_F4HWN_RESCUE_OPS=0 \
        EDITION_STRING=Broadcast \
        TARGET=f4hwn.broadcast \
        && cp f4hwn.broadcast* compiled-firmware/"
}

basic() {
    echo "☘️ Basic compilation..."
    docker run --rm -v "$FIRMWARE_DIR:/app/compiled-firmware" "$IMAGE_NAME" /bin/bash -c "\
        cd /app && make -s \
        ENABLE_SPECTRUM=1 \
        ENABLE_FMRADIO=1 \
        ENABLE_VOX=0 \
        ENABLE_AIRCOPY=0 \
        ENABLE_FEAT_F4HWN_GAME=0 \
        ENABLE_FEAT_F4HWN_SPECTRUM=0 \
        ENABLE_FEAT_F4HWN_PMR=1 \
        ENABLE_FEAT_F4HWN_GMRS_FRS_MURS=1 \
        ENABLE_NOAA=0 \
        ENABLE_AUDIO_BAR=0 \
        ENABLE_FEAT_F4HWN_RESUME_STATE=0 \
        ENABLE_FEAT_F4HWN_CHARGING_C=0 \
        ENABLE_FEAT_F4HWN_INV=1 \
        ENABLE_FEAT_F4HWN_CTR=0 \
        ENABLE_FEAT_F4HWN_NARROWER=0 \
        ENABLE_FEAT_F4HWN_RESCUE_OPS=0 \
        EDITION_STRING=Basic \
        TARGET=f4hwn.basic \
        && cp f4hwn.basic* compiled-firmware/"
}

rescueops() {
    echo "🚨 RescueOps compilation..."
    docker run --rm -v "$FIRMWARE_DIR:/app/compiled-firmware" "$IMAGE_NAME" /bin/bash -c "\
        cd /app && make -s \
        ENABLE_SPECTRUM=0 \
        ENABLE_FMRADIO=0 \
        ENABLE_VOX=1 \
        ENABLE_AIRCOPY=1 \
        ENABLE_FEAT_F4HWN_SCREENSHOT=1 \
        ENABLE_FEAT_F4HWN_GAME=0 \
        ENABLE_FEAT_F4HWN_PMR=1 \
        ENABLE_FEAT_F4HWN_GMRS_FRS_MURS=1 \
        ENABLE_NOAA=1 \
        ENABLE_FEAT_F4HWN_RESCUE_OPS=1 \
        EDITION_STRING=RescueOps \
        TARGET=f4hwn.rescueops \
        && cp f4hwn.rescueops* compiled-firmware/"
}

game() {
    echo "🎮 Game compilation..."
    docker run --rm -v "$FIRMWARE_DIR:/app/compiled-firmware" "$IMAGE_NAME" /bin/bash -c "\
        cd /app && make -s \
        ENABLE_SPECTRUM=0 \
        ENABLE_FMRADIO=1 \
        ENABLE_VOX=0 \
        ENABLE_AIRCOPY=1 \
        ENABLE_FEAT_F4HWN_GAME=1 \
        ENABLE_FEAT_F4HWN_PMR=1 \
        ENABLE_FEAT_F4HWN_GMRS_FRS_MURS=1 \
        ENABLE_NOAA=0 \
        ENABLE_FEAT_F4HWN_RESCUE_OPS=0 \
        EDITION_STRING=Game \
        TARGET=f4hwn.game \
        && cp f4hwn.game* compiled-firmware/"
}

# ------------------ MENU ------------------

case "$1" in
    custom) custom ;;
    standard) standard ;;
    bandscope) bandscope ;;
    broadcast) broadcast ;;
    basic) basic ;;
    rescueops) rescueops ;;
    game) game ;;
    all)
        bandscope
        broadcast
        basic
        rescueops
        game
        ;;
    *)
        echo "Usage: $0 {custom|standard|bandscope|broadcast|basic|rescueops|game|all}"
        exit 1
        ;;
esac
