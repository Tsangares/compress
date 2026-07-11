#!/usr/bin/env bash
# Generate test fixture videos into tests/fixtures/. Idempotent: skips any
# file that already exists with nonzero size.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURES="$HERE/fixtures"
mkdir -p "$FIXTURES"

need() {
    local path="$1"
    if [[ -s "$path" ]]; then
        echo "[fixtures] skip (exists): $path"
        return 1
    fi
    return 0
}

# small.mp4: 12s, 640x360, 30fps, testsrc2 + sine audio, 3s GOP (-g 90) so
# trim cuts land mid-GOP, libx264+aac, faststart.
SMALL="$FIXTURES/small.mp4"
if need "$SMALL"; then
    echo "[fixtures] generating small.mp4"
    ffmpeg -hide_banner -loglevel error -y \
        -f lavfi -i "testsrc2=size=640x360:rate=30:duration=12" \
        -f lavfi -i "sine=frequency=440:duration=12" \
        -c:v libx264 -pix_fmt yuv420p -g 90 -preset veryfast -crf 23 \
        -c:a aac -b:a 128k \
        -movflags +faststart \
        -shortest \
        "$SMALL"
fi

# noaudio.mp4: same as small.mp4 but with no audio track.
NOAUDIO="$FIXTURES/noaudio.mp4"
if need "$NOAUDIO"; then
    echo "[fixtures] generating noaudio.mp4"
    ffmpeg -hide_banner -loglevel error -y \
        -f lavfi -i "testsrc2=size=640x360:rate=30:duration=12" \
        -c:v libx264 -pix_fmt yuv420p -g 90 -preset veryfast -crf 23 \
        -an \
        -movflags +faststart \
        "$NOAUDIO"
fi

# big.mp4: >50MB to trigger the app's server-side compression path.
BIG="$FIXTURES/big.mp4"
gen_big() {
    local duration="$1"
    local bitrate="$2"
    echo "[fixtures] generating big.mp4 (duration=${duration}s bitrate=${bitrate})"
    ffmpeg -hide_banner -loglevel error -y \
        -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=${duration}" \
        -f lavfi -i "sine=frequency=440:duration=${duration}" \
        -c:v libx264 -pix_fmt yuv420p -preset veryfast \
        -b:v "${bitrate}" -maxrate "${bitrate}" -bufsize 16M \
        -c:a aac -b:a 128k \
        -movflags +faststart \
        -shortest \
        "$BIG"
}

if need "$BIG"; then
    gen_big 70 8M
    size=$(stat -c%s "$BIG" 2>/dev/null || stat -f%z "$BIG")
    threshold=52428800
    attempt=1
    while [[ "$size" -le "$threshold" && $attempt -lt 4 ]]; do
        attempt=$((attempt + 1))
        echo "[fixtures] big.mp4 too small ($size bytes) — bumping bitrate/duration (attempt $attempt)"
        gen_big $((70 + attempt * 20)) "$((8 + attempt * 2))M"
        size=$(stat -c%s "$BIG" 2>/dev/null || stat -f%z "$BIG")
    done
    if [[ "$size" -le "$threshold" ]]; then
        echo "[fixtures] ERROR: big.mp4 still <=50MB after retries ($size bytes)" >&2
        exit 1
    fi
    echo "[fixtures] big.mp4 final size: $size bytes"
fi

echo "[fixtures] done"
