
add_library(printf INTERFACE)
# It's recommended for client code to not include `printf.h` directly. Include `<stdio.h>` instead
# target_include_directories(printf INTERFACE .)
target_sources(printf INTERFACE printf.c)
