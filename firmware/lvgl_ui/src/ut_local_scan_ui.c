#include <math.h>
#include <stdio.h>
#include <string.h>
#include "ut_local_scan_ui.h"
#include "ut_ui.h"
#include "../vendor/lvgl/src/misc/cache/instance/lv_image_cache.h"

static lv_obj_t *overlay, *camera, *overview, *status_label, *hint_label, *live_caption;
static ut_local_scan_t current;
static ut_local_action_cb_t action_handler;
static lv_image_dsc_t preview_image;
static uint32_t notice_until;

static lv_obj_t *panel(lv_obj_t *parent, int position_x, int position_y, int width, int height)
{
    lv_obj_t *object = lv_obj_create(parent);
    lv_obj_remove_style_all(object);
    lv_obj_set_pos(object, position_x, position_y);
    lv_obj_set_size(object, width, height);
    lv_obj_set_style_bg_color(object, lv_color_hex(0x142431), 0);
    lv_obj_set_style_bg_opa(object, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(object, 9, 0);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_SCROLLABLE);
    return object;
}

static lv_obj_t *label(lv_obj_t *parent, const char *caption, int position_x, int position_y, int width)
{
    lv_obj_t *object = lv_label_create(parent);
    lv_obj_set_pos(object, position_x, position_y);
    lv_obj_set_width(object, width);
    lv_obj_set_style_text_font(object, &ut_font_14, 0);
    lv_obj_set_style_text_color(object, lv_color_hex(0xD9E6EC), 0);
    lv_label_set_text(object, caption);
    return object;
}

static void action_event(lv_event_t *event)
{
    ut_local_action_t action = (ut_local_action_t)(uintptr_t)lv_event_get_user_data(event);
    if (action_handler) action_handler(action, 0, 0);
}

