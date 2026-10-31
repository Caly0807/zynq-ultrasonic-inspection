#ifndef UT_LOCAL_SCAN_UI_H
#define UT_LOCAL_SCAN_UI_H
#include "lvgl.h"
#include "ut_local_scan.h"
typedef void (*ut_local_action_cb_t)(ut_local_action_t action, float position_x, float position_y);
void ut_local_scan_ui_create(ut_local_action_cb_t callback);
void ut_local_scan_ui_update(const ut_local_scan_t *state, const uint8_t *preview);
void ut_local_scan_ui_show(bool visible);
bool ut_local_scan_ui_visible(void);
void ut_local_scan_ui_notice(const char *message);
#endif
