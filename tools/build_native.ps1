# Build Tiro's native pieces that the Windows app bundles: tiro_hook.dll (global hotkey on its own thread) and the
# core logic tests. Uses the portable llvm-mingw toolchain in work\tools when it's there (this PC), otherwise
# CMake's default Visual Studio generator (GitHub Actions). Output: build\native\.
#   powershell -ExecutionPolicy Bypass -File tools\build_native.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "build\native"
New-Item -ItemType Directory -Force $out | Out-Null
$tools = Join-Path $root "work\tools"
$mingw = Get-ChildItem $tools -Directory -Filter "llvm-mingw-*" -ErrorAction SilentlyContinue | Select-Object -First 1
$cmakeDir = Get-ChildItem $tools -Directory -Filter "cmake-*" -ErrorAction SilentlyContinue | Select-Object -First 1
$build = Join-Path $root "build\native-build"
if ($mingw -and $cmakeDir) {
    $env:PATH = (Join-Path $mingw.FullName "bin") + ";" + (Join-Path $cmakeDir.FullName "bin") + ";" + $env:PATH
    cmake -S (Join-Path $root "core") -B $build -G Ninja -DCMAKE_BUILD_TYPE=Release `
        -DCMAKE_C_COMPILER=x86_64-w64-mingw32-clang -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-clang++ `
        -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_SYSTEM_PROCESSOR=x64
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }
    cmake --build $build --target tiro_hook tiro-tests -j 4
    if ($LASTEXITCODE -ne 0) { throw "native build failed" }
    $bin = $build
} else {
    cmake -S (Join-Path $root "core") -B $build -A x64
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }
    cmake --build $build --config Release --target tiro_hook tiro-tests
    if ($LASTEXITCODE -ne 0) { throw "native build failed" }
    $bin = Join-Path $build "Release"
}
& (Join-Path $bin "tiro-tests.exe")
if ($LASTEXITCODE -ne 0) { throw "native logic tests failed" }
$dll = Get-ChildItem $bin -Filter "*tiro_hook.dll" | Select-Object -First 1
Copy-Item $dll.FullName (Join-Path $out "tiro_hook.dll") -Force
Write-Host "native: $out\tiro_hook.dll"
