#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#include <sys/stat.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ut_ui.h"
#include "ut_board_demo.h"

static uint32_t framebuffer[800 * 480];
static uint32_t draw_buffer[800 * 64];
static HWND main_window;
static lv_indev_t *desktop_pointer;
static bool mouse_pressed, quit_requested, demo_mode, demo_reflector, watch_mode;
static bool scan_mode;
static bool board_demo_mode;
static ut_scan_t board_scan;
static const char *map_snapshot_path;
static ut_map_t displayed_map;
static int mouse_x, mouse_y;
static char csv_path[MAX_PATH];
static char scan_summary_path[MAX_PATH];
static ut_frame_t frame;
static time_t last_file_time;
static long last_file_size;
static BITMAPINFO bitmap_info;

static uint32_t tick_ms(void) { return (uint32_t)GetTickCount(); }

static bool read_map_snapshot(void)
{
    char error[160];
    if (!ut_map_load_csv(map_snapshot_path, &displayed_map, error, sizeof(error))) {
        ut_ui_notify(error);
        return false;
    }
    ut_ui_update_map(&displayed_map);
    return true;
}

static void flush_display(lv_display_t *display, const lv_area_t *area, uint8_t *pixel_map)
{
    unsigned width = (unsigned)(area->x2 - area->x1 + 1);
    uint32_t *source = (uint32_t *)pixel_map;
    for (int row = area->y1; row <= area->y2; row++) {
        memcpy(&framebuffer[row * 800 + area->x1], source, width * sizeof(uint32_t));
        source += width;
    }
    if (main_window && lv_display_flush_is_last(display)) InvalidateRect(main_window, NULL, FALSE);
    lv_display_flush_ready(display);
}

static void read_pointer(lv_indev_t *input, lv_indev_data_t *data)
{
    (void)input;
    data->point.x = mouse_x;
    data->point.y = mouse_y;
    data->state = mouse_pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
}

static void read_csv_file(bool force)
{
    struct _stat64 metadata;
    if (!csv_path[0] || _stat64(csv_path, &metadata) != 0) {
        if (frame.source == UT_SOURCE_LIVE) { frame.source = UT_SOURCE_RECORDED; ut_ui_update(&frame); ut_ui_notify("Capture file unavailable; displaying the last recorded frame."); }
        return;
    }
    if (force || watch_mode || metadata.st_mtime != last_file_time || metadata.st_size != last_file_size) {
        ut_frame_t incoming;
        char error[128];
        if (!ut_load_csv(csv_path, &incoming, error, sizeof(error))) {
            if (frame.source == UT_SOURCE_LIVE && difftime(time(NULL), last_file_time) > 3.0) { frame.source = UT_SOURCE_RECORDED; ut_ui_update(&frame); }
            ut_ui_notify(error); return;
        }
        frame = incoming;
        last_file_time = metadata.st_mtime;
        last_file_size = (long)metadata.st_size;
    }
    if (frame.source != UT_SOURCE_DEMO) {
        frame.source = UT_SOURCE_RECORDED;
    }
    ut_ui_update(&frame);
}

static bool load_scan_point(void)
{
    const ut_scan_t *scan = ut_ui_scan();
    size_t index = ut_ui_scan_index();
    if (index >= scan->count || !scan_summary_path[0]) return false;
    char path[MAX_PATH], error[160];
    ut_frame_t incoming = {0};
    demo_mode = watch_mode = false;
    scan_mode = true;
    if (!ut_scan_waveform_path(scan_summary_path, &scan->points[index], path, sizeof(path)) ||
        !ut_load_csv(path, &incoming, error, sizeof(error))) {
        memset(&frame, 0, sizeof(frame));
        ut_ui_update(&frame);
        ut_ui_notify("Selected scan waveform unavailable; no previous waveform is substituted.");
        return false;
    }
    incoming.source = scan->points[index].source == UT_SOURCE_DEMO || incoming.source == UT_SOURCE_DEMO ? UT_SOURCE_DEMO : UT_SOURCE_RECORDED;
    frame = incoming;
    ut_ui_update(&frame);
    return true;
}

static bool load_scan_summary(const char *path)
{
    if (ut_ui_is_paused()) { ut_ui_notify("Resume the display before opening another scan."); return false; }
    ut_scan_t incoming;
    char error[160];
    if (strlen(path) >= sizeof(scan_summary_path)) { ut_ui_notify("Scan path is too long."); return false; }
    if (!ut_scan_load_csv(path, &incoming, error, sizeof(error))) { ut_ui_notify(error); return false; }
    snprintf(scan_summary_path, sizeof(scan_summary_path), "%s", path);
    demo_mode = watch_mode = false;
    scan_mode = true;
    ut_ui_update_scan(&incoming);
    load_scan_point();
    return true;
}