static lv_obj_t *button(lv_obj_t *parent, const char *caption, int position_x, int position_y, int width, lv_event_cb_t callback, void *data)
{
    lv_obj_t *object = panel(parent, position_x, position_y, width, 44);
    lv_obj_add_flag(object, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_color(object, lv_color_hex(0x244251), LV_STATE_PRESSED);
    lv_obj_set_style_border_width(object, 1, 0);
    lv_obj_set_style_border_color(object, lv_color_hex(0x2CB9AC), 0);
    lv_obj_t *caption_label = label(object, caption, 0, 0, width);
    lv_obj_set_style_text_align(caption_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_center(caption_label);
    lv_obj_add_event_cb(object, callback, LV_EVENT_CLICKED, data);
    return object;
}

void ut_local_scan_ui_show(bool visible)
{
    if (!overlay) return;
    if (visible) {
        lv_obj_remove_flag(overlay, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(overlay);
    } else lv_obj_add_flag(overlay, LV_OBJ_FLAG_HIDDEN);
}

bool ut_local_scan_ui_visible(void)
{
    return overlay && !lv_obj_has_flag(overlay, LV_OBJ_FLAG_HIDDEN);
}

static void open_event(lv_event_t *event) { (void)event; ut_local_scan_ui_show(true); }
static void close_event(lv_event_t *event) { (void)event; ut_local_scan_ui_show(false); }

static void camera_click(lv_event_t *event)
{
    (void)event;
    lv_indev_t *input = lv_indev_active();
    if (!input || !(current.flags & 8U) || current.corner_count >= 4 || !action_handler) return;
    lv_point_t point;
    lv_area_t area;
    lv_indev_get_point(input, &point);
    lv_obj_get_coords(camera, &area);
    float position_x = (float)(point.x - area.x1) / UT_LOCAL_PREVIEW_WIDTH;
    float position_y = (float)(point.y - area.y1) / UT_LOCAL_PREVIEW_HEIGHT;
    if (position_x >= 0 && position_x < 1 && position_y >= 0 && position_y < 1)
        action_handler(UT_LOCAL_CORNER, position_x, position_y);
}

static uint32_t cell_color(ut_map_status_t status)
{
    const uint32_t colors[] = {0x293B4B, 0x2CB9AC, 0xF36B73, 0xD6A64C, 0x825D76};
    return status <= UT_MAP_INVALID ? colors[status] : colors[0];
}

static void draw_overview(lv_event_t *event)
{
    const ut_map_t *map = ut_ui_map();
    if (!map || !map->columns || !map->rows) return;
    lv_area_t area;
    lv_obj_get_coords(overview, &area);
    lv_layer_t *layer = lv_event_get_layer(event);
    for (unsigned row = 0; row < map->rows; row++) {
        for (unsigned column = 0; column < map->columns; column++) {
            lv_draw_rect_dsc_t descriptor;
            lv_draw_rect_dsc_init(&descriptor);
            descriptor.bg_color = lv_color_hex(cell_color(map->cells[row * map->columns + column].status));
            descriptor.bg_opa = LV_OPA_COVER;
            lv_area_t cell = {area.x1 + (int)(column * 180U / map->columns), area.y1 + (int)(row * 180U / map->rows),
                              area.x1 + (int)((column + 1) * 180U / map->columns) - 2,
                              area.y1 + (int)((row + 1) * 180U / map->rows) - 2};
            lv_draw_rect(layer, &descriptor, &cell);
        }
    }
    if ((current.flags & 4U) && current.position_x >= 0 && current.position_y >= 0) {
        int position_x = area.x1 + (int)(current.position_x / map->length_mm * 178.0f);
        int position_y = area.y1 + (int)(current.position_y / map->width_mm * 178.0f);
        lv_draw_rect_dsc_t marker;
        lv_draw_rect_dsc_init(&marker);
        marker.bg_opa = LV_OPA_TRANSP;
        marker.border_color = lv_color_hex(0xFFFFFF);
        marker.border_opa = LV_OPA_COVER;
        marker.border_width = 2;
        marker.radius = LV_RADIUS_CIRCLE;
        lv_area_t marker_area = {position_x - 5, position_y - 5, position_x + 5, position_y + 5};
        lv_draw_rect(layer, &marker, &marker_area);
    }
}

static void draw_corners(lv_event_t *event)
{
    lv_area_t area;
    lv_obj_get_coords(camera, &area);
    for (unsigned index = 0; index < current.corner_count; index++) {
        int position_x = area.x1 + (int)(current.corners[index * 2] * UT_LOCAL_PREVIEW_WIDTH);
        int position_y = area.y1 + (int)(current.corners[index * 2 + 1] * UT_LOCAL_PREVIEW_HEIGHT);
        lv_draw_rect_dsc_t descriptor;
        lv_draw_rect_dsc_init(&descriptor);
        descriptor.bg_color = lv_color_hex(0xFFD26F);
        descriptor.bg_opa = LV_OPA_COVER;
        lv_area_t point = {position_x - 3, position_y - 3, position_x + 3, position_y + 3};
        lv_draw_rect(lv_event_get_layer(event), &descriptor, &point);
    }
}

void ut_local_scan_ui_notice(const char *message)
{
    if (hint_label) lv_label_set_text(hint_label, message);
    notice_until = lv_tick_get() + 5000U;
}

void ut_local_scan_ui_update(const ut_local_scan_t *state, const uint8_t *preview)
{
    if (!overlay || !state) return;
    current = *state;
    const char *reasons[] = {"等待真实数据", "定位准备完成", "相机断开或画面过期", "请选择工件四角", "请先开启真实波形",
                            "探头标记丢失：不录入", "轻移探头，停稳后录入", "本格真实数据已录入", "扫描已结束，发射已停止",
                            "请求失败，请检查条件", "已采样，诊断尚未标定", "本格已采样，请移动探头", "图像与回波时间不匹配", "定位标定改变，请重新开始",
                            "回波无效：检查耦合、基线与幅值"};
    if ((int32_t)(lv_tick_get() - notice_until) >= 0) {
        if (current.flags & 8U)
            lv_label_set_text_fmt(hint_label, "四角 %u/4：左上 → 右上 → 右下 → 左下；确认相机与钢块固定", current.corner_count);
        else lv_label_set_text(hint_label, current.reason <= 14 ? reasons[current.reason] : "状态未知");
    }
    char position[64];
    if (current.flags & 4U) snprintf(position, sizeof(position), "X %.1f / Y %.1f mm", (double)current.position_x, (double)current.position_y);
    else strcpy(position, "X -- / Y -- mm");
    lv_label_set_text_fmt(status_label,
        "%s\n\n采样格 %u / %u\n未扫格 %u\n有效诊断格 %u\n疑似反射格 %u\n\n%s\n%s\n%s",
        current.phase == 1 ? "手动面扫进行中" : current.phase == 2 ? "扫描结果" : "脱机手动面扫",
        current.sampled, current.total, current.total - current.sampled, current.valid, current.suspect, position,
        current.flags & 2U ? "视觉：已标定" : "视觉：待选四角",
        current.flags & 32U ? "声学：已标定" : "声学：未标定，不报深度");
    lv_label_set_text(live_caption, current.flags & 16U ? "停止波形" : "真实波形");
    if (preview && (current.flags & 1U)) {
        preview_image.header.magic = LV_IMAGE_HEADER_MAGIC;
        preview_image.header.cf = LV_COLOR_FORMAT_XRGB8888;
        preview_image.header.w = UT_LOCAL_PREVIEW_WIDTH;
        preview_image.header.h = UT_LOCAL_PREVIEW_HEIGHT;
        preview_image.header.stride = UT_LOCAL_PREVIEW_WIDTH * 4U;
        preview_image.data_size = UT_LOCAL_PREVIEW_BYTES;
        preview_image.data = preview;
        lv_image_cache_drop(&preview_image);
        lv_image_set_src(camera, &preview_image);
        lv_obj_set_style_image_opa(camera, LV_OPA_COVER, 0);
    } else if (!(current.flags & 1U)) lv_obj_set_style_image_opa(camera, LV_OPA_20, 0);
    lv_obj_invalidate(camera);
    lv_obj_invalidate(overview);
}

void ut_local_scan_ui_create(ut_local_action_cb_t callback)
{
    action_handler = callback;
    memset(&current, 0, sizeof(current));
    notice_until = 0;
    button(lv_screen_active(), "视觉面扫", 564, 68, 110, open_event, NULL);
    overlay = panel(lv_screen_active(), 12, 114, 776, 352);
    label(overlay, "视觉定位 / 手动面扫", 16, 8, 300);
    button(overlay, "新工件", 566, 0, 112, action_event, (void *)(uintptr_t)UT_LOCAL_NEW);
    button(overlay, "返回", 700, 0, 64, close_event, NULL);
    hint_label = label(overlay, "相机和工件固定后，先选取工件四角", 16, 29, 680);
    camera = lv_image_create(overlay);
    lv_obj_set_pos(camera, 16, 52);
    lv_obj_set_size(camera, UT_LOCAL_PREVIEW_WIDTH, UT_LOCAL_PREVIEW_HEIGHT);
    lv_obj_add_flag(camera, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(camera, camera_click, LV_EVENT_CLICKED, NULL);
    lv_obj_add_event_cb(camera, draw_corners, LV_EVENT_DRAW_POST, NULL);
    overview = panel(overlay, 350, 52, 180, 180);
    lv_obj_add_event_cb(overview, draw_overview, LV_EVENT_DRAW_MAIN, NULL);
    label(overlay, "灰：未扫  绿：有效\n红：疑似  黄：未标定\n紫：无效  白圈：探头", 350, 240, 188);
    status_label = label(overlay, "正在连接板端服务", 548, 52, 212);
    button(overlay, "选取四角", 16, 300, 110, action_event, (void *)(uintptr_t)UT_LOCAL_FREEZE);
    button(overlay, "确认固定", 138, 300, 110, action_event, (void *)(uintptr_t)UT_LOCAL_CALIBRATE);
    lv_obj_t *live = button(overlay, "真实波形", 260, 300, 110, action_event, (void *)(uintptr_t)UT_LOCAL_LIVE);
    live_caption = lv_obj_get_child(live, 0);
    button(overlay, "开始面扫", 382, 300, 110, action_event, (void *)(uintptr_t)UT_LOCAL_START);
    button(overlay, "结束二维", 504, 300, 110, action_event, (void *)(uintptr_t)UT_LOCAL_FINISH);
    button(overlay, "查看三维", 626, 300, 134, action_event, (void *)(uintptr_t)UT_LOCAL_MAP_3D);
    ut_local_scan_ui_show(false);
}
