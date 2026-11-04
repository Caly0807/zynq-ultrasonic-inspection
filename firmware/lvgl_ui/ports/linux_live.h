#ifndef UT_LINUX_LIVE_H
#define UT_LINUX_LIVE_H
#include "ut_model.h"
#define UT_LIVE_FRESH_NS 500000000ULL
#define UT_LIVE_HOLD_NS 2000000000ULL
bool ut_linux_live_read(const char *directory, uint64_t now_ns, ut_frame_t *frame);
bool ut_linux_live_present(ut_frame_t *display, const ut_frame_t *incoming, uint64_t now_ns);
bool ut_linux_live_command(const char *directory, const char *action, const char *owner, unsigned gain, uint64_t now_ns);
#endif
