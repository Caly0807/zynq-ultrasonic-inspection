#ifndef UT_LINUX_SCAN_H
#define UT_LINUX_SCAN_H
#include <stddef.h>
#include "../src/ut_local_scan.h"
bool ut_linux_scan_read(const char *directory, uint64_t now, ut_local_scan_t *state);
bool ut_linux_scan_preview(const char *directory, uint64_t now, uint8_t *pixels, size_t capacity);
bool ut_linux_scan_command(const char *directory, ut_local_action_t action, float position_x, float position_y, uint64_t now);
#endif
