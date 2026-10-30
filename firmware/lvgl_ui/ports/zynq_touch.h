#ifndef ZYNQ_TOUCH_H
#define ZYNQ_TOUCH_H
#include "ut_ui.h"
#include "ut_i2c.h"
bool ut_zynq_touch_start(void);
void ut_zynq_touch_action(ut_action_t action);
const ut_touch_t *ut_zynq_touch_state(void);
uint32_t ut_zynq_touch_bus_status(void);
#endif
