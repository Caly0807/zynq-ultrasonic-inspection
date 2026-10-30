#include "ut_ui.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <float.h>

static uint32_t draw_buffer[800 * 32];
static ut_action_t latest_action;
static unsigned action_count;
static void action_callback(ut_action_t action) { latest_action = action; action_count++; }
static void flush_callback(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    (void)area;
    (void)pixels;
    lv_display_flush_ready(display);
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
static void click_caption(const char *caption)
{
    lv_obj_t *label = find_label(lv_screen_active(), caption);
    assert(label);
    lv_obj_send_event(lv_obj_get_parent(label), LV_EVENT_CLICKED, NULL);
}
int main(void)
{
    lv_init();
    lv_display_t *display = lv_display_create(800, 480);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, flush_callback);
    ut_ui_create(action_callback);
    ut_frame_t frame;
    ut_generate_demo(&frame, 0, false);
    ut_ui_update(&frame);
    assert(find_label(lv_screen_active(), "DEMO / SYNTH"));
    ut_ui_update(&frame);
    assert(find_label(lv_screen_active(), "DEMO 演示"));
    assert(find_label(lv_screen_active(), "底波可见"));
    click_caption(LV_SYMBOL_PAUSE "  暂停");
    assert(ut_ui_is_paused());
    memset(frame.samples, 128, sizeof(frame.samples));
    frame.source = UT_SOURCE_RECORDED;
    ut_ui_update(&frame);
    assert(ut_ui_frame()->source == UT_SOURCE_DEMO);
    click_caption(LV_SYMBOL_PLAY "  继续");
    assert(!ut_ui_is_paused());
    ut_ui_update(&frame);
    assert(find_label(lv_screen_active(), "无有效回波"));
    click_caption("演示模式");
    assert(latest_action == UT_ACTION_DEMO && action_count == 1);
    click_caption(LV_SYMBOL_DIRECTORY);
    assert(latest_action == UT_ACTION_OPEN);
    click_caption(LV_SYMBOL_SAVE);
    assert(latest_action == UT_ACTION_EXPORT);
    uint32_t original_children = lv_obj_get_child_count(lv_screen_active());
    ut_ui_open_settings();
    assert(lv_obj_get_child_count(lv_screen_active()) == original_children + 1);
    lv_obj_t *overlay = lv_obj_get_child(lv_screen_active(), -1);
    lv_obj_t *card = lv_obj_get_child(overlay, 0);
    unsigned slider_count = 0;
    for (uint32_t index = 0; index < lv_obj_get_child_count(card); index++) {
        lv_obj_t *child = lv_obj_get_child(card, (int32_t)index);
        if (lv_obj_check_type(child, &lv_slider_class)) {
            lv_slider_set_value(child, slider_count == 0 ? 70 : 6100, LV_ANIM_OFF);
            lv_obj_send_event(child, LV_EVENT_VALUE_CHANGED, NULL);
            slider_count++;
        }
    }
    assert(slider_count == 2);
    assert(ut_ui_settings()->threshold_percent == 70.0f && ut_ui_settings()->sound_speed_mps == 6100.0f);
    click_caption("自动量程  ON");
    assert(!ut_ui_settings()->auto_scale);
    lv_refr_now(display);
    click_caption(LV_SYMBOL_CLOSE);
    assert(lv_obj_get_child_count(lv_screen_active()) == original_children);
    ut_scan_t scan = {0};
    scan.count = 3;
    scan.no_indication_count = 2;
    scan.suspect_count = 1;
    scan.contains_demo = true;
    scan.maximum_x_mm = 10;
    for (size_t index = 0; index < scan.count; index++) {
        scan.points[index].source = UT_SOURCE_DEMO;
        scan.points[index].stage_x_mm = (float)index * 5;
    }
    scan.points[1].status = UT_SCAN_SUSPECT;
    scan.points[1].depth_valid = true;
    scan.points[1].depth_mm = 16.52f;
    ut_ui_update_scan(&scan);
    assert(ut_ui_scan()->count == 3 && ut_ui_scan_index() == 0);
    assert(find_label(lv_screen_active(), "门区未见指示"));
    click_caption("下一点");
    assert(ut_ui_scan_index() == 1 && latest_action == UT_ACTION_SCAN_POINT);
    assert(find_label(lv_screen_active(), "疑似异常"));
    assert(find_label(lv_screen_active(), "16.52 mm"));
    assert(find_label(lv_screen_active(), "DEMO 演示 / 非实测结论\n不是实际滑台扫描"));
    click_caption("上一点");
    assert(ut_ui_scan_index() == 0);
    assert(!ut_ui_select_scan_point(3));
    click_caption("导入扫描");
    assert(latest_action == UT_ACTION_SCAN_OPEN);
    unsigned actions_before_view_change = action_count;
    click_caption("查看回波");
    assert(action_count == actions_before_view_change);
    click_caption("扫描复盘");
    click_caption(LV_SYMBOL_PAUSE "  暂停");
    assert(!ut_ui_select_scan_point(1) && ut_ui_scan_index() == 0);
    click_caption(LV_SYMBOL_PLAY "  继续");
    lv_refr_now(display);
    static ut_map_t map;
    char error[160];
    const char *map_text = "UTMAP,1,2,1,10,5,30,5,DEMO,MANUAL_UNVERIFIED\nCELL,0,0,SUSPECT,1,12\nCELL,1,0,UNSCANNED,0,\n";
    assert(ut_map_decode(map_text, strlen(map_text), &map, error, sizeof(error)));
    ut_ui_update_map(&map);
    unsigned actions_before_map = action_count;
    click_caption("二维面扫");
    assert(find_label(lv_screen_active(), "DEMO 面扫"));
    assert(find_label(lv_screen_active(), "MAP / DEMO / MANUAL GRID SNAPSHOT"));
    assert(find_label(lv_screen_active(), "z 12.00 mm"));
    assert(find_label(lv_screen_active(), "距底面 18.00 mm"));
    assert(ut_ui_select_map_cell(1, 0));
    assert(find_label(lv_screen_active(), "该格尚未扫描"));
    assert(find_label(lv_screen_active(), "z --.-- mm"));
    assert(!ut_ui_select_map_cell(2, 0) && !ut_ui_select_map_cell(0, 1));
    assert(actions_before_map == action_count);
    click_caption(LV_SYMBOL_PAUSE "  暂停");
    map.cells[0].depth_mm = 13;
    ut_ui_update_map(&map);
    assert(ut_ui_map()->cells[0].depth_mm == 12);
    click_caption(LV_SYMBOL_PLAY "  继续");
    ut_ui_update_map(&map);
    assert(ut_ui_map()->cells[0].depth_mm == 13);
    assert(find_label(lv_screen_active(), "DEMO 面扫"));
    click_caption("实时检测");
    assert(find_label(lv_screen_active(), "历史回放"));
    lv_refr_now(display);
    memset(&map, 0, sizeof(map));
    map.columns = map.rows = 1U;
    map.length_mm = map.width_mm = map.thickness_mm = map.grid_mm = FLT_TRUE_MIN;
    strcpy(map.source, "DEMO");
    assert(ut_map_validate(&map));
    ut_ui_update_map(&map); ut_ui_show_map(true);
    ut_ui_set_map_3d(false); lv_refr_now(display);
    ut_ui_set_map_3d(true); lv_refr_now(display);
    map.columns = map.rows = 32U;
    map.length_mm = map.width_mm = map.thickness_mm = 10000.0f;
    map.grid_mm = 312.5f;
    map.cells[1023].status = UT_MAP_SUSPECT;
    map.cells[1023].count = 1U;
    map.cells[1023].depth_valid = true;
    map.cells[1023].depth_mm = 9999.0f;
    map.sampled_cells = map.suspect_cells = map.depth_cells = map.point_count = 1U;
    assert(ut_map_validate(&map));
    ut_ui_update_map(&map);
    assert(ut_ui_select_map_cell(31U, 31U));
    lv_refr_now(display);
    ut_ui_set_map_3d(false); lv_refr_now(display);
    unsigned actions_before_stress = action_count;
    ut_touch_t touch = {0};
    touch.kind = UT_TOUCH_GOODIX;
    touch.address = 0x5dU;
    memcpy(touch.product, "911", 4U);
    touch.transform.raw_width = 800U;
    touch.transform.raw_height = 480U;
    for (unsigned cycle = 0; cycle < 250U; cycle++) {
        ut_ui_set_map_3d((cycle & 1U) != 0U);
        assert(ut_ui_select_map_cell(cycle % 32U, cycle / 32U));
        ut_ui_open_touch();
        ut_ui_open_touch();
        assert(lv_obj_get_child_count(lv_screen_active()) == original_children + 1U);
        touch.reads = cycle;
        ut_ui_update_touch(&touch);
        click_caption(LV_SYMBOL_CLOSE);
        assert(lv_obj_get_child_count(lv_screen_active()) == original_children);
        ut_ui_update_touch(&touch);
        ut_ui_open_settings();
        ut_ui_open_touch();
        assert(lv_obj_get_child_count(lv_screen_active()) == original_children + 1U);
        click_caption(LV_SYMBOL_CLOSE);
        assert(lv_obj_get_child_count(lv_screen_active()) == original_children);
        if (cycle % 25U == 0U) lv_refr_now(display);
    }
    assert(action_count == actions_before_stress);
    lv_deinit();
    puts("PASS: actual LVGL controls, pause/resume, parameters, scan navigation, map selection, depths, provenance, extreme dimensions, 250 overlay/navigation stress cycles and rendering");
    return 0;
}
