idf_component_register(
    SRCS bench_main.c metrics.c scenes.c serial_proto.c render_clock.c
    INCLUDE_DIRS "."
    REQUIRES amy
    PRIV_REQUIRES esp_timer esp_driver_gptimer esp_hw_support esp_rom
)

# Bake the repo revision into the run header so captures are self-describing.
# Configure-time value: reconfigure (or clean) after committing to refresh it.
#
# Overridable (-D BENCH_GIT_REV=...) because the default describes the working
# tree, which says nothing about a src/ tree supplied via AMY_SRC_DIR. abrun.py
# stamps each side with the ref actually under test, so the two captures of an
# A/B run are distinguishable in their headers.
if(NOT DEFINED BENCH_GIT_REV)
    execute_process(
        COMMAND git -C ${CMAKE_CURRENT_LIST_DIR} describe --always --dirty --tags
        OUTPUT_VARIABLE BENCH_GIT_REV
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
    )
endif()
if(NOT BENCH_GIT_REV)
    set(BENCH_GIT_REV "unknown")
endif()
target_compile_definitions(${COMPONENT_LIB} PRIVATE BENCH_GIT_REV="${BENCH_GIT_REV}")
