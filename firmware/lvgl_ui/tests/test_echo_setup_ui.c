#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "ut_echo_setup_ui.h"
#include "ut_ui.h"

static uint32_t draw_buffer[800U * 48U], screen[800U * 480U];
static unsigned calls, last_parameter;
static float last_value;
static ut_echo_action_t last_action;
static lv_obj_t *controls;

static void action(ut_echo_action_t requested, unsigned parameter, float value)
{
    calls++;
    last_action = requested;
    last_parameter = parameter;
    last_value = value;
}

static void flush(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    const uint32_t *source = (const uint32_t *)pixels;
    for (int row = area->y1; row <= area->y2; row++) {
        memcpy(screen + row * 800 + area->x1, source, (size_t)(area->x2 - area->x1 + 1) * 4U);
        source += area->x2 - area->x1 + 1;
    }
    lv_display_flush_ready(display);
}

static lv_obj_t *find(lv_obj_t *parent, const char *caption, const lv_obj_class_t *class_type)
{
    if (class_type && lv_obj_check_type(parent, class_type)) return parent;
    if (caption && lv_obj_check_type(parent, &lv_label_class) && !strcmp(lv_label_get_text(parent), caption)) return parent;
    for (uint32_t index = 0; index < lv_obj_get_child_count(parent); index++) {
        lv_obj_t *found = find(lv_obj_get_child(parent, index), caption, class_type);
        if (found) return found;
    }
    return NULL;
}

static void press(const char *caption)
{
    lv_obj_t *found = find(controls ? controls : lv_screen_active(), caption, NULL);
    assert(found);
    lv_obj_send_event(lv_obj_get_parent(found), LV_EVENT_CLICKED, NULL);
}

int main(int argument_count, char **arguments)
{
    lv_init();
    lv_display_t *display = lv_display_create(800, 480);
    assert(display);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, flush);
    ut_ui_create(NULL);
    ut_ui_set_live_controls(false);
    ut_echo_setup_ui_init(action);
    assert(!ut_echo_setup_ui_visible() && calls == 0);
    press("声学设置");
    assert(ut_echo_setup_ui_visible() && calls == 0);
    press("应用未标定");
    assert(calls == 0);
    ut_echo_setup_t state = {.fresh = true, .target = 8, .gain = 128, .age_ms = -1,
        .parameters = {2.5f, 8, 8.5f, 12, 100, 110, 35, 12, 5900, 0, 30}};
    ut_echo_setup_ui_update(&state);
    lv_obj_t *selector = find(lv_screen_active(), NULL, &lv_dropdown_class);
    assert(selector);
    controls = lv_obj_get_parent(selector);
    lv_dropdown_open(selector);
    assert(lv_obj_get_style_text_font(lv_dropdown_get_list(selector), 0) == &ut_font_14);
    lv_dropdown_close(selector);
    lv_dropdown_set_selected(selector, 4);
    lv_obj_send_event(selector, LV_EVENT_VALUE_CHANGED, NULL);
    press("+");
    assert(calls == 0);
    press("采完整块参考");
    assert(calls == 0);
    press("更新当前项");
    assert(calls == 1 && last_action == UT_ECHO_EDIT && last_parameter == 4 && last_value == 101);
    state.parameters[4] = 101;
    ut_echo_setup_ui_update(&state);
    press("应用未标定");
    assert(calls == 1);
    lv_tick_inc(5001);
    press("应用未标定");
    assert(calls == 1);
    press("应用未标定");
    assert(calls == 2 && last_action == UT_ECHO_PREVIEW);
    press("审核并应用");
    press("审核并应用");
    assert(calls == 2);
    press("采完整块参考");
    press("采完整块参考");
    assert(calls == 3 && last_action == UT_ECHO_REFERENCE);
    state.phase = 2;
    state.reason = 11;
    state.collected = 8;
    state.proposed_zero = .8305f;
    state.flaw = 8;
    state.wall = 67;
    state.noise = .4f;
    ut_echo_setup_ui_update(&state);
    press("审核并应用");
    assert(calls == 3);
    press("审核并应用");
    assert(calls == 4 && last_action == UT_ECHO_APPROVE);
    lv_refr_now(display);
    if (argument_count == 2) {
        FILE *output = fopen(arguments[1], "wb");
        assert(output);
        assert(fprintf(output, "P6\n800 480\n255\n") > 0);
        for (unsigned index = 0; index < 800U * 480U; index++) {
            uint8_t rgb[] = {(uint8_t)(screen[index] >> 16), (uint8_t)(screen[index] >> 8), (uint8_t)screen[index]};
            assert(fwrite(rgb, 1, 3, output) == 3);
        }
        assert(fclose(output) == 0);
    }
    state.fresh = false;
    ut_echo_setup_ui_update(&state);
    press("应用未标定");
    press("应用未标定");
    assert(calls == 4);
    press("返回");
    assert(!ut_echo_setup_ui_visible());
    lv_deinit();
    puts("ECHO_SETUP_UI_PASS: opening sends nothing; draft edits, expiring double confirmation and explicit review required");
    return 0;
}
