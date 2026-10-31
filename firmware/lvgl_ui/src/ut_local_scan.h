#ifndef UT_LOCAL_SCAN_H
#define UT_LOCAL_SCAN_H
#include <stdbool.h>
#include <stdint.h>

#define UT_LOCAL_PREVIEW_WIDTH 320U
#define UT_LOCAL_PREVIEW_HEIGHT 240U
#define UT_LOCAL_PREVIEW_BYTES (UT_LOCAL_PREVIEW_WIDTH * UT_LOCAL_PREVIEW_HEIGHT * 4U)
typedef struct {
    uint32_t flags, phase, reason, width, height, corner_count;
    uint32_t sampled, total, valid, suspect, depths, sequence;
    float position_x, position_y, coverage, skew_ms, corners[8];
} ut_local_scan_t;
typedef enum {
    UT_LOCAL_FREEZE, UT_LOCAL_CORNER, UT_LOCAL_CALIBRATE, UT_LOCAL_START,
    UT_LOCAL_FINISH, UT_LOCAL_LIVE, UT_LOCAL_MAP_3D, UT_LOCAL_NEW
} ut_local_action_t;
#endif
