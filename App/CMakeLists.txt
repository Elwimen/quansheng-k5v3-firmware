
add_library(App INTERFACE)
target_include_directories(App INTERFACE .)
target_link_libraries(App INTERFACE PY32F071_Driver printf)
target_compile_definitions(App INTERFACE PRINTF_INCLUDE_CONFIG_H)
