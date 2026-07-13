# AMY on-target A/B benchmark harness for ESP32-S3.
cmake_minimum_required(VERSION 4.0)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(amy-bench)

# Optional LTO profile: enabled by building with
#   idf.py -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.lto" ...
# Requires the patched gcc.cmake (see ../tools/build-patches/) copied over
# managed_components/espressif__cmake_utilities/gcc.cmake after component fetch.
if(CONFIG_CU_GCC_LTO_ENABLE)
    include(gcc)
    cu_gcc_lto_set(COMPONENTS main amy)
endif()
