#!/bin/sh
# Build tiro_core for Windows with the portable llvm-mingw toolchain (no Visual Studio needed).
#   sh core/build-win.sh [x64|arm64]
set -e
ARCH=${1:-x64}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TOOLS=$ROOT/work/tools
export PATH="$TOOLS/llvm-mingw-20260922-ucrt-x86_64/bin:$TOOLS/cmake-4.4.3-windows-x86_64/bin:$PATH"
if [ "$ARCH" = "arm64" ]; then TRIPLE=aarch64-w64-mingw32; else TRIPLE=x86_64-w64-mingw32; fi
BUILD="$ROOT/build/core-win-$ARCH"
cmake -S "$ROOT/core" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=$TRIPLE-clang -DCMAKE_CXX_COMPILER=$TRIPLE-clang++ \
  -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_SYSTEM_PROCESSOR=$ARCH \
  -DORT_ROOT="$TOOLS/onnxruntime-win-$ARCH-1.30.0"
cmake --build "$BUILD" -j ${JOBS:-6}
cp "$TOOLS/onnxruntime-win-$ARCH-1.30.0/lib/onnxruntime.dll" "$BUILD/"
echo "built $BUILD"
