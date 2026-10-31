#ifndef UT_LINUX_LIVE_H
#define UT_LINUX_LIVE_H
#include "ut_model.h"
bool ut_linux_live_read(const char *directory, uint64_t now_ns, ut_frame_t *frame);
bool ut_linux_live_command(const char *directory, const char *action, const char *owner, unsigned gain, uint64_t now_ns);
#endif