static void choose_scan(void)
{
    char selected[MAX_PATH] = "";
    OPENFILENAMEA dialog = {0};
    dialog.lStructSize = sizeof(dialog);
    dialog.hwndOwner = main_window;
    dialog.lpstrFilter = "Inspection scan summary (summary.csv)\0*.csv\0";
    dialog.lpstrFile = selected;
    dialog.nMaxFile = sizeof(selected);
    dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
    if (GetOpenFileNameA(&dialog)) load_scan_summary(selected);
}

static void choose_csv(void)
{
    char selected[MAX_PATH] = "";
    OPENFILENAMEA dialog = {0};
    dialog.lStructSize = sizeof(dialog);
    dialog.hwndOwner = main_window;
    dialog.lpstrFilter = "Vivado ILA CSV\0*.csv\0All files\0*.*\0";
    dialog.lpstrFile = selected;
    dialog.nMaxFile = sizeof(selected);
    dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
    if (GetOpenFileNameA(&dialog)) {
        snprintf(csv_path, sizeof(csv_path), "%s", selected);
        demo_mode = false;
        watch_mode = false;
        scan_mode = false;
        ut_ui_show_scan(false);
        read_csv_file(true);
    }
}

static void save_csv(void)
{
    if (!frame.count) { ut_ui_notify("No captured frame to export."); return; }
    char selected[MAX_PATH] = "capture.csv";
    OPENFILENAMEA dialog = {0};
    dialog.lStructSize = sizeof(dialog);
    dialog.hwndOwner = main_window;
    dialog.lpstrFilter = "Vivado ILA CSV\0*.csv\0";
    dialog.lpstrFile = selected;
    dialog.nMaxFile = sizeof(selected);
    dialog.lpstrDefExt = "csv";
    dialog.Flags = OFN_OVERWRITEPROMPT | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
    if (GetSaveFileNameA(&dialog)) ut_ui_notify(ut_save_csv(selected, ut_ui_frame()) ? "CSV exported." : "CSV export failed.");
}

static void handle_action(ut_action_t action)
{
    if (action >= UT_ACTION_TOUCH_RETRY) { ut_ui_notify("Desktop preview / touch GPIO is only available on the touch board platform"); return; }
    if (board_demo_mode) {
        if (action == UT_ACTION_DEMO) ut_ui_show_map(true);
        else if (action == UT_ACTION_SCAN_POINT) {
            ut_generate_demo(&frame, 0, board_scan.points[ut_ui_scan_index()].status == UT_SCAN_SUSPECT);
            ut_ui_update(&frame);
        } else ut_ui_notify("Board DEMO preview / no hardware or filesystem action");
        return;
    }
    if (action == UT_ACTION_SCAN_OPEN) choose_scan();
    if (action == UT_ACTION_SCAN_POINT) load_scan_point();
    if (action == UT_ACTION_OPEN) choose_csv();
    if (action == UT_ACTION_EXPORT) save_csv();
    if (action == UT_ACTION_DEMO) {
        scan_mode = false;
        ut_ui_show_scan(false);
        demo_mode = !demo_mode;
        if (demo_mode) { ut_generate_demo(&frame, 0, demo_reflector); ut_ui_update(&frame); }
        else if (csv_path[0]) read_csv_file(true);
        else { memset(&frame, 0, sizeof(frame)); ut_ui_update(&frame); }
    }
}

static LRESULT CALLBACK window_callback(HWND window, UINT message, WPARAM word_param, LPARAM long_param)
{
    if (message == WM_DESTROY) { quit_requested = true; PostQuitMessage(0); return 0; }
    if (message == WM_ERASEBKGND) return 1;
    if (message == WM_KILLFOCUS || message == WM_CAPTURECHANGED || message == WM_CANCELMODE) {
        mouse_pressed = false;
        if (desktop_pointer) lv_indev_reset(desktop_pointer, NULL);
        if (GetCapture() == window) ReleaseCapture();
        return 0;
    }
    if (message == WM_LBUTTONDOWN) { mouse_pressed = true; SetCapture(window); }
    if (message == WM_LBUTTONUP) { mouse_pressed = false; ReleaseCapture(); }
    if (message == WM_MOUSEMOVE || message == WM_LBUTTONDOWN || message == WM_LBUTTONUP) {
        RECT client;
        GetClientRect(window, &client);
        if (client.right > 0 && client.bottom > 0) {
            mouse_x = (int)(short)LOWORD(long_param) * 800 / client.right;
            mouse_y = (int)(short)HIWORD(long_param) * 480 / client.bottom;
        }
        return 0;
    }
    if (message == WM_PAINT) {
        PAINTSTRUCT paint;
        HDC context = BeginPaint(window, &paint);
        RECT client;
        GetClientRect(window, &client);
        SetStretchBltMode(context, HALFTONE);
        StretchDIBits(context, 0, 0, client.right, client.bottom, 0, 0, 800, 480,
                      framebuffer, &bitmap_info, DIB_RGB_COLORS, SRCCOPY);
        EndPaint(window, &paint);
        return 0;
    }
    return DefWindowProcW(window, message, word_param, long_param);
}

