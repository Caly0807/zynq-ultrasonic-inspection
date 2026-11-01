#include <math.h>
#include <stdio.h>
#include <string.h>
#include "ut_echo_setup_ui.h"
#include "ut_ui.h"

static lv_obj_t *overlay, *selector, *value_label, *summary_label, *status_label, *hint_label;
static ut_echo_action_cb_t action_handler;
static ut_echo_setup_t current;
static unsigned selected;
static float edited;
static bool dirty;
static uint32_t deadline, notice_until;
static ut_echo_action_t pending_action;
static const float steps[UT_ECHO_PARAMETER_COUNT] = {.1f, .1f, .1f, .1f, 1, 1, 1, 1, 10, .02f, .1f};
static const float minimums[UT_ECHO_PARAMETER_COUNT] = {0, .04f, .04f, .08f, 0, .04f, 1, 1, 1000, 0, 1};
static const float maximums[UT_ECHO_PARAMETER_COUNT] = {80, 100, 140, 160, 155, 163, 120, 120, 10000, 80, 100};

static lv_obj_t *label(lv_obj_t *parent, const char *text, int position_x, int position_y, int width)
{
    lv_obj_t *object = lv_label_create(parent);
    lv_obj_set_pos(object, position_x, position_y);
    lv_obj_set_width(object, width);
    lv_obj_set_style_text_font(object, &ut_font_14, 0);
    lv_obj_set_style_text_color(object, lv_color_hex(0xD9E6EC), 0);
    lv_label_set_text(object, text);
    return object;
}

