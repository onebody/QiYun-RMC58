#!/usr/bin/env bash
# build_release.sh — Build all PlatformIO environments, collect .bin artifacts
# into firmware/dist/ with version-prefixed filenames.
#
# Usage:
#   ./build_release.sh                  # auto version from git tag or date
#   ./build_release.sh v1.2.3           # explicit version
#
# Output:
#   firmware/dist/v1.2.3/
#     QiYun-RMC58_C5-scanner_v1.2.3_firmware.factory.bin
#     QiYun-RMC58_C5-head_v1.2.3_firmware.factory.bin
#     QiYun-RMC58_C5-scanner-W5500_v1.2.3_firmware.factory.bin
#     QiYun-RMC58_S3-relay_v1.2.3_firmware.factory.bin
#     QiYun-RMC58_S3-relay-standalone_v1.2.3_firmware.factory.bin
#     firmware.factory.bin           # (just firmware, no bootloader)
#     bootloader.bin
#     partitions.bin
#     sha256sums.txt
#
# GPL-3.0-or-later.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIRMWARE_DIR="$SCRIPT_DIR/firmware"
cd "$FIRMWARE_DIR"

# -------- version --------
if [[ -n "${1:-}" ]]; then
    VERSION="$1"
else
    VERSION="$(git describe --tags --always --dirty 2>/dev/null || echo "dev")"
fi
if [[ "$VERSION" != v* ]]; then
    VERSION="v${VERSION}"
fi

echo "=== Building version $VERSION ==="

# -------- env list --------
# env name → release asset label
declare -A ENVS=(
    [esp32c5]="C5-scanner"
    [esp32c5-head]="C5-head"
    [esp32c5-eth]="C5-scanner-W5500"
    [esp32s3]="S3-relay-standalone"
    [esp32s3-relay]="S3-relay"
)

DIST_DIR="$FIRMWARE_DIR/dist/$VERSION"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

FAILED=()
for env in "${!ENVS[@]}"; do
    label="${ENVS[$env]}"
    echo ""
    echo "===== [$env] ${label} ====="
    set +e
    build_out=$(pio run -e "$env" 2>&1)
    rc=$?
    set -e
    if [[ $rc -eq 0 ]] && echo "$build_out" | grep -q "SUCCESS"; then
        echo "  ✅ $env build OK"
        src=".pio/build/$env/firmware.factory.bin"
        if [[ -f "$src" ]]; then
            dst="$DIST_DIR/QiYun-RMC58_${label}_${VERSION}_firmware.factory.bin"
            cp "$src" "$dst"
            size=$(stat -f%z "$dst" 2>/dev/null || stat -c%s "$dst" 2>/dev/null || echo "?")
            echo "  📦 $(basename "$dst")  ($size bytes)"
        else
            echo "  ⚠️  firmware.factory.bin not found for $env"
            FAILED+=("$env: missing artifact")
        fi
    else
        echo "  ❌ $env FAILED (rc=$rc)"
        echo "$build_out" | tail -5
        FAILED+=("$env")
    fi
done

# -------- checksums --------
if compgen -G "$DIST_DIR/*.bin" > /dev/null; then
    (cd "$DIST_DIR" && shasum -a 256 *.bin > sha256sums.txt)
    echo "  📄 sha256sums.txt generated"
fi

echo ""
echo "==================== RESULT ===================="
echo "  Version:  $VERSION"
echo "  Output:   $DIST_DIR"
echo "  Assets:   $(ls "$DIST_DIR"/*.bin 2>/dev/null | wc -l | tr -d ' ') .bin files"
if [[ ${#FAILED[@]} -eq 0 ]]; then
    echo "  Status:   ✅ ALL BUILDS OK"
    exit 0
else
    echo "  Status:   ❌ ${#FAILED[@]} failed: ${FAILED[*]}"
    exit 1
fi
