#include "ut_ui.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define COLOR_BG 0x0B121C
#define COLOR_RAIL 0x101B28
#define COLOR_CARD 0x132130
#define COLOR_BORDER 0x26394A
#define COLOR_TEXT 0xEAF2F8
#define COLOR_MUTED 0x8599AD
#define COLOR_TEAL 0x45E2BD
#define COLOR_AMBER 0xF2C17B
#define COLOR_BLUE 0x75ABF7
#define COLOR_RED 0xF1797D

static lv_obj_t *root_screen, *plot, *source_label, *source_badge, *status_label;
static lv_obj_t *status_detail, *thickness_label, *amplitude_label, *tof_label, *gain_label;
static lv_obj_t *source_detail, *pause_label, *settings_overlay, *threshold_value, *velocity_value;
static lv_obj_t *scale_label, *legend_label, *sample_clock_label;
static lv_obj_t *scan_page, *scan_plot, *scan_summary, *scan_point_label, *scan_status_label;
static lv_obj_t *scan_position_label, *scan_depth_label, *scan_scope_label, *scan_range_label, *scan_min_label, *active_tab_line;
static ut_scan_t current_scan;
static ut_map_t current_map;
static lv_obj_t *map_page, *map_plot, *map_summary_label, *map_position_label, *map_depth_label, *map_bottom_label, *map_source_label, *map_axes_label;
static lv_obj_t *map_mode_label, *map_title_label, *map_hint_label, *notice_box;
static lv_timer_t *notice_timer;
static lv_obj_t *touch_status_label, *touch_coordinates_label, *touch_statistics_label, *touch_packet_label;
static lv_obj_t *touch_swap_label, *touch_x_label, *touch_y_label;
static lv_obj_t *wave_tab_label, *scan_tab_label, *map_tab_label;
static ut_touch_t touch_state;
static bool touch_overlay_visible, map_3d, map_dragged;
static int map_drag_start, map_drag_last;
static float map_yaw = 0.65f;
static unsigned map_selected_column, map_selected_row;
static size_t scan_index;
static ut_frame_t current_frame;
static ut_settings_t settings;
static ut_metrics_t metrics;
static ut_action_cb_t action_handler;
static bool paused;
static bool live_controls;
static lv_obj_t *live_mode_button;

static lv_color_t color(uint32_t value) { return lv_color_hex(value); }

static lv_obj_t *panel(lv_obj_t *parent, int left, int top, int width, int height, uint32_t background, int radius)
{
    lv_obj_t *object = lv_obj_create(parent);
    lv_obj_remove_style_all(object);
    lv_obj_set_pos(object, left, top);
    lv_obj_set_size(object, width, height);
    lv_obj_set_style_bg_color(object, color(background), 0);
    lv_obj_set_style_bg_opa(object, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(object, radius, 0);
    lv_obj_set_style_border_color(object, color(COLOR_BORDER), 0);
    lv_obj_set_style_border_width(object, radius ? 1 : 0, 0);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_SCROLLABLE);
    return object;
}

static lv_obj_t *text(lv_obj_t *parent, const char *value, int left, int top,
                       const lv_font_t *font, uint32_t foreground)
{
    lv_obj_t *label = lv_label_create(parent);
    lv_obj_set_pos(label, left, top);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, color(foreground), 0);
    lv_label_set_text(label, value);
    return label;
}

static lv_obj_t *button(lv_obj_t *parent, const char *caption, int left, int top,
                         int width, int height, const lv_font_t *font, uint32_t foreground,
                         lv_event_cb_t callback, void *user_data)
{
    lv_obj_t *object = panel(parent, left, top, width, height, COLOR_CARD, 7);
    lv_obj_add_flag(object, LV_OBJ_FLAG_CLICKABLE);
    if (height < 44) lv_obj_set_ext_click_area(object, (44 - height + 1) / 2);
    lv_obj_set_style_bg_color(object, color(0x22394A), LV_STATE_PRESSED);
    lv_obj_set_style_border_color(object, color(COLOR_TEAL), LV_STATE_FOCUSED);
    lv_obj_t *label = text(object, caption, 0, 0, font, foreground);
    lv_obj_center(label);
    if (callback) lv_obj_add_event_cb(object, callback, LV_EVENT_CLICKED, user_data);
    return object;
}

static void redraw_values(void);

static void close_settings(lv_event_t *event)
{
    (void)event;
    if (settings_overlay) { lv_obj_delete(settings_overlay); settings_overlay = NULL; }
    touch_overlay_visible = false;
}

static void change_threshold(lv_event_t *event)
{
    settings.threshold_percent = (float)lv_slider_get_value(lv_event_get_target_obj(event));
    lv_label_set_text_fmt(threshold_value, "%.0f %%", (double)settings.threshold_percent);
    redraw_values();
}

static void change_velocity(lv_event_t *event)
{
    settings.sound_speed_mps = (float)lv_slider_get_value(lv_event_get_target_obj(event));
    lv_label_set_text_fmt(velocity_value, "%.0f m/s", (double)settings.sound_speed_mps);
    redraw_values();
}

static void toggle_scale(lv_event_t *event)
{
    settings.auto_scale = !settings.auto_scale;
    lv_obj_t *label = lv_obj_get_child(lv_event_get_target_obj(event), 0);
    lv_label_set_text(label, settings.auto_scale ? "自动量程  ON" : "自动量程  OFF");
    redraw_values();
}

