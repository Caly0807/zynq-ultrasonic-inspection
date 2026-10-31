#ifndef UT_UI_H
#define UT_UI_H
#include "lvgl.h"
#include "../assets/ut_font.h"
#include "ut_model.h"
#include "ut_scan.h"
#include "ut_map.h"
#include "ut_touch.h"
typedef enum { UT_ACTION_OPEN, UT_ACTION_EXPORT, UT_ACTION_DEMO, UT_ACTION_WAIT, UT_ACTION_SCAN_OPEN, UT_ACTION_SCAN_POINT,
               UT_ACTION_TOUCH_RETRY, UT_ACTION_TOUCH_SWAP, UT_ACTION_TOUCH_INVERT_X, UT_ACTION_TOUCH_INVERT_Y, UT_ACTION_TOUCH_RESET,
               UT_ACTION_LIVE_TOGGLE } ut_action_t;
typedef void (*ut_action_cb_t)(ut_action_t action);
void ut_ui_create(ut_action_cb_t action_callback);
void ut_ui_update(const ut_frame_t *frame);
void ut_ui_open_settings(void);
void ut_ui_notify(const char *message);
bool ut_ui_is_paused(void);
const ut_frame_t *ut_ui_frame(void);
const ut_settings_t *ut_ui_settings(void);
void ut_ui_update_scan(const ut_scan_t *scan);
void ut_ui_show_scan(bool visible);
bool ut_ui_select_scan_point(size_t index);
size_t ut_ui_scan_index(void);
const ut_scan_t *ut_ui_scan(void);
void ut_ui_update_map(const ut_map_t *map);
void ut_ui_show_map(bool visible);
bool ut_ui_select_map_cell(unsigned column, unsigned row);
const ut_map_t *ut_ui_map(void);
void ut_ui_set_map_3d(bool enabled);
bool ut_ui_map_is_3d(void);
void ut_ui_open_touch(void);
void ut_ui_update_touch(const ut_touch_t *touch);
void ut_ui_set_live_controls(bool active);
#endif
