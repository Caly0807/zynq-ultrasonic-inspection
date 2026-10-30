#include "ut_ui.h"
#include "ut_board_demo.h"
#include "ut_touch_input.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint32_t ticks, draw_buffer[800U * 32U];
static ut_touch_t touch;
static ut_touch_input_t contact;
static lv_indev_t *pointer;
static bool fail_reads, not_ready;
static uint8_t fingers;
static uint16_t raw_x, raw_y;
static unsigned action_count;

static uint32_t now_ms(void) { return ticks; }
static bool read_register(void *context, uint8_t address, uint16_t register_address, unsigned register_bytes, uint8_t *data, size_t length)
{
    (void)context;
    if (fail_reads || address != 0x5dU || register_bytes != 2U) return false;
    memset(data, 0, length);
    if (register_address == 0x8140U && length == 10U) {
        memcpy(data, "911", 4U);
        data[6] = 0x20U; data[7] = 3U; data[8] = 0xe0U; data[9] = 1U;
        return true;
    }
    if (register_address != 0x814eU || length != 9U) return false;
    data[0] = (not_ready ? 0U : 0x80U) | fingers;
    data[2] = (uint8_t)raw_x; data[3] = (uint8_t)(raw_x >> 8);
    data[4] = (uint8_t)raw_y; data[5] = (uint8_t)(raw_y >> 8);
    return true;
}
static bool write_register(void *context, uint8_t address, uint16_t register_address, unsigned register_bytes, const uint8_t *data, size_t length)
{
    (void)context;
    assert(address == 0x5dU && register_address == 0x814eU && register_bytes == 2U && length == 1U && data[0] == 0U);
    return true;
}
static void read_pointer(lv_indev_t *input, lv_indev_data_t *data)
{
    ut_touch_result_t result = ut_touch_poll(&touch, ticks);
    ut_touch_input_update(&contact, result, &touch.sample);
    if (contact.cancel) lv_indev_reset(input, NULL);
    data->state = contact.pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
    data->point.x = contact.x; data->point.y = contact.y;
    ut_ui_update_touch(&touch);
}
static void flush(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    (void)area; (void)pixels;
    lv_display_flush_ready(display);
}
static void pump(unsigned count)
{
    for (unsigned index = 0; index < count; index++) { ticks += 21U; lv_timer_handler(); }
}
static void position(unsigned screen_x, unsigned screen_y)
{
    assert(!touch.transform.swap_xy);
    raw_x = (uint16_t)(touch.transform.invert_x ? 799U - screen_x : screen_x);
    raw_y = (uint16_t)(touch.transform.invert_y ? 479U - screen_y : screen_y);
}
static void click(unsigned screen_x, unsigned screen_y)
{
    position(screen_x, screen_y);
    fingers = 1U; pump(2U);
    fingers = 0U; pump(2U);
}
static lv_obj_t *find_label(lv_obj_t *parent, const char *caption)
{
    if (lv_obj_check_type(parent, &lv_label_class) && !strcmp(lv_label_get_text(parent), caption)) return parent;
    for (uint32_t index = 0; index < lv_obj_get_child_count(parent); index++) {
        lv_obj_t *found = find_label(lv_obj_get_child(parent, (int32_t)index), caption);
        if (found) return found;
    }
    return NULL;
}
static void handle_action(ut_action_t action)
{
    action_count++;
    if (action == UT_ACTION_TOUCH_INVERT_X) {
        touch.transform.invert_x = !touch.transform.invert_x;
        ut_touch_input_disarm(&contact);
        lv_indev_reset(pointer, NULL);
        ut_ui_update_touch(&touch);
    }
}

int main(void)
{
    lv_init(); lv_tick_set_cb(now_ms);
    lv_display_t *display = lv_display_create(800, 480);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, flush);
    ut_ui_create(handle_action);
    static ut_map_t map;
    static ut_scan_t scan;
    ut_board_demo_create(&map, &scan);
    assert(ut_map_validate(&map) && map.sampled_cells == 208U && map.suspect_cells == 23U);
    ut_ui_update_map(&map); ut_ui_update_scan(&scan); ut_ui_show_scan(false);
    ut_touch_bus_t bus = {NULL, read_register, write_register, NULL};
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_OK);
    pointer = lv_indev_create();
    lv_indev_set_type(pointer, LV_INDEV_TYPE_POINTER); lv_indev_set_read_cb(pointer, read_pointer);
    lv_timer_set_period(lv_indev_get_read_timer(pointer), 20U);
    lv_refr_now(display); pump(2U);
    click(510U, 87U);
    assert(find_label(lv_screen_active(), "DEMO 面扫"));
    click(520U, 146U);
    assert(ut_ui_map_is_3d());
    click(680U, 373U);
    assert(find_label(lv_screen_active(), "z 17.22 mm"));
    position(200U, 260U); fingers = 1U; pump(2U);
    position(260U, 260U); pump(2U);
    fingers = 0U; pump(2U);
    assert(ut_ui_map_is_3d());
    uint32_t child_count = lv_obj_get_child_count(lv_screen_active());
    click(29U, 322U);
    assert(lv_obj_get_child_count(lv_screen_active()) == child_count + 1U);
    assert(find_label(lv_screen_active(), "911 / 0x5D / 800 x 480"));
    click(400U, 275U);
    assert(touch.transform.invert_x && action_count == 1U);
    assert(find_label(lv_screen_active(), "翻转 X / 开"));
    click(675U, 81U);
    assert(lv_obj_get_child_count(lv_screen_active()) == child_count);
    position(200U, 87U); fingers = 1U; pump(2U);
    fail_reads = true; pump(2U);
    assert(!contact.pressed && !contact.armed);
    assert(lv_obj_get_child_count(lv_screen_active()) == child_count);
    fail_reads = false; pump(2U);
    fingers = 0U; pump(2U);
    assert(lv_obj_get_child_count(lv_screen_active()) == child_count);
    click(200U, 87U);
    assert(lv_obj_get_child_count(lv_screen_active()) == child_count + 1U);
    click(608U, 105U);
    assert(lv_obj_get_child_count(lv_screen_active()) == child_count);
    position(300U, 87U); fingers = 1U; pump(2U);
    not_ready = true; pump(8U);
    assert(!contact.pressed && !contact.armed && action_count == 1U);
    not_ready = false; fingers = 0U; pump(2U);
    position(300U, 87U); fingers = 1U; pump(2U);
    fingers = 2U; pump(2U);
    fingers = 1U; pump(2U);
    fingers = 0U; pump(2U);
    assert(action_count == 1U && !contact.pressed && contact.armed);
    click(300U, 87U);
    assert(action_count == 2U);
    ut_ui_notify("Persistent test notice");
    assert(find_label(lv_screen_active(), "Persistent test notice"));
    pump(192U);
    assert(!find_label(lv_screen_active(), "Persistent test notice"));
    lv_refr_now(display); lv_deinit();
    puts("PASS: actual Goodix packets -> contact filter -> LVGL pointer -> tabs, 3D, navigation, diagnostics, orientation; fault/timeout/multitouch cancel without ghost clicks; hardware NOT tested");
    return 0;
}
