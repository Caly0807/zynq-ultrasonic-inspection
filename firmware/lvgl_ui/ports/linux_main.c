#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "ut_linux_abi.h"
#include "ut_ui.h"
#include "ut_board_demo.h"
#include "ut_rgb.h"
#include "linux_touch.h"

static volatile sig_atomic_t running = 1;
static uint8_t *pixels;
static uint32_t draw_buffer[UT_LCD_WIDTH * 48U];
static ut_frame_t frame;
static ut_map_t scan_map;
static ut_scan_t line_scan;
static struct timespec epoch;
static const char *waveform_path, *map_path, *status_path;
static uint64_t flushes;
static bool allow_demo;
static struct stat waveform_stamp, map_stamp;
static bool waveform_seen, map_seen;

static bool changed(const char *path, const struct stat *previous, bool seen, struct stat *current)
{
    return path && stat(path, current) == 0 && (!seen || current->st_ino != previous->st_ino ||
        current->st_size != previous->st_size || current->st_mtim.tv_sec != previous->st_mtim.tv_sec ||
        current->st_mtim.tv_nsec != previous->st_mtim.tv_nsec);
}

static uint32_t milliseconds(void)
{
    struct timespec current;
    clock_gettime(CLOCK_MONOTONIC, &current);
    return (uint32_t)((current.tv_sec - epoch.tv_sec) * 1000 + (current.tv_nsec - epoch.tv_nsec) / 1000000);
}

static void stop_handler(int signal_number)
{
    (void)signal_number;
    running = 0;
}

static void flush_display(lv_display_t *display, const lv_area_t *area, uint8_t *pixel_map)
{
    if (area->x1 < 0 || area->y1 < 0 || area->x2 >= (int)UT_LCD_WIDTH || area->y2 >= (int)UT_LCD_HEIGHT ||
        area->x1 > area->x2 || area->y1 > area->y2) {
        running = 0;
        lv_display_flush_ready(display);
        return;
    }
    const uint32_t *source = (const uint32_t *)pixel_map;
    size_t width = (size_t)(area->x2 - area->x1 + 1);
    for (int row = area->y1; row <= area->y2; row++) {
        ut_rgb_copy_row(pixels + (size_t)row * UT_LCD_STRIDE + (size_t)area->x1 * 3U, source, width);
        source += width;
    }
    lv_display_flush_ready(display);
    flushes++;
}

static void publish_runtime_status(bool headless)
{
    char temporary[1024];
    if (!status_path || snprintf(temporary, sizeof(temporary), "%s.tmp", status_path) >= (int)sizeof(temporary)) return;
    int descriptor = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
    FILE *output = descriptor < 0 ? NULL : fdopen(descriptor, "w");
    if (!output) { if (descriptor >= 0) close(descriptor); return; }
    struct timespec current;
    clock_gettime(CLOCK_MONOTONIC, &current);
    bool written = fprintf(output,
        "{\"schema\":\"ut-lcd-status/1\",\"mode\":\"%s\",\"updated_monotonic_ns\":%llu,"
        "\"flushes\":%llu,\"map_points\":%u,\"waveform_source\":%u,\"touch\":",
        headless ? "HEADLESS_ONLY" : "LCD_TRANSPORT", (unsigned long long)current.tv_sec * 1000000000ULL + current.tv_nsec,
        (unsigned long long)flushes, (unsigned)scan_map.point_count, (unsigned)frame.source) > 0;
    written = ut_linux_touch_write_status(output) && written;
    written = fputs("}\n", output) >= 0 && written;
    int closed = fclose(output);
    if (written && !closed) rename(temporary, status_path);
}

static void reload_files(void)
{
    char error[160];
    ut_frame_t incoming;
    ut_map_t incoming_map;
    struct stat current;
    if (changed(waveform_path, &waveform_stamp, waveform_seen, &current) &&
        ut_load_csv(waveform_path, &incoming, error, sizeof(error))) {
        waveform_seen = true;
        waveform_stamp = current;
        frame = incoming;
        ut_ui_update(&frame);
    }
    if (changed(map_path, &map_stamp, map_seen, &current) && ut_map_load_csv(map_path, &incoming_map, error, sizeof(error))) {
        map_seen = true;
        map_stamp = current;
        scan_map = incoming_map;
        ut_ui_update_map(&scan_map);
        if (!scan_map.point_count) {
            memset(&frame, 0, sizeof(frame));
            frame.source = UT_SOURCE_WAITING;
            ut_ui_update(&frame);
            waveform_seen = false;
        }
    }
}

static void handle_action(ut_action_t action)
{
    if (action == UT_ACTION_DEMO && allow_demo) {
        ut_generate_demo(&frame, milliseconds() / 250U, true);
        strcpy(frame.source_name, "Linux / synthetic DEMO / HV OFF");
        ut_ui_update(&frame);
    } else if (action == UT_ACTION_OPEN || action == UT_ACTION_SCAN_OPEN) {
        reload_files();
        ut_ui_notify("Board service files refreshed / no hardware trigger");
    } else if (action == UT_ACTION_WAIT) {
        memset(&frame, 0, sizeof(frame));
        frame.source = UT_SOURCE_WAITING;
        ut_ui_update(&frame);
    } else if (action == UT_ACTION_EXPORT) {
        ut_ui_notify("Export the complete report from the PC dashboard");
    } else if (action >= UT_ACTION_TOUCH_RETRY) {
        ut_linux_touch_action(action);
    }
}

