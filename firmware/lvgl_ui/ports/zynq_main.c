#include <stdint.h>
#include <string.h>
#include "xil_cache.h"
#include "xil_io.h"
#include "xil_mmu.h"
#include "xtime_l.h"
#include "sleep.h"
#include "ut_ui.h"
#include "../assets/ut_palette.h"

#define FRAMEBUFFER_BASE 0x40000000U
#define CAPTURE_BASE 0x42000000U
#define CONTROL_BASE 0x41200000U

static uint32_t draw_buffer[800 * 48] __attribute__((aligned(64)));
static ut_frame_t frame;
static XTime start_time;
static uint16_t previous_frame;

static uint32_t milliseconds(void)
{
    XTime current;
    XTime_GetTime(&current);
    return (uint32_t)((current - start_time) / (COUNTS_PER_SECOND / 1000U));
}

static uint8_t palette_index(uint32_t pixel)
{
    uint16_t rgb565 = (uint16_t)(((pixel >> 8) & 0xF800U) | ((pixel >> 5) & 0x07E0U) | ((pixel >> 3) & 0x001FU));
    return ut_rgb565_to_index[rgb565];
}

static void flush_display(lv_display_t *display, const lv_area_t *area, uint8_t *pixel_map)
{
    uint32_t *source = (uint32_t *)pixel_map;
    unsigned width = (unsigned)(area->x2 - area->x1 + 1);
    for (int row = area->y1; row <= area->y2; row++) {
        uintptr_t destination = FRAMEBUFFER_BASE + (unsigned)row * 800U + (unsigned)area->x1;
        unsigned remaining = width;
        while (remaining && (destination & 3U)) {
            Xil_Out8(destination++, palette_index(*source++));
            remaining--;
        }
        while (remaining >= 4) {
            uint32_t packed = palette_index(source[0]) | ((uint32_t)palette_index(source[1]) << 8) |
                              ((uint32_t)palette_index(source[2]) << 16) | ((uint32_t)palette_index(source[3]) << 24);
            Xil_Out32(destination, packed);
            destination += 4;
            source += 4;
            remaining -= 4;
        }
        while (remaining) {
            Xil_Out8(destination++, palette_index(*source++));
            remaining--;
        }
    }
    lv_display_flush_ready(display);
}

static void handle_action(ut_action_t action)
{
    (void)action;
    ut_ui_notify("Use the PC application for file import/export. HV is disabled by default.");
}

static void read_capture(void)
{
    uint32_t before = Xil_In32(CONTROL_BASE + 8U);
    uint16_t sequence = (uint16_t)(before >> 16);
    if ((before & 1U) || sequence == previous_frame || sequence == 0) return;
    for (size_t index = 0; index < UT_SAMPLE_CAPACITY; index++) frame.samples[index] = (uint8_t)Xil_In32(CAPTURE_BASE + index * 4U);
    uint32_t after = Xil_In32(CONTROL_BASE + 8U);
    if ((after & 1U) || (after >> 16) != sequence) return;
    frame.count = UT_SAMPLE_CAPACITY;
    frame.trigger_index = 0;
    frame.sample_period_us = 0.04f;
    frame.gain = (uint8_t)Xil_In32(CONTROL_BASE);
    frame.overrange = (after & 2U) != 0;
    frame.source = UT_SOURCE_LIVE;
    strcpy(frame.source_name, "Zynq PS / PL ADC 25 MHz");
    previous_frame = sequence;
    ut_ui_update(&frame);
}

int main(void)
{
    Xil_ICacheEnable();
    Xil_DCacheEnable();
    Xil_SetTlbAttributes(FRAMEBUFFER_BASE, DEVICE_MEMORY);
    Xil_SetTlbAttributes(CAPTURE_BASE, DEVICE_MEMORY);
    Xil_SetTlbAttributes(CONTROL_BASE, DEVICE_MEMORY);
    Xil_Out32(CONTROL_BASE + 4U, 0U);
    Xil_Out32(CONTROL_BASE, 0x00000080U);
    for (unsigned index = 0; index < 800U * 480U; index += 4U) Xil_Out32(FRAMEBUFFER_BASE + index, 0U);
    XTime_GetTime(&start_time);
    lv_init();
    lv_tick_set_cb(milliseconds);
    lv_display_t *display = lv_display_create(800, 480);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, flush_display);
    ut_ui_create(handle_action);
    lv_refr_now(display);
    Xil_Out32(CONTROL_BASE, 0x00000280U);
    while (1) {
        read_capture();
        lv_timer_handler();
        usleep(5000);
    }
}