#include "ut_touch_input.h"

void ut_touch_input_disarm(ut_touch_input_t *input)
{
    if (!input) return;
    input->cancel = input->pressed;
    input->pressed = false;
    input->armed = false;
}

void ut_touch_input_update(ut_touch_input_t *input, ut_touch_result_t result, const ut_touch_sample_t *sample)
{
    if (!input || !sample) return;
    input->cancel = false;
    if (result != UT_TOUCH_OK && result != UT_TOUCH_NOT_READY) { ut_touch_input_disarm(input); return; }
    if (result == UT_TOUCH_NOT_READY) {
        if (input->pressed && !sample->pressed) ut_touch_input_disarm(input);
        return;
    }
    if (sample->fingers > 1U) { ut_touch_input_disarm(input); return; }
    if (!sample->pressed) { input->pressed = false; input->armed = true; return; }
    if (input->armed) { input->pressed = true; input->x = sample->x; input->y = sample->y; }
}