static lv_obj_t *button(lv_obj_t *parent, const char *text, int position_x, int position_y, int width, lv_event_cb_t callback, void *data)
{
    lv_obj_t *object = lv_button_create(parent);
    lv_obj_set_pos(object, position_x, position_y);
    lv_obj_set_size(object, width, 40);
    lv_obj_set_style_bg_color(object, lv_color_hex(0x244251), 0);
    lv_obj_set_style_bg_opa(object, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(object, 0, 0);
    lv_obj_set_style_border_width(object, 1, 0);
    lv_obj_set_style_border_color(object, lv_color_hex(0x46707E), 0);
    lv_obj_set_style_radius(object, 8, 0);
    lv_obj_t *caption = label(object, text, 0, 0, width);
    lv_obj_set_style_text_align(caption, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_center(caption);
    lv_obj_add_event_cb(object, callback, LV_EVENT_CLICKED, data);
    return object;
}

void ut_echo_setup_ui_notice(const char *message)
{
    if (!hint_label) return;
    lv_label_set_text(hint_label, message);
    notice_until = lv_tick_get() + 5000;
}

static void open_event(lv_event_t *event)
{
    (void)event;
    deadline = 0;
    dirty = false;
    edited = current.parameters[selected];
    lv_obj_remove_flag(overlay, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(overlay);
}

static void close_event(lv_event_t *event)
{
    (void)event;
    deadline = 0;
    if (selector) lv_dropdown_close(selector);
    lv_obj_add_flag(overlay, LV_OBJ_FLAG_HIDDEN);
}
bool ut_echo_setup_ui_visible(void) { return overlay && !lv_obj_has_flag(overlay, LV_OBJ_FLAG_HIDDEN); }

static void show_value(void)
{
    lv_label_set_text_fmt(value_label, "%.2f%s", (double)edited, dirty ? " *" : "");
}

static void select_event(lv_event_t *event)
{
    (void)event;
    selected = lv_dropdown_get_selected(selector);
    if (selected >= UT_ECHO_PARAMETER_COUNT) selected = 0;
    edited = current.parameters[selected];
    dirty = false;
    deadline = 0;
    show_value();
}

static void dropdown_event(lv_event_t *event)
{
    lv_obj_t *list = lv_dropdown_get_list(lv_event_get_target(event));
    if (list) {
        lv_obj_set_style_text_font(list, &ut_font_14, 0);
        lv_obj_set_style_text_color(list, lv_color_hex(0xD9E6EC), 0);
        lv_obj_set_style_bg_color(list, lv_color_hex(0x244251), 0);
        lv_obj_set_style_bg_opa(list, LV_OPA_COVER, 0);
    }
}

static void adjust_event(lv_event_t *event)
{
    if (!current.fresh) { ut_echo_setup_ui_notice("板端声学状态未就绪，不修改参数"); return; }
    int direction = (int)(intptr_t)lv_event_get_user_data(event);
    edited = fmaxf(minimums[selected], fminf(maximums[selected], edited + steps[selected] * direction));
    dirty = true;
    deadline = 0;
    show_value();
}

static void command_event(lv_event_t *event)
{
    ut_echo_action_t action = (ut_echo_action_t)(uintptr_t)lv_event_get_user_data(event);
    if (!current.fresh || !action_handler) { ut_echo_setup_ui_notice("板端声学状态未就绪，不发送请求"); return; }
    if (action == UT_ECHO_EDIT) {
        if (!dirty) return;
        action_handler(action, selected, edited);
        dirty = false;
        ut_echo_setup_ui_notice("已请求更新草案；草案不会自动变成有效标定");
        return;
    }
    if (dirty) { ut_echo_setup_ui_notice("请先更新当前项，或重新选择以放弃编辑"); return; }
    if (action == UT_ECHO_APPROVE && current.phase != 2) { ut_echo_setup_ui_notice("参考检查尚未通过，禁止批准标定"); return; }
    uint32_t now = lv_tick_get();
    if (!deadline || now > deadline || pending_action != action) {
        pending_action = action;
        deadline = now + 5000;
        ut_echo_setup_ui_notice(action == UT_ECHO_REFERENCE ? "确认完整块、垂直耦合及厚度声速已核对：5秒内再按采完整块参考" :
                               action == UT_ECHO_APPROVE ? "人工核对参考与前段干扰：5秒内再按审核并应用；应用会停采" :
                               "将停采并撤销标定：5秒内再按应用未标定；需手动重启波形");
        return;
    }
    deadline = 0;
    action_handler(action, 0, 0);
}

void ut_echo_setup_ui_update(const ut_echo_setup_t *state)
{
    if (!overlay || !state) return;
    current = *state;
    if (!dirty) { edited = current.parameters[selected]; show_value(); }
    if (!current.fresh) {
        lv_label_set_text(status_label, "板端声学状态失效\n不会自动应用、采集或发射");
        return;
    }
    const char *reasons[] = {"草案未应用", "请先在视觉页开启真实波形", "门区或零点不合理：不能标定", "底波不足：检查耦合与门区",
        "完整块缺陷门超限：存在前段干扰", "安静门不安静：重选基线", "参考不稳定：停稳后重采", "参考过期：重新采集",
        "请先新建空工件，禁止混用标定", "未标定参数已应用：手动重启波形", "增益改变：重新采参考", "8帧检查通过：仍需人工审核",
        "标定已应用：手动重启波形", "削顶或过量程：检查增益与链路"};
    lv_label_set_text_fmt(status_label, "%s\n参考 %u / %u   增益 0x%02X\n缺陷门最大 %.1f   底波最小 %.1f\n噪声 %.2f   零点建议 %.3f us\n实际：%s / %s",
        current.reason <= 13 ? reasons[current.reason] : "状态未知", current.collected, current.target, current.gain,
        (double)current.flaw, (double)current.wall, (double)current.noise, (double)current.proposed_zero,
        current.flags & 2U ? "已标定" : "未标定", current.flags & 4U ? "基线已配置" : "基线未配置");
    const float *values = current.parameters;
    lv_label_set_text_fmt(summary_label, "草案门区(us)：缺陷 %.2f~%.2f / 底波 %.2f~%.2f / 安静 %.1f~%.1f\n阈值 %.0f / %.0f码  声速 %.0f m/s  厚度 %.2f mm  零点 %.3f us",
        (double)values[0], (double)values[1], (double)values[2], (double)values[3], (double)values[4], (double)values[5],
        (double)values[6], (double)values[7], (double)values[8], (double)values[10], (double)values[9]);
    if (lv_tick_get() >= notice_until) lv_label_set_text(hint_label, "不掩盖浅层干扰。标定与扫描数据仅在RAM；断电丢失。三维仅为反射指示点。");
}

void ut_echo_setup_ui_init(ut_echo_action_cb_t callback)
{
    action_handler = callback;
    memset(&current, 0, sizeof(current));
    selected = 0;
    dirty = false;
    deadline = notice_until = 0;
    button(lv_screen_active(), "声学设置", 682, 68, 106, open_event, NULL);
    overlay = lv_obj_create(lv_screen_active());
    lv_obj_remove_style_all(overlay);
    lv_obj_set_pos(overlay, 12, 114);
    lv_obj_set_size(overlay, 776, 352);
    lv_obj_set_style_bg_color(overlay, lv_color_hex(0x142431), 0);
    lv_obj_set_style_bg_opa(overlay, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(overlay, 9, 0);
    lv_obj_remove_flag(overlay, LV_OBJ_FLAG_SCROLLABLE);
    label(overlay, "声学设置 / 完整块参考", 16, 8, 580);
    button(overlay, "返回", 676, 0, 84, close_event, NULL);
    label(overlay, "先明确开启真实波形；完整块上垂直耦合、停稳。厚度与声速由操作者核对，检查通过仍须审核。", 16, 45, 740);
    selector = lv_dropdown_create(overlay);
    lv_obj_set_pos(selector, 16, 88);
    lv_obj_set_size(selector, 320, 34);
    lv_obj_set_style_text_font(selector, &ut_font_14, 0);
    lv_obj_set_style_text_color(selector, lv_color_hex(0xD9E6EC), 0);
    lv_obj_set_style_bg_color(selector, lv_color_hex(0x244251), 0);
    lv_obj_set_style_bg_opa(selector, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(selector, 1, 0);
    lv_obj_set_style_border_color(selector, lv_color_hex(0x46707E), 0);
    lv_obj_add_event_cb(selector, dropdown_event, LV_EVENT_READY, NULL);
    lv_dropdown_set_options(selector, "缺陷门起点 us\n缺陷门终点 us\n底波门起点 us\n底波门终点 us\n安静门起点 us\n安静门终点 us\n缺陷阈值 ADC码\n底波阈值 ADC码\n参考声速 m/s\n手动零点 us\n参考块厚度 mm");
    lv_obj_add_event_cb(selector, select_event, LV_EVENT_VALUE_CHANGED, NULL);
    value_label = label(overlay, "--", 16, 136, 140);
    lv_obj_set_style_text_font(value_label, &ut_font_20, 0);
    button(overlay, "-", 170, 130, 70, adjust_event, (void *)(intptr_t)-1);
    button(overlay, "+", 262, 130, 70, adjust_event, (void *)(intptr_t)1);
    button(overlay, "更新当前项", 16, 184, 152, command_event, (void *)(uintptr_t)UT_ECHO_EDIT);
    button(overlay, "应用未标定", 184, 184, 152, command_event, (void *)(uintptr_t)UT_ECHO_PREVIEW);
    status_label = label(overlay, "正在读取板端声学状态", 362, 88, 398);
    summary_label = label(overlay, "参数尚未读取", 16, 232, 740);
    button(overlay, "采完整块参考", 16, 296, 184, command_event, (void *)(uintptr_t)UT_ECHO_REFERENCE);
    button(overlay, "审核并应用", 216, 296, 168, command_event, (void *)(uintptr_t)UT_ECHO_APPROVE);
    hint_label = label(overlay, "不自动开始采集或发射", 404, 291, 354);
    lv_obj_add_flag(overlay, LV_OBJ_FLAG_HIDDEN);
}
