#ifndef UT_LINUX_ECHO_H
#define UT_LINUX_ECHO_H
#include "../src/ut_echo_setup.h"
bool ut_linux_echo_read(const char *directory, uint64_t now, ut_echo_setup_t *state);
bool ut_linux_echo_command(const char *directory, ut_echo_action_t action, unsigned parameter, float value, uint64_t now);
#endif
