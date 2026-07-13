# ESP-IDF component wrapper around this repository's own src/ tree.
# The bench compiles the fork's real sources in place - edit src/*.c,
# rebuild, measure. No vendored copy, no sync step.
get_filename_component(AMY_SRC_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../../src" ABSOLUTE)

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

idf_component_register(
    SRCS ${AMY_SRC_FILES}
    INCLUDE_DIRS "${AMY_SRC_DIR}"
    REQUIRES esp_timer esp_driver_uart esp_driver_i2s esp_driver_i2c esp_driver_gpio
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