static bool save_ppm(const char *path)
{
    FILE *output = fopen(path, "wb");
    if (!output) return false;
    bool success = fprintf(output, "P6\n800 480\n255\n") > 0;
    for (size_t offset = 0; success && offset < UT_LCD_BYTES; offset += 3U) {
        uint8_t rgb[3] = {pixels[offset + 2U], pixels[offset], pixels[offset + 1U]};
        success = fwrite(rgb, 1, sizeof(rgb), output) == sizeof(rgb);
    }
    return fclose(output) == 0 && success;
}

int main(int argument_count, char **arguments)
{
    const char *device = "/dev/ut_board", *headless_output = NULL, *view = "waveform";
    const char *touch_path = NULL;
    unsigned frames = 0;
    int descriptor = -1, result = 1;
    for (int index = 1; index < argument_count; index++) {
        if (!strcmp(arguments[index], "--demo")) { allow_demo = true; continue; }
        if (index + 1 >= argument_count) { fprintf(stderr, "Missing argument value\n"); return 2; }
        const char *option = arguments[index++], *value = arguments[index];
        if (!strcmp(option, "--device")) device = value;
        else if (!strcmp(option, "--headless-out")) headless_output = value;
        else if (!strcmp(option, "--waveform")) waveform_path = value;
        else if (!strcmp(option, "--map")) map_path = value;
        else if (!strcmp(option, "--touch-i2c")) touch_path = value;
        else if (!strcmp(option, "--status")) status_path = value;
        else if (!strcmp(option, "--view")) view = value;
        else if (!strcmp(option, "--frames")) {
            char *end;
            errno = 0;
            unsigned long parsed = strtoul(value, &end, 10);
            if (errno || !*value || *end || !parsed || parsed > 60000U) return 2;
            frames = (unsigned)parsed;
        } else { fprintf(stderr, "Unknown option: %s\n", option); return 2; }
    }
    if (strcmp(view, "waveform") && strcmp(view, "map") && strcmp(view, "3d")) return 2;
    if (headless_output) {
        if (!frames) frames = 40;
        pixels = calloc(1, UT_LCD_BYTES);
    } else {
        struct ut_board_status status;
        descriptor = open(device, O_RDWR | O_CLOEXEC);
        if (descriptor < 0 || ioctl(descriptor, UT_BOARD_STATUS, &status) ||
            status.magic != UT_BOARD_MAGIC || status.version != 1 || status.width != UT_LCD_WIDTH ||
            status.height != UT_LCD_HEIGHT || status.stride != UT_LCD_STRIDE || status.pixel_format != UT_PIXEL_GRB888) {
            fprintf(stderr, "Reviewed Linux LCD transport unavailable\n");
            goto cleanup;
        }
        pixels = mmap(NULL, UT_LCD_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, descriptor, 0);
        if (pixels == MAP_FAILED) { pixels = NULL; goto cleanup; }
        memset(pixels, 0, UT_LCD_BYTES);
        if (ioctl(descriptor, UT_DISPLAY_START)) { perror("LCD start"); goto cleanup; }
    }
    if (!pixels) goto cleanup;
    signal(SIGTERM, stop_handler);
    signal(SIGINT, stop_handler);
    clock_gettime(CLOCK_MONOTONIC, &epoch);
    lv_init();
    lv_tick_set_cb(milliseconds);
    lv_display_t *display = lv_display_create(UT_LCD_WIDTH, UT_LCD_HEIGHT);
    if (!display) goto cleanup;
    lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, flush_display);
    ut_ui_create(handle_action);
    if (touch_path && !headless_output)
        printf("UT_LINUX_TOUCH identified=%u; physical acceptance still required\n", ut_linux_touch_open(touch_path));
    if (allow_demo) {
        ut_generate_demo(&frame, 0, true);
        strcpy(frame.source_name, "Linux / synthetic DEMO / HV OFF");
        ut_board_demo_create(&scan_map, &line_scan);
        ut_ui_update_scan(&line_scan);
        ut_ui_update_map(&scan_map);
    } else frame.source = UT_SOURCE_WAITING;
    ut_ui_update(&frame);
    reload_files();
    if (strcmp(view, "waveform")) {
        ut_ui_show_map(true);
        ut_ui_set_map_3d(!strcmp(view, "3d"));
    }
    printf("UT_LINUX_LVGL source=%s transport=%s no_HV_or_motion\n", allow_demo ? "DEMO" : "WAITING", headless_output ? "HEADLESS_TEST" : "PL_LCD");
    fflush(stdout);
    unsigned iterations = 0;
    while (running && (!frames || iterations < frames)) {
        lv_timer_handler();
        if (iterations % 25U == 0) reload_files();
        if (iterations % 50U == 0) publish_runtime_status(headless_output != NULL);
        struct timespec delay = {0, 10000000L};
        nanosleep(&delay, NULL);
        iterations++;
    }
    result = headless_output && !save_ppm(headless_output) ? 1 : 0;
    ut_linux_touch_close();
    lv_deinit();
cleanup:
    if (descriptor >= 0) {
        ioctl(descriptor, UT_DISPLAY_STOP);
        if (pixels) munmap(pixels, UT_LCD_BYTES);
        close(descriptor);
    } else free(pixels);
    return result;
}
