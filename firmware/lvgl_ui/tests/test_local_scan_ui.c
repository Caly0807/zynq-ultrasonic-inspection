#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "ut_local_scan_ui.h"
#include "ut_ui.h"

static uint32_t draw_buffer[800U * 48U], screen[800U * 480U];
static uint8_t preview[UT_LOCAL_PREVIEW_BYTES];
static unsigned calls;
static ut_local_action_t last_action;

static void local_action(ut_local_action_t action, float position_x, float position_y)
{
    (void)position_x;
    (void)position_y;
    calls++;
    last_action = action;
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

static lv_obj_t *find_caption(lv_obj_t *parent, const char *caption)
{
    if (lv_obj_check_type(parent, &lv_label_class) && !strcmp(lv_label_get_text(parent), caption)) return parent;
    for (uint32_t index = 0; index < lv_obj_get_child_count(parent); index++) {
        lv_obj_t *found = find_caption(lv_obj_get_child(parent, index), caption);
        if (found) return found;
    }
    return NULL;
}

static void press(const char *caption)
{
    lv_obj_t *found = find_caption(lv_screen_active(), caption);
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
    ut_local_scan_ui_create(local_action);
    assert(!ut_local_scan_ui_visible() && calls == 0);
    press("视觉面扫");
    assert(ut_local_scan_ui_visible() && calls == 0);
    press("选取四角");
    assert(calls == 1 && last_action == UT_LOCAL_FREEZE);
    press("真实波形");
    assert(calls == 2 && last_action == UT_LOCAL_LIVE);
    ut_local_scan_t state = {.flags = 55, .phase = 1, .reason = 7, .width = 640, .height = 480,
                             .sampled = 1, .total = 256, .valid = 1, .suspect = 1, .depths = 1,
                             .position_x = 40, .position_y = 40, .coverage = .390625f};
    ut_map_t map = {.columns = 16, .rows = 16, .length_mm = 80, .width_mm = 80, .thickness_mm = 30,
                    .grid_mm = 5, .source = "LIVE", .sampled_cells = 1, .suspect_cells = 1, .depth_cells = 1, .point_count = 1};
    map.cells[136] = (ut_map_cell_t){.status = UT_MAP_SUSPECT, .count = 1, .depth_mm = 13, .depth_valid = true};
    ut_ui_update_map(&map);
    for (unsigned offset = 0; offset < sizeof(preview); offset += 4) {
        preview[offset] = 0x64;
        preview[offset + 1] = 0x71;
        preview[offset + 2] = 0x79;
    }
    ut_local_scan_ui_update(&state, preview);
    assert(calls == 2 && find_caption(lv_screen_active(), "停止波形"));
    press("开始面扫");
    assert(calls == 3 && last_action == UT_LOCAL_START);
    lv_refr_now(display);
    if (argument_count == 2) {
        FILE *output = fopen(arguments[1], "wb");
        assert(output);
        fprintf(output, "P6\n800 480\n255\n");
        for (unsigned index = 0; index < 800U * 480U; index++) {
            uint8_t rgb[] = {(uint8_t)(screen[index] >> 16), (uint8_t)(screen[index] >> 8), (uint8_t)screen[index]};
            assert(fwrite(rgb, 1, 3, output) == 3);
        }
        assert(fclose(output) == 0);
    }
    press("结束二维");
    assert(calls == 4 && last_action == UT_LOCAL_FINISH);
    press("返回");
    assert(!ut_local_scan_ui_visible());
    lv_deinit();
    puts("LOCAL_SCAN_UI_PASS: no implicit start; local calibration, explicit acquisition, scan, 2D and 3D controls");
    return 0;
}
