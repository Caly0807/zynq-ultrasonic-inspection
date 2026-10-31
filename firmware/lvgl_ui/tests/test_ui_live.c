#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "ut_ui.h"

static unsigned calls;
static ut_action_t last_action;
static uint32_t pixels[800U * 8U];

static void action(ut_action_t requested)
{
    last_action = requested;
    calls++;
}

static lv_obj_t *find_caption(lv_obj_t *parent, const char *caption)
{
    if (lv_obj_check_type(parent, &lv_label_class) && !strcmp(lv_label_get_text(parent), caption)) return parent;
    for (uint32_t index = 0; index < lv_obj_get_child_count(parent); index++) {
        lv_obj_t *found = find_caption(lv_obj_get_child(parent, index), caption);
        if (found) return found;
    }
    return NULL;
}

int main(void)
{
    lv_init();
    lv_display_t *display = lv_display_create(800, 480);
    assert(display);
    lv_display_set_buffers(display, pixels, NULL, sizeof(pixels), LV_DISPLAY_RENDER_MODE_PARTIAL);
    ut_ui_create(action);
    assert(calls == 0);
    ut_ui_set_live_controls(false);
    assert(calls == 0);
    lv_obj_t *caption = find_caption(lv_screen_active(), "LIVE");
    assert(caption);
    lv_obj_send_event(lv_obj_get_parent(caption), LV_EVENT_CLICKED, NULL);
    assert(calls == 1 && last_action == UT_ACTION_LIVE_TOGGLE);
    ut_ui_set_live_controls(true);
    caption = find_caption(lv_screen_active(), "STOP");
    assert(caption);
    lv_obj_send_event(lv_obj_get_parent(caption), LV_EVENT_CLICKED, NULL);
    assert(calls == 2 && last_action == UT_ACTION_LIVE_TOGGLE);
    ut_frame_t frame = {0};
    frame.source = UT_SOURCE_LIVE;
    frame.count = UT_SAMPLE_CAPACITY;
    frame.sample_period_us = 0.04f;
    memset(frame.samples, 128, sizeof(frame.samples));
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "诊断无效"));
    frame.diagnosis.present = frame.diagnosis.baseline_valid = true;
    frame.diagnosis.status = UT_DIAG_UNCALIBRATED;
    frame.diagnosis.baseline = 128;
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "参数未标定"));
    frame.diagnosis.calibrated = true;
    frame.diagnosis.status = UT_DIAG_SUSPECT;
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "疑似反射"));
    lv_deinit();
    puts("LIVE_UI_CONTROL_PASS: entering a page never arms acquisition; start/stop are explicit operator actions");
    return 0;
}
