#ifndef UT_TOUCH_INPUT_H
#define UT_TOUCH_INPUT_H
#include "ut_touch.h"
typedef struct {
    bool armed, pressed, cancel;
    uint16_t x, y;
} ut_touch_input_t;
void ut_touch_input_update(ut_touch_input_t *input, ut_touch_result_t result, const ut_touch_sample_t *sample);
void ut_touch_input_disarm(ut_touch_input_t *input);
#endif
