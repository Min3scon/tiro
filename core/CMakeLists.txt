cmake_minimum_required(VERSION 3.22)
project(tiro_core VERSION 0.1.0 LANGUAGES C CXX)

# Tiro's shared speech engine: one C++ library behind a small C API (include/tiro_core.h), used unchanged by
# every Lite app (Windows, Mac, iPhone/iPad, Android, Linux, web) and by the training/eval harness.

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

option(TIRO_BUILD_TOOLS "Build the command-line tools" ON)
option(TIRO_BUILD_TESTS "Build the unit tests" ON)
option(TIRO_SHARED "Build tiro_core as a shared library (DLL/.so/.dylib)" ON)
set(ORT_ROOT "" CACHE PATH "ONNX Runtime package root (contains include/ and lib/)")

if(ORT_ROOT)
  set(TIRO_ENGINE ON)
  find_path(ORT_INCLUDE onnxruntime_c_api.h PATHS ${ORT_ROOT}/include ${ORT_ROOT}/include/onnxruntime NO_DEFAULT_PATH REQUIRED)
  find_library(ORT_LIB NAMES onnxruntime PATHS ${ORT_ROOT}/lib NO_DEFAULT_PATH REQUIRED)
else()
  # without ONNX Runtime only the pieces that don't need it are built (hotkey DLL, logic tests)
  message(STATUS "ORT_ROOT not set: building the hotkey library and the tests only")
  set(TIRO_ENGINE OFF)
endif()

if(TIRO_ENGINE)

set(MOONSHINE third_party/moonshine)
add_library(tiro_moonshine STATIC
  ${MOONSHINE}/moonshine-streaming-model.cpp
  ${MOONSHINE}/bin-tokenizer.cpp
  ${MOONSHINE}/context-biaser.cpp
  ${MOONSHINE}/word-alignment.cpp
  ${MOONSHINE}/resampler.cpp
  ${MOONSHINE}/ort-utils.cpp
  ${MOONSHINE}/ort-utils-ep.cpp
  ${MOONSHINE}/moonshine-ort-allocator.cpp
  ${MOONSHINE}/debug-utils.cpp
  ${MOONSHINE}/file-utils.cpp
  ${MOONSHINE}/string-utils.cpp
)
target_include_directories(tiro_moonshine PUBLIC ${MOONSHINE} ${ORT_INCLUDE})
set_target_properties(tiro_moonshine PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(MSVC)
  target_compile_options(tiro_moonshine PRIVATE /W2)
else()
  target_compile_options(tiro_moonshine PRIVATE -w)
endif()

set(TIRO_SOURCES
  src/ort_env.cc
  src/moonshine_asr.cc
  src/vad.cc
  src/session.cc
  src/text.cc
  src/correct.cc
  src/ladder.cc
  src/c_api.cc
)
if(TIRO_SHARED)
  add_library(tiro_core SHARED ${TIRO_SOURCES})
  target_compile_definitions(tiro_core PRIVATE TIRO_BUILDING_DLL)
else()
  add_library(tiro_core STATIC ${TIRO_SOURCES})
  target_compile_definitions(tiro_core PUBLIC TIRO_STATIC)
endif()
target_include_directories(tiro_core PUBLIC include PRIVATE src third_party ${ORT_INCLUDE})
target_link_libraries(tiro_core PRIVATE tiro_moonshine ${ORT_LIB})
if(MINGW)
  # one self-contained DLL: no libstdc++/libgcc/winpthread DLLs to ship next to it
  target_link_options(tiro_core PRIVATE -static -static-libgcc -static-libstdc++)
endif()
if(NOT MSVC)
  target_compile_options(tiro_core PRIVATE -Wall -Wextra)
endif()
endif()  # TIRO_ENGINE

if(WIN32)
  # The global hotkey: a tiny DLL without ONNX Runtime, so it can start before any model is loaded.
  add_library(tiro_hook SHARED src/hotkey.cc src/win_hook.cc)
  target_compile_definitions(tiro_hook PRIVATE TIRO_BUILDING_HOOK)
  target_include_directories(tiro_hook PUBLIC include PRIVATE src third_party)
  if(MINGW)
    target_link_options(tiro_hook PRIVATE -static -static-libgcc -static-libstdc++)
  endif()
  if(NOT MSVC)
    target_compile_options(tiro_hook PRIVATE -Wall -Wextra)
  endif()
endif()

if(TIRO_BUILD_TESTS)
  # pure logic tests (no models needed): every platform build runs them
  add_executable(tiro-tests tests/test_text.cc tests/test_correct.cc tests/test_ladder.cc tests/test_hotkey.cc
                            src/text.cc src/correct.cc src/ladder.cc src/hotkey.cc)
  target_include_directories(tiro-tests PRIVATE src third_party)
  if(MINGW)
    target_link_options(tiro-tests PRIVATE -static -static-libgcc -static-libstdc++)
  endif()
  enable_testing()
  add_test(NAME tiro-tests COMMAND tiro-tests)
endif()

if(TIRO_BUILD_TOOLS AND TIRO_ENGINE)
  add_executable(tiro-transcribe tools/transcribe.cc)
  target_link_libraries(tiro-transcribe PRIVATE tiro_core)
  if(MINGW)
    target_link_options(tiro-transcribe PRIVATE -static -static-libgcc -static-libstdc++)
  endif()
endif()
