#ifndef UT_ECHO_SETUP_UI_H
#define UT_ECHO_SETUP_UI_H
#include "ut_echo_setup.h"
void ut_echo_setup_ui_init(ut_echo_action_cb_t callback);
void ut_echo_setup_ui_update(const ut_echo_setup_t *state);
void ut_echo_setup_ui_notice(const char *message);
bool ut_echo_setup_ui_visible(void);
#endif
