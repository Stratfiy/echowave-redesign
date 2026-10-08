#!/bin/sh
# Reproduce dfn3_streaming.onnx from DeepFilterNet's official v0.5.6 checkpoint.
#
#   ./build.sh OUT_DIR
#
# Needs python3 with torch (CPU), onnx and onnxruntime. Fetches the checkpoint
# (sha256-pinned) and the deepfilternet 0.5.6 package source (no dependencies:
# its Rust extension, libdf, is replaced by libdf_stub/, a numpy port checked
# against the real one by verify.py). Used by the API Dockerfile's
# WITH_VOICE_ISOLATION stage and by hand; never at run time.
set -eu
OUT="$(cd "${1:?usage: build.sh OUT_DIR}" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HERE/work"
mkdir -p "$WORK"
cd "$WORK"

CKPT_URL=https://raw.githubusercontent.com/Rikorose/DeepFilterNet/v0.5.6/models/DeepFilterNet3.zip
CKPT_SHA=49c52edc8947ae1f9bf50d81530beaf3a2c3245aeaf34b6f31ff535cd22284d2
curl -fsSL --retry 3 -o DeepFilterNet3.zip "$CKPT_URL"
echo "$CKPT_SHA  DeepFilterNet3.zip" | sha256sum -c -
python3 -c "import zipfile; zipfile.ZipFile('DeepFilterNet3.zip').extractall('.')"

python3 -m pip download --quiet --no-deps --dest wheel deepfilternet==0.5.6
python3 -m pip install --quiet --no-deps --target pkg wheel/deepfilternet-0.5.6-*.whl

PYTHONPATH="$WORK/pkg" DFN_MODEL_DIR="$WORK/DeepFilterNet3" \
    DFN_ONNX_OUT="$OUT/dfn3_streaming.onnx" python3 "$HERE/export_streaming.py"
