# ESP-IDF component wrapper around an AMY src/ tree that lives outside this repo.
# The bench measures AMY; it does not vendor it. Sources are compiled in place
# from whatever checkout is named here - no copy, no sync step.
#
# AMY_SRC_DIR is resolved in this order:
#   -D AMY_SRC_DIR=...  explicit, and what tools/abrun.py passes: it builds two
#                       firmwares from two git refs with an otherwise identical
#                       harness, so each side points at its own materialised tree.
#   $AMY_REPO/src       the AMY checkout used for a plain `idf.py build`.
#   ../amy/src          a sibling clone next to this repo.
if(NOT DEFINED AMY_SRC_DIR)
    if(DEFINED ENV{AMY_REPO})
        get_filename_component(AMY_SRC_DIR "$ENV{AMY_REPO}/src" ABSOLUTE)
    else()
        get_filename_component(AMY_SRC_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../../amy/src" ABSOLUTE)
    endif()
endif()
if(NOT EXISTS "${AMY_SRC_DIR}/amy.h")
    message(FATAL_ERROR
        "AMY_SRC_DIR=${AMY_SRC_DIR} does not look like an AMY src/ tree.\n"
        "This harness is a separate repo from AMY. Clone AMY and point the build "
        "at it:\n"
        "    git clone https://github.com/shorepine/amy\n"
        "    AMY_REPO=../amy idf.py build\n"
        "or pass -D AMY_SRC_DIR=/path/to/amy/src explicitly.")
endif()

file(GLOB AMY_SRC_FILES LIST_DIRECTORIES OFF "${AMY_SRC_DIR}/*.c")

# Exclude standalone example mains and host/desktop-only drivers.
list(FILTER AMY_SRC_FILES EXCLUDE REGEX "amy-example\\.c$")
list(FILTER AMY_SRC_FILES EXCLUDE REGEX "amy-message\\.c$")
list(FILTER AMY_SRC_FILES EXCLUDE REGEX "amy-piano\\.c$")
list(FILTER AMY_SRC_FILES EXCLUDE REGEX "libminiaudio-audio\\.c$")
list(FILTER AMY_SRC_FILES EXCLUDE REGEX "pyamy\\.c$")
list(FILTER AMY_SRC_FILES EXCLUDE REGEX "usb\\.c$")

if(NOT AMY_SRC_FILES)
    message(FATAL_ERROR "No AMY sources found in ${AMY_SRC_DIR}.")
endif()

# pie_dsp holds the ESP32-S3 PIE (128-bit SIMD) kernels that an AMY src/ tree can
# route its block clears/copies and block scans through, via a src/amy_simd.h shim.
# Listed unconditionally so the harness is one ruler for every arm: a src/ tree that
# does not include amy_simd.h never references a pie_dsp symbol, so the component's
# objects are simply not pulled out of the static library and the baseline's codegen
# is untouched.
idf_component_register(
    SRCS ${AMY_SRC_FILES}
    INCLUDE_DIRS "${AMY_SRC_DIR}"
    REQUIRES esp_timer esp_driver_uart esp_driver_i2s esp_driver_i2c esp_driver_gpio pie_dsp
)

# Build-config injection - src/ stays pristine (see bench/AMY-EDITS.md for the
# two #ifndef guards these rely on). PUBLIC so main sees identical amy.h macros.
target_compile_definitions(${COMPONENT_LIB} PUBLIC AMY_SAMPLE_RATE=${CONFIG_BENCH_SAMPLE_RATE})

if(CONFIG_BENCH_AMY_FLOAT)
    target_compile_definitions(${COMPONENT_LIB} PUBLIC AMY_USE_FLOAT)
endif()

# Per-tag profiler build (upstream AMY_DEBUG). Adds a timestamp read at every
# profiled call site - keep profile-build numbers separate from wall numbers.
if(CONFIG_BENCH_PROFILE)
    target_compile_definitions(${COMPONENT_LIB} PUBLIC AMY_DEBUG)
endif()

# amy.c does deliberate type punning; silence the (known) aliasing warnings.
set_source_files_properties("${AMY_SRC_DIR}/amy.c" PROPERTIES COMPILE_FLAGS "-Wno-strict-aliasing")

# Upstream declares run_midi_task() and esp_fill_audio_buffer_task() without
# the TaskFunction_t void* param; under IDF 6 (gnu23) that's an
# incompatible-pointer-types error. Harmless at runtime (the param is unused);
# demoted to keep src/ pristine. Upstream PR candidate: declare both as
# void fn(void *pvParameters).
foreach(_task_file "${AMY_SRC_DIR}/amy_midi.c" "${AMY_SRC_DIR}/i2s.c")
    set_property(SOURCE ${_task_file} APPEND_STRING PROPERTY COMPILE_FLAGS " -Wno-error=incompatible-pointer-types")
endforeach()

# Strip assert() from the pure-DSP hot-path files only, matching the production
# firmware's codegen (assertions inside per-sample loops cost real cycles and
# these files carry no structural/cross-task state).
set(AMY_DSP_HOT_FILES
    "${AMY_SRC_DIR}/filters.c"
    "${AMY_SRC_DIR}/oscillators.c"
    "${AMY_SRC_DIR}/envelope.c"
    "${AMY_SRC_DIR}/delay.c"
    "${AMY_SRC_DIR}/log2_exp2.c"
)
foreach(_dsp_file ${AMY_DSP_HOT_FILES})
    if(EXISTS ${_dsp_file})
        set_property(SOURCE ${_dsp_file} APPEND_STRING PROPERTY COMPILE_FLAGS " -DNDEBUG")
    endif()
endforeach()
