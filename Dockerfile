FROM debian:bookworm-slim

# ---------------------------------------------
# Install required packages
# ---------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build python3 curl xz-utils ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------
# Download and install ARM GCC toolchain
# Supports both Apple Silicon (arm64) and Intel (amd64)
# ---------------------------------------------
ARG TARGETARCH
ARG ARM_GCC_VERSION=13.3.rel1

RUN set -eux; \
    if [ "${TARGETARCH}" = "arm64" ]; then HOSTARCH="aarch64"; else HOSTARCH="x86_64"; fi; \
    TARBALL="arm-gnu-toolchain-${ARM_GCC_VERSION}-${HOSTARCH}-arm-none-eabi.tar.xz"; \
    URL="https://armkeil.blob.core.windows.net/developer/Files/downloads/gnu/${ARM_GCC_VERSION}/binrel/${TARBALL}"; \
    echo "Downloading ${URL}"; \
    curl -fL -O "${URL}"; \
    tar -xf "${TARBALL}"; \
    rm "${TARBALL}"; \
    DIR="$(ls -d arm-gnu-toolchain-${ARM_GCC_VERSION}-*-arm-none-eabi)"; \
    mv "${DIR}" /opt/toolchain

# ---------------------------------------------
# Add toolchain to PATH
# ---------------------------------------------
ENV PATH="/opt/toolchain/bin:${PATH}"

# ---------------------------------------------
# Set working directory
# ---------------------------------------------
WORKDIR /src