static void style_slider(lv_obj_t *slider)
{
    lv_obj_set_style_bg_color(slider, color(COLOR_BORDER), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(slider, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_radius(slider, 4, LV_PART_MAIN);
    lv_obj_set_style_bg_color(slider, color(COLOR_TEAL), LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(slider, LV_OPA_COVER, LV_PART_INDICATOR);
    lv_obj_set_style_radius(slider, 4, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(slider, color(COLOR_TEAL), LV_PART_KNOB);
    lv_obj_set_style_bg_opa(slider, LV_OPA_COVER, LV_PART_KNOB);
    lv_obj_set_style_pad_all(slider, 5, LV_PART_KNOB);
    lv_obj_set_style_radius(slider, LV_RADIUS_CIRCLE, LV_PART_KNOB);
    lv_obj_set_ext_click_area(slider, 12);
}
static void open_settings(lv_event_t *event)
{
    (void)event;
    if (settings_overlay) return;
    settings_overlay = panel(root_screen, 0, 0, 800, 480, 0x03080F, 0);
    lv_obj_set_style_bg_opa(settings_overlay, LV_OPA_80, 0);
    lv_obj_t *card = panel(settings_overlay, 150, 72, 500, 340, COLOR_CARD, 14);
    text(card, "检测参数", 24, 20, &ut_font_16, COLOR_TEXT);
    text(card, "PREVIEW SETTINGS / NO HARDWARE WRITES", 24, 47, &lv_font_montserrat_10, COLOR_MUTED);
    button(card, LV_SYMBOL_CLOSE, 438, 16, 38, 34, &lv_font_montserrat_14, COLOR_MUTED, close_settings, NULL);
    text(card, "闸门 A 参考阈值", 24, 86, &ut_font_16, COLOR_MUTED);
    threshold_value = text(card, "", 370, 88, &lv_font_montserrat_16, COLOR_AMBER);
    lv_label_set_text_fmt(threshold_value, "%.0f %%", (double)settings.threshold_percent);
    lv_obj_t *threshold_slider = lv_slider_create(card);
    lv_obj_set_pos(threshold_slider, 28, 126);
    lv_obj_set_size(threshold_slider, 438, 8);
    style_slider(threshold_slider);
    lv_slider_set_range(threshold_slider, 5, 90);
    lv_slider_set_value(threshold_slider, (int)settings.threshold_percent, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(threshold_slider, color(COLOR_TEAL), LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(threshold_slider, color(COLOR_TEAL), LV_PART_KNOB);
    lv_obj_add_event_cb(threshold_slider, change_threshold, LV_EVENT_VALUE_CHANGED, NULL);
    text(card, "材料参考声速", 24, 164, &ut_font_16, COLOR_MUTED);
    velocity_value = text(card, "", 356, 166, &lv_font_montserrat_16, COLOR_TEAL);
    lv_label_set_text_fmt(velocity_value, "%.0f m/s", (double)settings.sound_speed_mps);
    lv_obj_t *velocity_slider = lv_slider_create(card);
    lv_obj_set_pos(velocity_slider, 28, 204);
    lv_obj_set_size(velocity_slider, 438, 8);
    style_slider(velocity_slider);
    lv_slider_set_range(velocity_slider, 5000, 6500);
    lv_slider_set_value(velocity_slider, (int)settings.sound_speed_mps, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(velocity_slider, color(COLOR_TEAL), LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(velocity_slider, color(COLOR_TEAL), LV_PART_KNOB);
    lv_obj_add_event_cb(velocity_slider, change_velocity, LV_EVENT_VALUE_CHANGED, NULL);
    button(card, settings.auto_scale ? "自动量程  ON" : "自动量程  OFF", 24, 240, 190, 38,
           &ut_font_16, COLOR_TEAL, toggle_scale, NULL);
    text(card, "A  2.5 - 8.0 us    B  8.5 - 14.0 us", 240, 252, &lv_font_montserrat_10, COLOR_MUTED);
    text(card, "参考显示，不替代标准试块标定或缺陷判定。", 24, 300, &ut_font_14, COLOR_AMBER);
}

static void action_event(lv_event_t *event)
{
    ut_action_t action = (ut_action_t)(uintptr_t)lv_event_get_user_data(event);
    if (live_controls && action == UT_ACTION_DEMO) action = UT_ACTION_LIVE_TOGGLE;
    if (action_handler) action_handler(action);
}

static void refresh_touch_panel(void)
{
    if (!touch_overlay_visible) return;
    if (touch_state.kind == UT_TOUCH_NONE) lv_label_set_text(touch_status_label, "触控未识别 / 保留只读探测信息");
    else lv_label_set_text_fmt(touch_status_label, "%s / 0x%02X / %u x %u", touch_state.product, touch_state.address,
                               touch_state.transform.raw_width, touch_state.transform.raw_height);
    lv_obj_set_style_text_color(touch_status_label, color(touch_state.kind == UT_TOUCH_NONE ? COLOR_AMBER : COLOR_TEAL), 0);
    lv_label_set_text_fmt(touch_coordinates_label, "RAW  %u , %u    SCREEN  %u , %u\n%s / %u FINGER", touch_state.sample.raw_x,
                          touch_state.sample.raw_y, touch_state.sample.x, touch_state.sample.y,
                          touch_state.sample.pressed ? "PRESSED" : "RELEASED", touch_state.sample.fingers);
    lv_label_set_text_fmt(touch_statistics_label, "READ %lu   ERROR %lu   REJECT %lu\nACK %02lX   FT %04X : %02X   GT %08lX", (unsigned long)touch_state.reads,
                          (unsigned long)touch_state.errors, (unsigned long)touch_state.rejected, (unsigned long)touch_state.probe_ack_mask,
                          touch_state.ft_version, touch_state.ft_chip_id, (unsigned long)touch_state.goodix_product_code);
    lv_label_set_text_fmt(touch_packet_label, "FT PACKET %02X -- %02X %02X %02X %02X %02X / RESULT %u", touch_state.last_packet[0],
                          touch_state.last_packet[2], touch_state.last_packet[3], touch_state.last_packet[4],
                          touch_state.last_packet[5], touch_state.last_packet[6], (unsigned)touch_state.last_result);
    lv_label_set_text(touch_swap_label, touch_state.transform.swap_xy ? "交换 XY / 开" : "交换 XY / 关");
    lv_label_set_text(touch_x_label, touch_state.transform.invert_x ? "翻转 X / 开" : "翻转 X / 关");
    lv_label_set_text(touch_y_label, touch_state.transform.invert_y ? "翻转 Y / 开" : "翻转 Y / 关");
}

void ut_ui_update_touch(const ut_touch_t *touch)
{
    if (!touch) return;
    touch_state = *touch;
    touch_state.product[sizeof(touch_state.product) - 1U] = 0;
    refresh_touch_panel();
}

void ut_ui_open_touch(void)
{
    if (settings_overlay) return;
    settings_overlay = panel(root_screen, 0, 0, 800, 480, 0x03080F, 0);
    lv_obj_add_flag(settings_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_opa(settings_overlay, LV_OPA_80, 0);
    lv_obj_t *card = panel(settings_overlay, 80, 48, 640, 384, COLOR_CARD, 14);
    text(card, "触控诊断与方向", 24, 20, &ut_font_20, COLOR_TEXT);
    button(card, LV_SYMBOL_CLOSE, 570, 12, 48, 44, &lv_font_montserrat_16, COLOR_MUTED, close_settings, NULL);
    touch_status_label = text(card, "", 24, 66, &ut_font_16, COLOR_TEAL);
    touch_coordinates_label = text(card, "", 24, 106, &lv_font_montserrat_16, COLOR_TEXT);
    touch_statistics_label = text(card, "", 24, 162, &lv_font_montserrat_14, COLOR_MUTED);
    lv_obj_t *swap = button(card, "", 24, 202, 180, 48, &ut_font_16, COLOR_TEAL, action_event, (void *)(uintptr_t)UT_ACTION_TOUCH_SWAP);
    lv_obj_t *invert_x = button(card, "", 230, 202, 180, 48, &ut_font_16, COLOR_TEAL, action_event, (void *)(uintptr_t)UT_ACTION_TOUCH_INVERT_X);
    lv_obj_t *invert_y = button(card, "", 436, 202, 180, 48, &ut_font_16, COLOR_TEAL, action_event, (void *)(uintptr_t)UT_ACTION_TOUCH_INVERT_Y);
    touch_swap_label = lv_obj_get_child(swap, 0);
    touch_x_label = lv_obj_get_child(invert_x, 0);
    touch_y_label = lv_obj_get_child(invert_y, 0);
    button(card, "重试探测", 24, 268, 180, 48, &ut_font_16, COLOR_AMBER, action_event, (void *)(uintptr_t)UT_ACTION_TOUCH_RETRY);
    button(card, "恢复方向", 230, 268, 180, 48, &ut_font_16, COLOR_MUTED, action_event, (void *)(uintptr_t)UT_ACTION_TOUCH_RESET);
    touch_packet_label = text(card, "", 24, 323, &lv_font_montserrat_14, COLOR_MUTED);
    text(card, "仅改变显示触控 / 不控制高压 / 方向掉电不保存", 24, 344, &ut_font_14, COLOR_AMBER);
    touch_overlay_visible = true;
    refresh_touch_panel();
}

static void open_touch_event(lv_event_t *event) { (void)event; ut_ui_open_touch(); }

static void toggle_pause(lv_event_t *event)
{
    (void)event;
    paused = !paused;
    lv_label_set_text(pause_label, paused ? LV_SYMBOL_PLAY "  继续" : LV_SYMBOL_PAUSE "  暂停");
    redraw_values();
}

static void draw_line(lv_layer_t *layer, int left, int top, int right, int bottom,
                      uint32_t foreground, unsigned width, unsigned opacity)
{
    lv_draw_line_dsc_t descriptor;
    lv_draw_line_dsc_init(&descriptor);
    descriptor.p1.x = left;
    descriptor.p1.y = top;
    descriptor.p2.x = right;
    descriptor.p2.y = bottom;
    descriptor.color = color(foreground);
    descriptor.width = width;
    descriptor.opa = opacity;
    lv_draw_line(layer, &descriptor);
}

static void draw_band(lv_layer_t *layer, int left, int top, int right, int bottom, uint32_t foreground)
{
    lv_draw_rect_dsc_t descriptor;
    lv_draw_rect_dsc_init(&descriptor);
    descriptor.bg_color = color(foreground);
    descriptor.bg_opa = 13;
    descriptor.border_width = 0;
    lv_area_t area = {left, top, right, bottom};
    lv_draw_rect(layer, &descriptor, &area);
    draw_line(layer, left, top, right, top, foreground, 2, 170);
}

static uint32_t scan_point_color(const ut_scan_point_t *point)
{
    return point->status == UT_SCAN_SUSPECT ? COLOR_RED : point->status == UT_SCAN_NO_INDICATION ? COLOR_TEAL :
           point->status == UT_SCAN_UNCALIBRATED ? COLOR_AMBER : COLOR_MUTED;
}

static uint32_t map_cell_color(ut_map_status_t status)
{
    return status == UT_MAP_SUSPECT ? COLOR_RED : status == UT_MAP_NO_INDICATION ? COLOR_TEAL :
           status == UT_MAP_UNCALIBRATED ? COLOR_AMBER : status == UT_MAP_INVALID ? COLOR_MUTED : 0x233448;
}

static void map_geometry(const lv_area_t *area, int *left, int *top, double *scale)
{
    *scale = fmin((double)(area->x2 - area->x1 - 30) / current_map.length_mm,
                  (double)(area->y2 - area->y1 - 20) / current_map.width_mm);
    *left = area->x1 + (int)(((double)(area->x2 - area->x1) - current_map.length_mm * *scale) / 2);
    *top = area->y1 + 9;
}

typedef struct { double cosine, sine, scale, center_x, center_y, offset_x, offset_y; } map_projection_t;

static void map_project_raw(const map_projection_t *projection, double position_x, double position_y, double depth, double *screen_x, double *screen_y)
{
    position_x -= current_map.length_mm / 2;
    position_y -= current_map.width_mm / 2;
    *screen_x = position_x * projection->cosine - position_y * projection->sine;
    *screen_y = (position_x * projection->sine + position_y * projection->cosine) * 0.45f + (depth - current_map.thickness_mm / 2) * 0.88f;
}

static map_projection_t map_projection(const lv_area_t *area)
{
    map_projection_t projection = {cosf(map_yaw), sinf(map_yaw), 1, 0, 0, 0, 0};
    double minimum_x = 1e6, maximum_x = -1e6, minimum_y = 1e6, maximum_y = -1e6;
    for (unsigned corner = 0; corner < 8U; corner++) {
        double projected_x, projected_y;
        map_project_raw(&projection, corner & 1U ? current_map.length_mm : 0, corner & 2U ? current_map.width_mm : 0,
                        corner & 4U ? current_map.thickness_mm : 0, &projected_x, &projected_y);
        minimum_x = fmin(minimum_x, projected_x); maximum_x = fmax(maximum_x, projected_x);
        minimum_y = fmin(minimum_y, projected_y); maximum_y = fmax(maximum_y, projected_y);
    }
    projection.scale = fmin((double)(lv_area_get_width(area) - 44) / (maximum_x - minimum_x),
                           (double)(lv_area_get_height(area) - 36) / (maximum_y - minimum_y));
    projection.center_x = (minimum_x + maximum_x) / 2;
    projection.center_y = (minimum_y + maximum_y) / 2;
    projection.offset_x = (float)(area->x1 + area->x2) / 2;
    projection.offset_y = (float)(area->y1 + area->y2) / 2;
    return projection;
}

static lv_point_t map_project(const map_projection_t *projection, double position_x, double position_y, double depth)
{
    double projected_x, projected_y;
    map_project_raw(projection, position_x, position_y, depth, &projected_x, &projected_y);
    lv_point_t point = {(int32_t)(projection->offset_x + (projected_x - projection->center_x) * projection->scale),
                        (int32_t)(projection->offset_y + (projected_y - projection->center_y) * projection->scale)};
    return point;
}

static lv_point_t map_cell_projection(const map_projection_t *projection, unsigned column, unsigned row, float depth)
{
    float position_x = ((float)column * current_map.grid_mm + fminf((float)(column + 1U) * current_map.grid_mm, current_map.length_mm)) / 2;
    float position_y = ((float)row * current_map.grid_mm + fminf((float)(row + 1U) * current_map.grid_mm, current_map.width_mm)) / 2;
    return map_project(projection, position_x, position_y, depth);
}

static void draw_map_3d(lv_layer_t *layer, const lv_area_t *area)
{
    map_projection_t projection = map_projection(area);
    lv_point_t corners[8];
    for (unsigned corner = 0; corner < 8U; corner++) corners[corner] = map_project(&projection, corner & 1U ? current_map.length_mm : 0,
                    corner & 2U ? current_map.width_mm : 0, corner & 4U ? current_map.thickness_mm : 0);
    for (unsigned corner = 0; corner < 8U; corner++) {
        for (unsigned axis = 1U; axis <= 4U; axis <<= 1) if (!(corner & axis)) {
            lv_point_t start = corners[corner], end = corners[corner | axis];
            draw_line(layer, start.x, start.y, end.x, end.y, COLOR_BLUE, 1, corner & 4U ? 75 : 170);
        }
    }
    for (unsigned row = 0; row < current_map.rows; row++) {
        for (unsigned column = 0; column < current_map.columns; column++) {
            const ut_map_cell_t *cell = &current_map.cells[row * current_map.columns + column];
            if (!cell->depth_valid) continue;
            bool selected = column == map_selected_column && row == map_selected_row;
            lv_point_t point = map_cell_projection(&projection, column, row, cell->depth_mm);
            if (selected) {
                lv_point_t surface = map_cell_projection(&projection, column, row, 0);
                draw_line(layer, surface.x, surface.y, point.x, point.y, COLOR_AMBER, 1, 160);
            }
            int radius = selected ? 6 : 3;
            draw_line(layer, point.x - radius, point.y, point.x + radius, point.y, selected ? COLOR_TEXT : COLOR_RED, selected ? 2 : 1, 255);
            draw_line(layer, point.x, point.y - radius, point.x, point.y + radius, selected ? COLOR_TEXT : COLOR_RED, selected ? 2 : 1, 255);
        }
    }
}

static void draw_map_grid(lv_event_t *event)
{
    if (!current_map.columns) return;
    lv_area_t area;
    lv_obj_get_coords(lv_event_get_target_obj(event), &area);
    if (map_3d) { draw_map_3d(lv_event_get_layer(event), &area); return; }
    int left, top;
    double scale;
    map_geometry(&area, &left, &top, &scale);
    lv_layer_t *layer = lv_event_get_layer(event);
    for (unsigned row = 0; row < current_map.rows; row++) {
        for (unsigned column = 0; column < current_map.columns; column++) {
            const ut_map_cell_t *cell = &current_map.cells[row * current_map.columns + column];
            lv_draw_rect_dsc_t descriptor;
            lv_draw_rect_dsc_init(&descriptor);
            descriptor.bg_color = color(map_cell_color(cell->status));
            descriptor.bg_opa = cell->count ? 180 : 100;
            descriptor.radius = 1;
            descriptor.border_width = row == map_selected_row && column == map_selected_column ? 2 : 0;
            descriptor.border_color = color(COLOR_TEXT);
            lv_area_t cell_area = {left + (int)((float)column * current_map.grid_mm * scale) + 1,
                                  top + (int)((float)row * current_map.grid_mm * scale) + 1,
                                  left + (int)(fminf((float)(column + 1) * current_map.grid_mm, current_map.length_mm) * scale) - 1,
                                  top + (int)(fminf((float)(row + 1) * current_map.grid_mm, current_map.width_mm) * scale) - 1};
            lv_draw_rect(layer, &descriptor, &cell_area);
        }
    }
}

static void redraw_map_header(void)
{
    if (!map_page || lv_obj_has_flag(map_page, LV_OBJ_FLAG_HIDDEN) || !source_label) return;
    bool demo = !strcmp(current_map.source, "DEMO");
    const char *caption = !current_map.columns ? "等待面扫" : demo ? "DEMO 面扫" :
                          !strcmp(current_map.source, "LIVE") ? "采集面扫" : "历史面扫";
    uint32_t foreground = demo ? COLOR_AMBER : COLOR_BLUE;
    lv_label_set_text(source_label, paused ? "显示已暂停" : caption);
    lv_obj_set_style_text_color(source_label, color(foreground), 0);
    lv_obj_set_style_border_color(source_badge, color(foreground), 0);
    lv_label_set_text_fmt(source_detail, "MAP / %s / MANUAL GRID SNAPSHOT", current_map.columns ? current_map.source : "WAITING");
}

static void redraw_map(void)
{
    if (!map_page) return;
    lv_label_set_text(map_title_label, map_3d ? "三维反射点图" : "二维手动扫描");
    lv_label_set_text(map_mode_label, map_3d ? "切换二维" : "三维点图");
    lv_label_set_text(map_hint_label, map_3d ? "拖动旋转 / 点击反射点 / 非 CT 或孔形" : "人工网格 / 数据快照 / 非孔形重建");
    if (!current_map.columns) {
        lv_label_set_text(map_summary_label, "等待面扫数据\n未扫区域保持未知");
        lv_label_set_text(map_source_label, "MAP / WAITING");
        lv_label_set_text(map_position_label, "X -- / Y -- mm");
        lv_label_set_text(map_depth_label, "z --.-- mm");
        lv_label_set_text(map_bottom_label, "距底面 --.-- mm");
        lv_label_set_text(map_axes_label, "左上 (0,0) / X → / Y ↓");
    } else {
        const ut_map_cell_t *cell = &current_map.cells[map_selected_row * current_map.columns + map_selected_column];
        float center_x = ((float)map_selected_column * current_map.grid_mm + fminf((float)(map_selected_column + 1) * current_map.grid_mm, current_map.length_mm)) / 2;
        float center_y = ((float)map_selected_row * current_map.grid_mm + fminf((float)(map_selected_row + 1) * current_map.grid_mm, current_map.width_mm)) / 2;
        lv_label_set_text_fmt(map_source_label, "%s / MANUAL\n%.0f x %.0f x %.0f mm", current_map.source, (double)current_map.length_mm,
                              (double)current_map.width_mm, (double)current_map.thickness_mm);
        lv_obj_set_style_text_color(map_source_label, color(!strcmp(current_map.source, "DEMO") ? COLOR_AMBER : COLOR_BLUE), 0);
        lv_label_set_text_fmt(map_summary_label, "网格 %u / %u\n疑似 %u  /  深度 %u", current_map.sampled_cells,
                              current_map.columns * current_map.rows, current_map.suspect_cells, current_map.depth_cells);
        lv_label_set_text_fmt(map_position_label, "X %.1f / Y %.1f mm", (double)center_x, (double)center_y);
        lv_label_set_text_fmt(map_axes_label, "左上 (0,0) / X %.0f / Y %.0f mm", (double)current_map.length_mm, (double)current_map.width_mm);
        if (cell->depth_valid) {
            lv_label_set_text_fmt(map_depth_label, "z %.2f mm", (double)cell->depth_mm);
            lv_label_set_text_fmt(map_bottom_label, "距底面 %.2f mm", (double)(current_map.thickness_mm - cell->depth_mm));
        } else {
            lv_label_set_text(map_depth_label, "z --.-- mm");
            lv_label_set_text(map_bottom_label, cell->count ? "该格无有效深度" : "该格尚未扫描");
        }
    }
    redraw_map_header();
    lv_obj_invalidate(map_plot);
}

bool ut_ui_select_map_cell(unsigned column, unsigned row)
{
    if (column >= current_map.columns || row >= current_map.rows) return false;
    map_selected_column = column;
    map_selected_row = row;
    redraw_map();
    return true;
}

static void map_grid_click(lv_event_t *event)
{
    lv_indev_t *pointer = lv_indev_active();
    if (!pointer || !current_map.columns) return;
    lv_point_t position;
    lv_indev_get_point(pointer, &position);
    lv_area_t area;
    lv_obj_get_coords(lv_event_get_target_obj(event), &area);
    if (map_3d) {
        if (map_dragged) return;
        map_projection_t projection = map_projection(&area);
        unsigned nearest = UT_MAP_CAPACITY;
        int minimum_distance = 24 * 24;
        for (unsigned index = 0; index < current_map.columns * current_map.rows; index++) {
            const ut_map_cell_t *cell = &current_map.cells[index];
            if (!cell->depth_valid) continue;
            lv_point_t point = map_cell_projection(&projection, index % current_map.columns, index / current_map.columns, cell->depth_mm);
            int offset_x = point.x - position.x, offset_y = point.y - position.y;
            int distance = offset_x * offset_x + offset_y * offset_y;
            if (distance < minimum_distance) { minimum_distance = distance; nearest = index; }
        }
        if (nearest < UT_MAP_CAPACITY) ut_ui_select_map_cell(nearest % current_map.columns, nearest / current_map.columns);
        return;
    }
    int left, top;
    double scale;
    map_geometry(&area, &left, &top, &scale);
    double position_x = (double)(position.x - left) / scale;
    double position_y = (double)(position.y - top) / scale;
    if (position_x < 0 || position_y < 0 || position_x >= current_map.length_mm || position_y >= current_map.width_mm) return;
    ut_ui_select_map_cell((unsigned)(position_x / current_map.grid_mm), (unsigned)(position_y / current_map.grid_mm));
}

static void map_drag(lv_event_t *event)
{
    if (!map_3d) return;
    lv_indev_t *pointer = lv_indev_active();
    if (!pointer) return;
    lv_point_t position;
    lv_indev_get_point(pointer, &position);
    if (lv_event_get_code(event) == LV_EVENT_PRESSED) {
        map_drag_start = map_drag_last = position.x;
        map_dragged = false;
    } else if (lv_event_get_code(event) == LV_EVENT_PRESSING) {
        int offset = position.x - map_drag_start;
        if (offset > 5 || offset < -5) map_dragged = true;
        if (map_dragged) {
            map_yaw += (float)(position.x - map_drag_last) * 0.012f;
            if (map_yaw > 3.141593f) map_yaw -= 6.283186f;
            if (map_yaw < -3.141593f) map_yaw += 6.283186f;
            lv_obj_invalidate(map_plot);
        }
        map_drag_last = position.x;
    }
}

void ut_ui_set_map_3d(bool enabled) { map_3d = enabled; redraw_map(); }
bool ut_ui_map_is_3d(void) { return map_3d; }
static void toggle_map_3d(lv_event_t *event) { (void)event; ut_ui_set_map_3d(!map_3d); }

static void map_navigation(lv_event_t *event)
{
    unsigned count = current_map.columns * current_map.rows;
    if (!count) return;
    int direction = (intptr_t)lv_event_get_user_data(event) < 0 ? -1 : 1;
    int current = (int)(map_selected_row * current_map.columns + map_selected_column);
    for (unsigned step = 1U; step < count; step++) {
        int candidate = (current + direction * (int)step + (int)count) % (int)count;
        if (map_3d && !current_map.cells[candidate].depth_valid) continue;
        ut_ui_select_map_cell((unsigned)candidate % current_map.columns, (unsigned)candidate / current_map.columns);
        break;
    }
}

void ut_ui_show_map(bool visible)
{
    if (!map_page) return;
    if (visible) {
        lv_obj_remove_flag(map_page, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(scan_page, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_pos(active_tab_line, 473, 106);
        lv_obj_set_width(active_tab_line, 58);
        redraw_map();
    } else {
        lv_obj_add_flag(map_page, LV_OBJ_FLAG_HIDDEN);
        redraw_values();
    }
    if (map_tab_label) {
        lv_obj_set_style_text_color(map_tab_label, color(visible ? COLOR_TEAL : COLOR_MUTED), 0);
        lv_obj_set_style_text_color(scan_tab_label, color(COLOR_MUTED), 0);
        lv_obj_set_style_text_color(wave_tab_label, color(visible ? COLOR_MUTED : COLOR_TEAL), 0);
    }
}

static void select_map_view(lv_event_t *event)
{
    (void)event;
    ut_ui_show_map(true);
}

static void create_map_page(void)
{
    map_page = panel(root_screen, 78, 112, 702, 335, COLOR_CARD, 12);
    map_title_label = text(map_page, "二维手动扫描", 18, 14, &ut_font_16, COLOR_TEXT);
    map_hint_label = text(map_page, "人工网格 / 数据快照 / 非孔形重建", 18, 42, &ut_font_14, COLOR_MUTED);
    map_source_label = text(map_page, "MAP / WAITING", 536, 18, &lv_font_montserrat_10, COLOR_AMBER);
    lv_obj_t *mode_button = button(map_page, "三维点图", 374, 12, 140, 44, &ut_font_16, COLOR_TEAL, toggle_map_3d, NULL);
    map_mode_label = lv_obj_get_child(mode_button, 0);
    map_plot = panel(map_page, 18, 67, 330, 216, 0x0E1925, 7);
    lv_obj_add_event_cb(map_plot, draw_map_grid, LV_EVENT_DRAW_MAIN, NULL);
    lv_obj_add_event_cb(map_plot, map_grid_click, LV_EVENT_CLICKED, NULL);
    lv_obj_add_event_cb(map_plot, map_drag, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(map_plot, map_drag, LV_EVENT_PRESSING, NULL);
    lv_obj_add_flag(map_plot, LV_OBJ_FLAG_CLICKABLE);
    map_summary_label = text(map_page, "", 374, 72, &ut_font_14, COLOR_TEXT);
    map_position_label = text(map_page, "", 374, 122, &ut_font_14, COLOR_MUTED);
    map_depth_label = text(map_page, "", 374, 151, &ut_font_20, COLOR_TEAL);
    map_bottom_label = text(map_page, "", 374, 184, &ut_font_14, COLOR_TEXT);
    text(map_page, "手动位置未标定 / 孔高未确定", 374, 210, &ut_font_14, COLOR_AMBER);
    button(map_page, "上一格", 374, 239, 140, 44, &ut_font_16, COLOR_MUTED, map_navigation, (void *)(intptr_t)-1);
    button(map_page, "下一格", 532, 239, 140, 44, &ut_font_16, COLOR_TEAL, map_navigation, (void *)(intptr_t)1);
    text(map_page, "青绿 无指示 / 红 疑似 / 黄 未标定 / 灰 无效 / 暗 未扫", 18, 294, &ut_font_14, COLOR_MUTED);
    map_axes_label = text(map_page, "", 374, 294, &ut_font_14, COLOR_MUTED);
    lv_obj_add_flag(map_axes_label, LV_OBJ_FLAG_HIDDEN);
    text(map_page, "未扫保持未知 / DEMO 非实测 / 不控制高压", 18, 315, &ut_font_14, COLOR_MUTED);
    lv_obj_add_flag(map_page, LV_OBJ_FLAG_HIDDEN);
    redraw_map();
}

void ut_ui_update_map(const ut_map_t *map)
{
    if (paused || !ut_map_validate(map)) return;
    bool initial = current_map.columns == 0;
    current_map = *map;
    if (map_selected_column >= map->columns || map_selected_row >= map->rows) map_selected_column = map_selected_row = 0;
    if (initial) {
        for (unsigned index = 0; index < map->columns * map->rows; index++) {
            if (map->cells[index].depth_valid) { map_selected_column = index % map->columns; map_selected_row = index / map->columns; break; }
        }
    }
    redraw_map();
}

const ut_map_t *ut_ui_map(void) { return &current_map; }

static int scan_point_x(const ut_scan_point_t *point, const lv_area_t *area)
{
    double span = (double)current_scan.maximum_x_mm - current_scan.minimum_x_mm;
    double fraction = span > 0 ? ((double)point->stage_x_mm - current_scan.minimum_x_mm) / span : 0.5;
    return area->x1 + 20 + (int)(fraction * (lv_area_get_width(area) - 41));
}

static void draw_scan_map(lv_event_t *event)
{
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_area_t area;
    lv_obj_get_coords(lv_event_get_target_obj(event), &area);
    int center_y = area.y1 + lv_area_get_height(&area) / 2;
    for (int column = 0; column <= 10; column++) {
        int pixel_x = area.x1 + 20 + column * (lv_area_get_width(&area) - 41) / 10;
        draw_line(layer, pixel_x, area.y1 + 12, pixel_x, area.y2 - 12, COLOR_BORDER, 1, 150);
    }
    draw_line(layer, area.x1 + 20, center_y, area.x2 - 20, center_y, COLOR_BORDER, 3, 255);
    for (size_t index = 0; index < current_scan.count; index++) {
        int pixel_x = scan_point_x(&current_scan.points[index], &area);
        lv_draw_rect_dsc_t descriptor;
        lv_draw_rect_dsc_init(&descriptor);
        descriptor.radius = LV_RADIUS_CIRCLE;
        descriptor.bg_color = color(scan_point_color(&current_scan.points[index]));
        descriptor.bg_opa = LV_OPA_COVER;
        descriptor.border_width = index == scan_index ? 2 : 0;
        descriptor.border_color = color(COLOR_TEXT);
        int radius = index == scan_index ? 9 : 6;
        lv_area_t marker = {pixel_x - radius, center_y - radius, pixel_x + radius, center_y + radius};
        lv_draw_rect(layer, &descriptor, &marker);
        if (index == scan_index) draw_line(layer, pixel_x, center_y + 15, pixel_x, area.y2 - 9, COLOR_TEXT, 1, 180);
    }
}

static void redraw_scan(void)
{
    if (!scan_page) return;
    if (!current_scan.count) {
        lv_label_set_text(scan_summary, "尚无扫描记录，请导入 summary.csv");
        lv_label_set_text(scan_point_label, "POINT -- / --");
        lv_label_set_text(scan_status_label, "等待记录");
        lv_label_set_text(scan_position_label, "X --.- mm");
        lv_label_set_text(scan_depth_label, "--.-- mm");
        lv_label_set_text(scan_scope_label, "仅回放已保存的扫描点\n不控制滑台或高压");
        lv_label_set_text(scan_range_label, "STAGE X / mm");
        lv_label_set_text(scan_min_label, "");
    } else {
        const ut_scan_point_t *point = &current_scan.points[scan_index];
        const char *status = point->status == UT_SCAN_SUSPECT ? "疑似异常" : point->status == UT_SCAN_NO_INDICATION ?
                             "门区未见指示" : point->status == UT_SCAN_UNCALIBRATED ? "参数未标定" : "诊断无效";
        lv_label_set_text_fmt(scan_summary, "%zu 点  /  %zu 疑似  /  %zu 无指示  /  %zu 待核验", current_scan.count,
                              current_scan.suspect_count, current_scan.no_indication_count,
                              current_scan.uncalibrated_count + current_scan.invalid_count);
        lv_label_set_text_fmt(scan_point_label, "POINT %03zu / %03zu", scan_index + 1, current_scan.count);
        lv_label_set_text(scan_status_label, status);
        lv_obj_set_style_text_color(scan_status_label, color(scan_point_color(point)), 0);
        lv_label_set_text_fmt(scan_position_label, "X %.1f mm", (double)point->stage_x_mm);
        if (point->depth_valid) lv_label_set_text_fmt(scan_depth_label, "%.2f mm", (double)point->depth_mm);
        else lv_label_set_text(scan_depth_label, "--.-- mm");
        lv_label_set_text(scan_scope_label, point->source == UT_SOURCE_DEMO ?
                          "DEMO 演示 / 非实测结论\n不是实际滑台扫描" : "历史诊断 / 标定待核验\n无指示不等于合格");
        lv_label_set_text_fmt(scan_min_label, "%.1f mm", (double)current_scan.minimum_x_mm);
        lv_label_set_text_fmt(scan_range_label, "%.1f mm / STAGE X", (double)current_scan.maximum_x_mm);
    }
    lv_obj_invalidate(scan_plot);
}

void ut_ui_show_scan(bool visible)
{
    ut_ui_show_map(false);
    if (!scan_page) return;
    if (visible) lv_obj_remove_flag(scan_page, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(scan_page, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_pos(active_tab_line, visible ? 360 : 79, 106);
    lv_obj_set_width(active_tab_line, visible ? 74 : 56);
    if (scan_tab_label) {
        lv_obj_set_style_text_color(scan_tab_label, color(visible ? COLOR_TEAL : COLOR_MUTED), 0);
        lv_obj_set_style_text_color(wave_tab_label, color(visible ? COLOR_MUTED : COLOR_TEAL), 0);
    }
}

bool ut_ui_select_scan_point(size_t index)
{
    if (paused || index >= current_scan.count) return false;
    scan_index = index;
    redraw_scan();
    if (action_handler) action_handler(UT_ACTION_SCAN_POINT);
    return true;
}

static void scan_navigation(lv_event_t *event)
{
    intptr_t direction = (intptr_t)lv_event_get_user_data(event);
    if (direction < 0 && scan_index > 0) ut_ui_select_scan_point(scan_index - 1);
    if (direction > 0 && scan_index + 1 < current_scan.count) ut_ui_select_scan_point(scan_index + 1);
}

static void scan_map_click(lv_event_t *event)
{
    lv_indev_t *pointer = lv_indev_active();
    if (!pointer || !current_scan.count) return;
    lv_point_t position;
    lv_indev_get_point(pointer, &position);
    lv_area_t area;
    lv_obj_get_coords(lv_event_get_target_obj(event), &area);
    size_t nearest = 0;
    int distance = 0x7FFFFFFF;
    for (size_t index = 0; index < current_scan.count; index++) {
        int offset = scan_point_x(&current_scan.points[index], &area) - position.x;
        if (offset < 0) offset = -offset;
        if (offset < distance) { nearest = index; distance = offset; }
    }
    ut_ui_select_scan_point(nearest);
}

static void select_view(lv_event_t *event)
{
    bool scan_visible = (intptr_t)lv_event_get_user_data(event) != 0;
    ut_ui_show_scan(scan_visible);
    if (scan_visible && current_scan.count && !paused && action_handler) action_handler(UT_ACTION_SCAN_POINT);
}

static void create_scan_page(void)
{
    scan_page = panel(root_screen, 78, 112, 702, 335, COLOR_CARD, 12);
    text(scan_page, "单轴扫描复盘", 18, 14, &ut_font_16, COLOR_TEXT);
    text(scan_page, "位置分布 / 非二维 C 扫描", 18, 43, &ut_font_14, COLOR_MUTED);
    text(scan_page, "SCAN REVIEW", 565, 18, &lv_font_montserrat_10, COLOR_MUTED);
    scan_plot = panel(scan_page, 18, 76, 426, 120, 0x0E1925, 7);
    lv_obj_add_flag(scan_plot, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scan_plot, draw_scan_map, LV_EVENT_DRAW_MAIN, NULL);
    lv_obj_add_event_cb(scan_plot, scan_map_click, LV_EVENT_CLICKED, NULL);
    scan_range_label = text(scan_page, "", 24, 204, &lv_font_montserrat_10, COLOR_MUTED);
    lv_obj_set_width(scan_range_label, 414);
    lv_obj_set_style_text_align(scan_range_label, LV_TEXT_ALIGN_RIGHT, 0);
    scan_min_label = text(scan_page, "", 24, 204, &lv_font_montserrat_10, COLOR_MUTED);
    scan_summary = text(scan_page, "", 18, 231, &ut_font_14, COLOR_TEXT);
    text(scan_page, "青绿 无指示    红 疑似    黄 未标定    灰 无效", 18, 255, &ut_font_14, COLOR_MUTED);
    button(scan_page, "导入扫描", 18, 289, 96, 30, &ut_font_14, COLOR_TEAL, action_event, (void *)(uintptr_t)UT_ACTION_SCAN_OPEN);
    button(scan_page, "上一点", 126, 289, 80, 30, &ut_font_14, COLOR_MUTED, scan_navigation, (void *)(intptr_t)-1);
    button(scan_page, "下一点", 218, 289, 80, 30, &ut_font_14, COLOR_MUTED, scan_navigation, (void *)(intptr_t)1);
    button(scan_page, "查看回波", 310, 289, 116, 30, &ut_font_14, COLOR_TEAL, select_view, NULL);
    lv_obj_t *detail = panel(scan_page, 460, 57, 226, 262, 0x10212B, 10);
    scan_point_label = text(detail, "", 16, 14, &lv_font_montserrat_14, COLOR_MUTED);
    scan_status_label = text(detail, "", 16, 46, &ut_font_20, COLOR_TEAL);
    scan_position_label = text(detail, "", 16, 86, &lv_font_montserrat_24, COLOR_TEXT);
    text(detail, "反射深度估计 / 待核验", 16, 129, &ut_font_14, COLOR_MUTED);
    scan_depth_label = text(detail, "", 16, 153, &lv_font_montserrat_24, COLOR_AMBER);
    scan_scope_label = text(detail, "", 16, 202, &ut_font_14, COLOR_AMBER);
    lv_obj_set_width(scan_scope_label, 200);
    redraw_scan();
    ut_ui_show_scan(false);
}

static void draw_waveform(lv_event_t *event)
{
    lv_layer_t *layer = lv_event_get_layer(event);
    lv_area_t area;
    lv_obj_get_coords(lv_event_get_target_obj(event), &area);
    int width = lv_area_get_width(&area);
    int height = lv_area_get_height(&area);
    int center_y = area.y1 + height / 2;
    for (int column = 0; column <= 8; column++) {
        int position = area.x1 + column * (width - 1) / 8;
        draw_line(layer, position, area.y1, position, area.y2, COLOR_BORDER, 1, 115);
    }
    for (int row = 0; row <= 4; row++) {
        int position = area.y1 + row * (height - 1) / 4;
        draw_line(layer, area.x1, position, area.x2, position, COLOR_BORDER, 1, row == 2 ? 220 : 115);
    }
    int gate_a_left = area.x1 + (int)(settings.gate_a_start_us / 40.96f * width);
    int gate_a_right = area.x1 + (int)(settings.gate_a_end_us / 40.96f * width);
    int gate_b_left = area.x1 + (int)(settings.gate_b_start_us / 40.96f * width);
    int gate_b_right = area.x1 + (int)(settings.gate_b_end_us / 40.96f * width);
    draw_band(layer, gate_a_left, area.y1, gate_a_right, area.y2, COLOR_AMBER);
    draw_band(layer, gate_b_left, area.y1, gate_b_right, area.y2, COLOR_BLUE);
    if (!current_frame.count) return;
    float full_scale = settings.auto_scale && metrics.signal_present ? (float)metrics.peak_to_peak * 0.64f : 128.0f;
    if (full_scale < 24.0f) full_scale = 24.0f;
    float vertical_scale = ((float)height * 0.43f) / full_scale;
    int threshold_y = center_y - (int)(settings.threshold_percent * 1.28f * vertical_scale);
    if (threshold_y > area.y1 && threshold_y < area.y2) {
        for (int dash = area.x1; dash < area.x2; dash += 9) {
            draw_line(layer, dash, threshold_y, dash + 4 < area.x2 ? dash + 4 : area.x2,
                      threshold_y, COLOR_AMBER, 1, 110);
        }
    }
    int previous_y = center_y;
    for (int column = 0; column < width; column++) {
        float time_us = 40.96f * (float)column / (float)width;
        float next_time_us = 40.96f * (float)(column + 1) / (float)width;
        size_t start = current_frame.trigger_index + (size_t)(time_us / current_frame.sample_period_us);
        size_t end = current_frame.trigger_index + (size_t)(next_time_us / current_frame.sample_period_us);
        if (start >= current_frame.count) break;
        if (end <= start) end = start + 1;
        if (end > current_frame.count) end = current_frame.count;
        int minimum = 255, maximum = 0;
        for (size_t index = start; index < end; index++) {
            if (current_frame.samples[index] < minimum) minimum = current_frame.samples[index];
            if (current_frame.samples[index] > maximum) maximum = current_frame.samples[index];
        }
        int upper = center_y - (int)(((float)maximum - metrics.baseline) * vertical_scale);
        int lower = center_y - (int)(((float)minimum - metrics.baseline) * vertical_scale);
        if (upper < area.y1 + 2) upper = area.y1 + 2;
        if (upper > area.y2 - 2) upper = area.y2 - 2;
        if (lower < area.y1 + 2) lower = area.y1 + 2;
        if (lower > area.y2 - 2) lower = area.y2 - 2;
        int middle = (upper + lower) / 2;
        if (column) draw_line(layer, area.x1 + column - 1, previous_y, area.x1 + column, middle, COLOR_TEAL, 1, 230);
        draw_line(layer, area.x1 + column, upper, area.x1 + column, lower, COLOR_TEAL, 1, 255);
        previous_y = middle;
    }
}

static void redraw_values(void)
{
    if (!plot) return;
    ut_analyze(&current_frame, &settings, &metrics);
    const char *source = "等待采集";
    uint32_t source_color = COLOR_MUTED;
    if (current_frame.source == UT_SOURCE_DEMO) { source = "DEMO 演示"; source_color = COLOR_AMBER; }
    if (current_frame.source == UT_SOURCE_RECORDED) { source = "历史回放"; source_color = COLOR_BLUE; }
    if (current_frame.source == UT_SOURCE_LIVE) { source = "实时采集"; source_color = COLOR_TEAL; }
    if (paused) source = "显示已暂停";
    lv_label_set_text(source_label, source);
    lv_obj_set_style_text_color(source_label, color(source_color), 0);
    lv_obj_set_style_border_color(source_badge, color(source_color), 0);
    const char *status = !current_frame.count ? "等待数据" : !metrics.signal_present ? "无有效回波" :
                         metrics.clipped ? "信号饱和" : metrics.gate_a_crossed ? "疑似反射" :
                         metrics.gate_b_visible ? "底波可见" : "回波待确认";
    bool live = current_frame.source == UT_SOURCE_LIVE;
    const ut_diagnosis_t *diagnosis = &current_frame.diagnosis;
    if (live && current_frame.count)
        status = !diagnosis->present || diagnosis->status == UT_DIAG_INVALID ? "诊断无效" :
                 !diagnosis->calibrated ? "参数未标定" : diagnosis->status == UT_DIAG_SUSPECT ? "疑似反射" : "门区未见指示";
    lv_label_set_text(status_label, status);
    lv_obj_set_style_text_color(status_label, color(metrics.clipped || metrics.gate_a_crossed ? COLOR_AMBER : COLOR_TEAL), 0);
    lv_label_set_text(status_detail, current_frame.source == UT_SOURCE_DEMO ? "演示数据 / 非实测结论" : "请使用标准试块完成标定");
    if (metrics.gate_b_visible && !metrics.clipped && (!live || (diagnosis->present && diagnosis->calibrated && diagnosis->status != UT_DIAG_INVALID))) lv_label_set_text_fmt(thickness_label, "%.1f", (double)metrics.reference_thickness_mm);
    else lv_label_set_text(thickness_label, "--.-");
    if (current_frame.count) lv_label_set_text_fmt(amplitude_label, "%u", metrics.peak_to_peak);
    else lv_label_set_text(amplitude_label, "---");
    if (metrics.gate_b_visible) lv_label_set_text_fmt(tof_label, "%.2f", (double)metrics.gate_b_time_us);
    else lv_label_set_text(tof_label, "--.--");
    if (current_frame.count) lv_label_set_text_fmt(gain_label, "0x%02X", current_frame.gain);
    else lv_label_set_text(gain_label, "----");
    lv_label_set_text_fmt(scale_label, "%s / ADC", settings.auto_scale ? "AUTO" : "FIXED");
    lv_label_set_text_fmt(legend_label, "A  %.0f%%     B  ECHO", (double)settings.threshold_percent);
    lv_label_set_text_fmt(source_detail, "%s%s", current_frame.source == UT_SOURCE_DEMO ? "DEMO / " : "SOURCE / ",
                          current_frame.count ? current_frame.source_name : "waiting for ILA CSV or PS capture");
    if (sample_clock_label) lv_label_set_text(sample_clock_label,
        current_frame.source == UT_SOURCE_DEMO ? "DEMO / SYNTH" : !current_frame.count ? "ADC / WAIT" :
        current_frame.sample_period_us > 0.03f ? "PL 25 MHz" : "ILA 50 MHz");
    redraw_map_header();
    lv_obj_invalidate(plot);
}

void ut_ui_create(ut_action_cb_t action_callback)
{
    action_handler = action_callback;
    memset(&current_map, 0, sizeof(current_map));
    memset(&current_scan, 0, sizeof(current_scan));
    memset(&touch_state, 0, sizeof(touch_state));
    settings_overlay = notice_box = NULL;
    notice_timer = NULL;
    touch_overlay_visible = paused = map_3d = live_controls = false;
    map_selected_column = map_selected_row = 0;
    map_yaw = 0.65f;
    ut_settings_default(&settings);
    root_screen = lv_screen_active();
    lv_obj_remove_style_all(root_screen);
    lv_obj_set_style_bg_color(root_screen, color(COLOR_BG), 0);
    lv_obj_set_style_bg_opa(root_screen, LV_OPA_COVER, 0);
    lv_obj_remove_flag(root_screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *rail = panel(root_screen, 0, 0, 58, 480, COLOR_RAIL, 0);
    lv_obj_t *logo = panel(rail, 11, 18, 36, 36, COLOR_TEAL, 10);
    lv_obj_t *logo_text = text(logo, "UT", 0, 0, &lv_font_montserrat_16, COLOR_BG);
    lv_obj_center(logo_text);
    lv_obj_t *home = button(rail, LV_SYMBOL_HOME, 7, 100, 44, 44, &lv_font_montserrat_16, COLOR_TEAL, select_view, NULL);
    lv_obj_set_style_bg_color(home, color(0x193B3D), 0);
    button(rail, LV_SYMBOL_SETTINGS, 10, 150, 38, 38, &lv_font_montserrat_16, COLOR_MUTED, open_settings, NULL);
    button(rail, LV_SYMBOL_DIRECTORY, 10, 200, 38, 38, &lv_font_montserrat_16, COLOR_MUTED, action_event, (void *)(uintptr_t)UT_ACTION_OPEN);
    button(rail, LV_SYMBOL_SAVE, 10, 250, 38, 38, &lv_font_montserrat_16, COLOR_MUTED, action_event, (void *)(uintptr_t)UT_ACTION_EXPORT);
    button(rail, LV_SYMBOL_KEYBOARD, 7, 300, 44, 44, &lv_font_montserrat_16, COLOR_TEAL, open_touch_event, NULL);
    text(rail, "FPGA", 13, 420, &lv_font_montserrat_10, COLOR_MUTED);
    text(rail, "7020", 13, 438, &lv_font_montserrat_10, COLOR_TEAL);
    text(root_screen, "ULTRASONIC", 78, 17, &lv_font_montserrat_24, COLOR_TEXT);
    text(root_screen, "超声探伤工作站", 79, 47, &ut_font_16, COLOR_MUTED);
    text(root_screen, "ZYNQ-7020  /  LVGL 9.4", 556, 20, &lv_font_montserrat_10, COLOR_MUTED);
    source_badge = panel(root_screen, 670, 40, 110, 24, COLOR_CARD, 12);
    source_label = text(source_badge, "等待采集", 14, 5, &ut_font_14, COLOR_MUTED);
    lv_obj_t *wave_tab = button(root_screen, "实时检测", 75, 68, 74, 38, &ut_font_14, COLOR_TEAL, select_view, NULL);
    wave_tab_label = lv_obj_get_child(wave_tab, 0);
    active_tab_line = panel(root_screen, 79, 106, 56, 2, COLOR_TEAL, 0);
    button(root_screen, "参数设置", 159, 68, 86, 38, &ut_font_14, COLOR_MUTED, open_settings, NULL);
    live_mode_button = button(root_screen, "演示模式", 258, 68, 86, 38, &ut_font_14, COLOR_MUTED, action_event, (void *)(uintptr_t)UT_ACTION_DEMO);
    lv_obj_t *scan_tab = button(root_screen, "扫描复盘", 356, 68, 94, 38, &ut_font_14, COLOR_MUTED, select_view, (void *)(intptr_t)1);
    lv_obj_t *map_tab = button(root_screen, "二维面扫", 466, 68, 86, 38, &ut_font_14, COLOR_MUTED, select_map_view, NULL);
    scan_tab_label = lv_obj_get_child(scan_tab, 0);
    map_tab_label = lv_obj_get_child(map_tab, 0);
    lv_obj_t *pause_button = button(root_screen, "", 686, 68, 94, 38, &ut_font_14, COLOR_MUTED, toggle_pause, NULL);
    pause_label = lv_obj_get_child(pause_button, 0);
    lv_label_set_text(pause_label, LV_SYMBOL_PAUSE "  暂停");
    lv_obj_center(pause_label);
    lv_obj_t *wave_card = panel(root_screen, 78, 112, 480, 254, COLOR_CARD, 12);
    text(wave_card, "A-SCAN", 18, 15, &lv_font_montserrat_14, COLOR_TEXT);
    text(wave_card, "实时回波", 103, 14, &ut_font_14, COLOR_MUTED);
    legend_label = text(wave_card, "A  30%     B  ECHO", 306, 16, &lv_font_montserrat_10, COLOR_AMBER);
    scale_label = text(wave_card, "AUTO / ADC", 18, 46, &lv_font_montserrat_10, COLOR_MUTED);
    text(wave_card, "+", 17, 77, &lv_font_montserrat_10, COLOR_MUTED);
    text(wave_card, "0", 17, 139, &lv_font_montserrat_10, COLOR_MUTED);
    text(wave_card, "-", 18, 204, &lv_font_montserrat_10, COLOR_MUTED);
    plot = panel(wave_card, 35, 70, 425, 144, 0x0E1925, 0);
    lv_obj_add_event_cb(plot, draw_waveform, LV_EVENT_DRAW_MAIN, NULL);
    for (int tick = 0; tick <= 4; tick++) {
        char tick_text[16];
        snprintf(tick_text, sizeof(tick_text), "%d", tick * 10);
        text(wave_card, tick_text, 34 + tick * 104, 224, &lv_font_montserrat_10, COLOR_MUTED);
    }
    text(wave_card, "us", 447, 46, &lv_font_montserrat_10, COLOR_MUTED);
    lv_obj_t *status_card = panel(root_screen, 574, 112, 206, 112, COLOR_CARD, 12);
    text(status_card, "回波评估", 16, 13, &ut_font_14, COLOR_MUTED);
    lv_obj_t *uncalibrated = panel(status_card, 142, 12, 50, 22, 0x3A3025, 5);
    text(uncalibrated, "待标定", 5, 3, &ut_font_14, COLOR_AMBER);
    status_label = text(status_card, "等待数据", 16, 45, &ut_font_20, COLOR_TEAL);
    status_detail = text(status_card, "请使用标准试块完成标定", 16, 82, &ut_font_14, COLOR_MUTED);
    lv_obj_t *thickness_card = panel(root_screen, 574, 236, 206, 130, 0x142A32, 12);
    lv_obj_set_style_border_color(thickness_card, color(0x29534D), 0);
    text(thickness_card, "底波参考厚度", 16, 13, &ut_font_14, COLOR_MUTED);
    thickness_label = text(thickness_card, "--.-", 14, 37, &lv_font_montserrat_48, COLOR_TEXT);
    text(thickness_card, "mm", 160, 66, &lv_font_montserrat_14, COLOR_TEAL);
    text(thickness_card, "ESTIMATE / NOT CALIBRATED", 16, 106, &lv_font_montserrat_10, COLOR_TEAL);
    lv_obj_t *amplitude_card = panel(root_screen, 78, 380, 152, 67, COLOR_CARD, 10);
    text(amplitude_card, "回波峰峰值", 12, 9, &ut_font_14, COLOR_MUTED);
    amplitude_label = text(amplitude_card, "---", 12, 31, &lv_font_montserrat_24, COLOR_TEXT);
    text(amplitude_card, "code", 104, 44, &lv_font_montserrat_10, COLOR_MUTED);
    lv_obj_t *tof_card = panel(root_screen, 242, 380, 152, 67, COLOR_CARD, 10);
    text(tof_card, "底波飞行时间", 12, 9, &ut_font_14, COLOR_MUTED);
    tof_label = text(tof_card, "--.--", 12, 31, &lv_font_montserrat_24, COLOR_TEXT);
    text(tof_card, "us", 120, 44, &lv_font_montserrat_10, COLOR_MUTED);
    lv_obj_t *gain_card = panel(root_screen, 406, 380, 152, 67, COLOR_CARD, 10);
    text(gain_card, "增益控制", 12, 9, &ut_font_14, COLOR_MUTED);
    gain_label = text(gain_card, "----", 12, 31, &lv_font_montserrat_24, COLOR_TEXT);
    text(gain_card, "DAC", 118, 44, &lv_font_montserrat_10, COLOR_MUTED);
    lv_obj_t *sample_card = panel(root_screen, 574, 380, 206, 67, COLOR_CARD, 10);
    text(sample_card, "采样配置", 12, 9, &ut_font_14, COLOR_MUTED);
    text(sample_card, "25", 12, 31, &lv_font_montserrat_24, COLOR_TEXT);
    text(sample_card, "MSPS", 57, 44, &lv_font_montserrat_10, COLOR_MUTED);
    sample_clock_label = text(sample_card, "ILA 50 MHz", 119, 44, &lv_font_montserrat_10, COLOR_TEAL);
    source_detail = text(root_screen, "", 79, 461, &lv_font_montserrat_10, COLOR_MUTED);
    lv_obj_set_width(source_detail, 430);
    lv_label_set_long_mode(source_detail, LV_LABEL_LONG_DOT);
    text(root_screen, "工程验证 / 不作合格判定", 606, 459, &ut_font_14, COLOR_MUTED);
    create_scan_page();
    create_map_page();
    redraw_values();
}

void ut_ui_update(const ut_frame_t *frame)
{
    if (paused) return;
    current_frame = *frame;
    redraw_values();
}

static void expire_notice(lv_timer_t *timer)
{
    (void)timer;
    if (notice_box) lv_obj_delete(notice_box);
    notice_box = NULL;
    notice_timer = NULL;
}

void ut_ui_notify(const char *message)
{
    if (!root_screen || !message) return;
    if (notice_timer) lv_timer_delete(notice_timer);
    if (notice_box) lv_obj_delete(notice_box);
    notice_box = panel(root_screen, 78, 434, 702, 38, 0x233443, 8);
    lv_obj_t *caption = text(notice_box, message, 14, 11, &ut_font_14, COLOR_TEXT);
    lv_obj_set_width(caption, 674);
    lv_label_set_long_mode(caption, LV_LABEL_LONG_CLIP);
    lv_obj_set_height(caption, 18);
    notice_timer = lv_timer_create(expire_notice, 4000U, NULL);
    lv_timer_set_repeat_count(notice_timer, 1);
}
void ut_ui_set_live_controls(bool active)
{
    live_controls = true;
    if (live_mode_button) {
        lv_obj_t *caption = lv_obj_get_child(live_mode_button, 0);
        lv_label_set_text(caption, active ? "STOP" : "LIVE");
        lv_obj_center(caption);
    }
}

bool ut_ui_is_paused(void) { return paused; }
const ut_settings_t *ut_ui_settings(void) { return &settings; }
void ut_ui_open_settings(void) { open_settings(NULL); }

const ut_frame_t *ut_ui_frame(void) { return &current_frame; }

void ut_ui_update_scan(const ut_scan_t *scan)
{
    if (paused || scan->count > UT_SCAN_CAPACITY) return;
    current_scan = *scan;
    scan_index = 0;
    redraw_scan();
    ut_ui_show_scan(true);
}

size_t ut_ui_scan_index(void) { return scan_index; }
const ut_scan_t *ut_ui_scan(void) { return &current_scan; }
