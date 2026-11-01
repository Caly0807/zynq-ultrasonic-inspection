#include <assert.h>
#include <math.h>
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
    lv_obj_t *caption = find_caption(lv_screen_active(), "开始检测");
    assert(caption);
    lv_obj_send_event(lv_obj_get_parent(caption), LV_EVENT_CLICKED, NULL);
    assert(calls == 1 && last_action == UT_ACTION_LIVE_TOGGLE);
    ut_ui_set_live_controls(true);
    caption = find_caption(lv_screen_active(), "停止检测");
    assert(caption);
    lv_obj_send_event(lv_obj_get_parent(caption), LV_EVENT_CLICKED, NULL);
    assert(calls == 2 && last_action == UT_ACTION_LIVE_TOGGLE);
    ut_frame_t frame = {0};
    frame.source = UT_SOURCE_LIVE;
    frame.count = UT_SAMPLE_CAPACITY;
    frame.sample_period_us = 0.04f;
    memset(frame.samples, 128, sizeof(frame.samples));
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "等待判读结果"));
    frame.diagnosis.present = frame.diagnosis.baseline_valid = true;
    frame.diagnosis.status = UT_DIAG_UNCALIBRATED;
    frame.diagnosis.baseline = 128;
    frame.diagnosis.depth_valid = true;
    frame.diagnosis.depth_mm = 15.2f;
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "参数未标定"));
    assert(find_caption(lv_screen_active(), "反射深度"));
    assert(find_caption(lv_screen_active(), "待标定"));
    assert(!find_caption(lv_screen_active(), "15.2"));
    frame.diagnosis.estimated_depth_valid = frame.diagnosis.flaw_crossed = true;
    frame.diagnosis.estimated_depth_mm = 22.1f;
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "参考深度"));
    assert(find_caption(lv_screen_active(), "可疑反射"));
    assert(find_caption(lv_screen_active(), "22.1"));
    assert(find_caption(lv_screen_active(), "待标定"));
    assert(!find_caption(lv_screen_active(), "已标定"));
    frame.diagnosis.estimated_depth_valid = false;
    frame.diagnosis.calibrated = true;
    frame.diagnosis.status = UT_DIAG_SUSPECT;
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "疑似反射"));
    assert(find_caption(lv_screen_active(), "已标定"));
    assert(find_caption(lv_screen_active(), "15.2"));
    assert(!find_caption(lv_screen_active(), "待标定"));
    frame.diagnosis.status = UT_DIAG_NO_INDICATION;
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "本帧门区未见指示"));
    assert(!find_caption(lv_screen_active(), "15.2"));
    frame.diagnosis.status = UT_DIAG_SUSPECT;
    frame.diagnosis.clipped = true;
    ut_ui_update(&frame);
    assert(!find_caption(lv_screen_active(), "15.2"));
    frame.diagnosis.clipped = false;
    frame.diagnosis.depth_mm = NAN;
    ut_ui_update(&frame);
    assert(find_caption(lv_screen_active(), "本帧无有效深度"));
    frame.diagnosis.depth_mm = -1.0f;
    ut_ui_update(&frame);
    assert(!find_caption(lv_screen_active(), "-1.0"));
    frame.diagnosis.depth_mm = 15.2f;
    frame.diagnosis.depth_valid = false;
    ut_ui_update(&frame);
    assert(!find_caption(lv_screen_active(), "15.2"));
    frame.diagnosis.depth_valid = true;
    frame.diagnosis.status = UT_DIAG_INVALID;
    ut_ui_update(&frame);
    assert(!find_caption(lv_screen_active(), "已标定"));
    assert(!find_caption(lv_screen_active(), "15.2"));
    lv_deinit();
    puts("LIVE_UI_CONTROL_PASS: entering a page never arms acquisition; start/stop are explicit operator actions");
    return 0;
}
