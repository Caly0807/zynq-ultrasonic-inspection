#ifndef UT_LINUX_TOUCH_H
#define UT_LINUX_TOUCH_H
#include <stdio.h>
#include "ut_ui.h"
bool ut_linux_touch_open(const char *path);
void ut_linux_touch_close(void);
void ut_linux_touch_action(ut_action_t action);
bool ut_linux_touch_write_status(FILE *output);
#endif