static bool save_bitmap(const char *path)
{
    FILE *output = fopen(path, "wb");
    if (!output) { perror(path); return false; }
    BITMAPFILEHEADER header = {0};
    header.bfType = 0x4D42;
    header.bfOffBits = sizeof(header) + sizeof(BITMAPINFOHEADER);
    header.bfSize = header.bfOffBits + sizeof(framebuffer);
    bool success = fwrite(&header, sizeof(header), 1, output) == 1;
    success = fwrite(&bitmap_info.bmiHeader, sizeof(BITMAPINFOHEADER), 1, output) == 1 && success;
    success = fwrite(framebuffer, sizeof(framebuffer), 1, output) == 1 && success;
    if (!success) perror(path);
    return fclose(output) == 0 && success;
}

int main(int argument_count, char **arguments)
{
    SetConsoleOutputCP(CP_UTF8);
    const char *screenshot_path = NULL; bool show_settings = false;
    bool show_touch = false, map_3d_argument = false;
    const char *scan_argument = NULL;
    size_t initial_scan_point = 0;
    bool point_requested = false;
    bool scan_review = false;
    for (int index = 1; index < argument_count; index++) {
        if (!strcmp(arguments[index], "--settings")) show_settings = true; else if (!strcmp(arguments[index], "--demo")) demo_mode = true;
        else if (!strcmp(arguments[index], "--touch-diagnostics")) show_touch = true;
        else if (!strcmp(arguments[index], "--map-3d")) map_3d_argument = true;
        else if (!strcmp(arguments[index], "--board-demo")) board_demo_mode = true;
        else if (!strcmp(arguments[index], "--watch")) watch_mode = true; else if (!strcmp(arguments[index], "--reflector")) demo_reflector = true;
        else if (!strcmp(arguments[index], "--csv") && index + 1 < argument_count) snprintf(csv_path, sizeof(csv_path), "%s", arguments[++index]);
        else if (!strcmp(arguments[index], "--screenshot") && index + 1 < argument_count) screenshot_path = arguments[++index];
        else if (!strcmp(arguments[index], "--scan") && index + 1 < argument_count) scan_argument = arguments[++index];
        else if (!strcmp(arguments[index], "--map") && index + 1 < argument_count) map_snapshot_path = arguments[++index];
        else if (!strcmp(arguments[index], "--scan-review")) scan_review = true;
        else if (!strcmp(arguments[index], "--point") && index + 1 < argument_count) {
            char *end;
            const char *value = arguments[++index];
            unsigned long parsed = strtoul(value, &end, 10);
            if (!value[0] || value[0] == '-' || *end || parsed >= UT_SCAN_CAPACITY) { fprintf(stderr, "Invalid zero-based scan point index\n"); return 2; }
            initial_scan_point = parsed;
            point_requested = true;
        }
        else { fprintf(stderr, "Usage: ut_lvgl.exe [--csv file.csv | --demo [--reflector] | --scan summary.csv --point 0 | --map map.csv | --board-demo] [--map-3d] [--touch-diagnostics | --settings] [--screenshot image.bmp]\n"); return 2; }
    }
    unsigned source_count = (board_demo_mode ? 1U : 0U) + (map_snapshot_path ? 1U : 0U) + (scan_argument ? 1U : 0U) + (demo_mode ? 1U : 0U) + (csv_path[0] ? 1U : 0U);
    if (source_count > 1U || (watch_mode && !csv_path[0]) ||
        (board_demo_mode && (map_snapshot_path || scan_argument || scan_review || demo_mode || watch_mode || csv_path[0] || point_requested)) ||
        (map_3d_argument && !map_snapshot_path && !board_demo_mode) || (demo_reflector && !demo_mode) ||
        (show_settings && show_touch) ||
        (map_snapshot_path && (scan_argument || scan_review || demo_mode || watch_mode || csv_path[0] || point_requested)) ||
        (scan_argument && (demo_mode || watch_mode || csv_path[0])) || (point_requested && !scan_argument)) {
        fprintf(stderr, "Choose one data source; --watch requires --csv and never proves LIVE acquisition; --map-3d requires --map or --board-demo; --reflector requires --demo; settings and touch diagnostics are separate overlays\n"); return 2;
    }
    bitmap_info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bitmap_info.bmiHeader.biWidth = 800;
    bitmap_info.bmiHeader.biHeight = -480;
    bitmap_info.bmiHeader.biPlanes = 1;
    bitmap_info.bmiHeader.biBitCount = 32;
    bitmap_info.bmiHeader.biCompression = BI_RGB;
    lv_init();
    lv_tick_set_cb(tick_ms);
    lv_display_t *display = lv_display_create(800, 480);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, flush_display);
    lv_indev_t *pointer = lv_indev_create();
    desktop_pointer = pointer;
    lv_indev_set_type(pointer, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(pointer, read_pointer);
    ut_ui_create(handle_action);
    if (board_demo_mode) {
        ut_generate_demo(&frame, 0, true);
        ut_ui_update(&frame);
        ut_board_demo_create(&displayed_map, &board_scan);
        ut_ui_update_scan(&board_scan);
        ut_ui_update_map(&displayed_map);
        ut_ui_show_map(true);
    }
    else if (map_snapshot_path) {
        if (!read_map_snapshot()) { lv_deinit(); return 2; }
        ut_ui_show_map(true);
    }
    else if (scan_argument) {
        if (!load_scan_summary(scan_argument) || !ut_ui_select_scan_point(initial_scan_point)) { lv_deinit(); return 2; }
    }
    else if (demo_mode) { ut_generate_demo(&frame, 0, demo_reflector); ut_ui_update(&frame); }
    else if (csv_path[0]) read_csv_file(true);
    if (scan_review) ut_ui_show_scan(true);
    if (show_settings) ut_ui_open_settings();
    if (show_touch) ut_ui_open_touch();
    if (map_3d_argument) ut_ui_set_map_3d(true);
    lv_refr_now(display);
    if (screenshot_path) {
        bool success = save_bitmap(screenshot_path);
        printf("Screenshot%s: %s / source=%u / samples=%zu\n", success ? "" : " FAILED", screenshot_path, frame.source, frame.count);
        if (map_snapshot_path || board_demo_mode) {
            const ut_map_t *map = ut_ui_map();
            printf("Map: source=%s / sampled=%u / suspect=%u / depth=%u / points=%u\n", map->source,
                   map->sampled_cells, map->suspect_cells, map->depth_cells, map->point_count);
        }
        lv_deinit();
        return success ? 0 : 1;
    }
    SetProcessDPIAware();
    HINSTANCE instance = GetModuleHandleW(NULL);
    WNDCLASSW window_class = {0};
    window_class.lpfnWndProc = window_callback;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursor(NULL, IDC_ARROW);
    window_class.lpszClassName = L"UTLVGLWindow";
    RegisterClassW(&window_class);
    RECT window_rectangle = {0, 0, 1200, 720};
    AdjustWindowRect(&window_rectangle, WS_OVERLAPPEDWINDOW, FALSE);
    main_window = CreateWindowW(window_class.lpszClassName, L"Ultrasonic Studio | LVGL | 800 x 480",
                                 WS_OVERLAPPEDWINDOW, CW_USEDEFAULT, CW_USEDEFAULT,
                                 window_rectangle.right - window_rectangle.left,
                                 window_rectangle.bottom - window_rectangle.top,
                                 NULL, NULL, instance, NULL);
    if (!main_window) return 1;
    ShowWindow(main_window, SW_SHOW);
    unsigned phase = 0;
    uint32_t last_update = 0;
    while (!quit_requested) {
        MSG message;
        while (PeekMessageW(&message, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&message); DispatchMessageW(&message); }
        if (tick_ms() - last_update >= 150) {
            if (board_demo_mode) { ut_generate_demo(&frame, phase++, true); ut_ui_update(&frame); }
            else if (map_snapshot_path) read_map_snapshot();
            else if (demo_mode) { ut_generate_demo(&frame, phase++, demo_reflector); ut_ui_update(&frame); }
            else if (!scan_mode) read_csv_file(false);
            last_update = tick_ms();
        }
        lv_timer_handler();
        Sleep(5);
    }
    lv_deinit();
    return 0;
}
