#include <stdint.h>
#include <string.h>
#include "xil_cache.h"
#include "xil_io.h"
#include "xparameters.h"
#include "xvtc.h"
#include "xtime_l.h"
#include "sleep.h"
#include "ut_ui.h"
#include "ut_rgb.h"
#include "ut_board_demo.h"
#ifdef UT_WITH_TOUCH
#include "zynq_touch.h"
#endif

#define FRAMEBUFFER_BASE 0x10000000U
#define FRAMEBUFFER_BYTES (800U * 480U * 3U)
#define CAPTURE_BUFFER 0x02000000U
#define CAPTURE_BASE XPAR_CAPTURE_BASEADDR
#define DMA_BASE XPAR_ADC_DMA_BASEADDR
#define VDMA_BASE XPAR_DISPLAY_DMA_BASEADDR

typedef struct {
    uint32_t magic, version, phase, error;
    uint32_t heartbeat, flush_count, pixels_flushed, video_status;
    uint32_t vdma_status, control, capture_sequence, dma_words_checked;
    uint32_t ui_updates, mode, last_render_ms, maximum_render_ms;
    uint32_t touch_kind, touch_address, touch_reads, touch_errors;
    uint32_t touch_pressed, touch_raw_xy, touch_screen_xy, touch_bus_status;
    uint32_t map_cells, map_suspects, touch_flags, touch_last_command;
    uint32_t touch_probe_ack, touch_ft_version, touch_ft_chip_id, touch_product_code;
} ui_diagnostic_t;

volatile ui_diagnostic_t ut_rgb_diag __attribute__((aligned(64)));
#ifdef UT_WITH_TOUCH
volatile uint32_t ut_touch_command[16] __attribute__((aligned(64)));
#endif
static uint32_t draw_buffer[800U * 48U] __attribute__((aligned(64)));
static ut_frame_t frame;
static ut_map_t demo_map;
static ut_scan_t demo_scan;
static XVtc timing;
static XTime start_time;

static uint32_t milliseconds(void)
{
    XTime current;
    XTime_GetTime(&current);
    return (uint32_t)((current - start_time) / (COUNTS_PER_SECOND / 1000U));
}

static int failure(uint32_t error)
{
    Xil_Out32(CAPTURE_BASE + 4U, 0U);
    ut_rgb_diag.error = error;
    ut_rgb_diag.phase = 0xffU;
    return 0;
}

static int check_dma(void)
{
    Xil_Out32(DMA_BASE + 0x30U, 4U);
    uint32_t started = milliseconds();
    while (Xil_In32(DMA_BASE + 0x30U) & 4U) {
        if (milliseconds() - started > 1000U) return failure(2U);
    }
    for (unsigned capture = 0; capture < 3U; capture++) {
        for (unsigned index = 0; index < 1024U; index++) Xil_Out32(CAPTURE_BUFFER + index * 4U, 0xdeadbeefU);
        Xil_DCacheFlushRange(CAPTURE_BUFFER, 4096U);
        Xil_Out32(DMA_BASE + 0x34U, 0x7000U);
        Xil_Out32(DMA_BASE + 0x30U, 1U);
        Xil_Out32(DMA_BASE + 0x48U, CAPTURE_BUFFER);
        Xil_Out32(DMA_BASE + 0x58U, 4096U);
        Xil_Out32(CAPTURE_BASE + 4U, 0x16U);
        started = milliseconds();
        uint32_t status;
        do {
            status = Xil_In32(DMA_BASE + 0x34U);
            if ((status & 0x770U) || milliseconds() - started > 2000U) return failure(3U);
        } while (!(status & 0x1000U));
        Xil_DCacheInvalidateRange(CAPTURE_BUFFER, 4096U);
        for (unsigned index = 0; index < 1024U; index++) {
            uint32_t low = (index * 4U) & 255U;
            uint32_t expected = low | ((low + 1U) << 8) | ((low + 2U) << 16) | ((low + 3U) << 24);
            if (Xil_In32(CAPTURE_BUFFER + index * 4U) != expected) return failure(4U);
            ut_rgb_diag.dma_words_checked++;
        }
    }
    ut_rgb_diag.capture_sequence = Xil_In32(CAPTURE_BASE + 0x1cU);
    Xil_Out32(CAPTURE_BASE + 4U, 0U);
    return 1;
}

static void flush_display(lv_display_t *display, const lv_area_t *area, uint8_t *pixel_map)
{
    if (area->x1 < 0 || area->y1 < 0 || area->x2 >= 800 || area->y2 >= 480 ||
        area->x2 < area->x1 || area->y2 < area->y1) {
        failure(5U);
        lv_display_flush_ready(display);
        return;
    }
    uint32_t *source = (uint32_t *)pixel_map;
    size_t width = (size_t)(area->x2 - area->x1 + 1);
    for (int row = area->y1; row <= area->y2; row++) {
        uintptr_t destination = FRAMEBUFFER_BASE + ((unsigned)row * 800U + (unsigned)area->x1) * 3U;
        ut_rgb_copy_row((uint8_t *)destination, source, width);
        Xil_DCacheFlushRange(destination, width * 3U);
        source += width;
    }
    ut_rgb_diag.flush_count++;
    ut_rgb_diag.pixels_flushed += width * (unsigned)(area->y2 - area->y1 + 1);
    lv_display_flush_ready(display);
}

static int start_display(void)
{
    XVtc_Config *configuration = XVtc_LookupConfig(XPAR_TIMING_DEVICE_ID);
    if (!configuration || XVtc_CfgInitialize(&timing, configuration, configuration->BaseAddress) != 0) return failure(6U);
    XVtc_Timing parameters = {0};
    parameters.HActiveVideo = 800U;
    parameters.HFrontPorch = 40U;
    parameters.HSyncWidth = 48U;
    parameters.HBackPorch = 88U;
    parameters.VActiveVideo = 480U;
    parameters.V0FrontPorch = 13U;
    parameters.V0SyncWidth = 3U;
    parameters.V0BackPorch = 32U;
    parameters.V1FrontPorch = 13U;
    parameters.V1SyncWidth = 3U;
    parameters.V1BackPorch = 32U;
    XVtc_SetGeneratorTiming(&timing, &parameters);
    XVtc_SourceSelect source;
    memset(&source, 1, sizeof(source));
    source.InterlacedMode = 0U;
    XVtc_SetSource(&timing, &source);
    XVtc_RegUpdateEnable(&timing);
    XVtc_EnableGenerator(&timing);
    Xil_Out32(VDMA_BASE, 4U);
    uint32_t started = milliseconds();
    while (Xil_In32(VDMA_BASE) & 4U) {
        if (milliseconds() - started > 1000U) return failure(7U);
    }
    Xil_Out32(VDMA_BASE + 4U, 0xffffffffU);
    Xil_Out32(VDMA_BASE + 0x28U, 0U);
    Xil_Out32(VDMA_BASE, 1U);
    Xil_Out32(VDMA_BASE + 0x5cU, FRAMEBUFFER_BASE);
    Xil_Out32(VDMA_BASE + 0x60U, FRAMEBUFFER_BASE);
    Xil_Out32(VDMA_BASE + 0x64U, FRAMEBUFFER_BASE);
    Xil_Out32(VDMA_BASE + 0x58U, 2400U);
    Xil_Out32(VDMA_BASE + 0x54U, 2400U);
    Xil_Out32(VDMA_BASE + 0x50U, 480U);
    Xil_Out32(XPAR_BACKLIGHT_BASEADDR + 4U, 0U);
    Xil_Out32(XPAR_BACKLIGHT_BASEADDR, 1U);
    started = milliseconds();
    while (!(Xil_In32(CAPTURE_BASE + 8U) & 0x200U)) {
        if (milliseconds() - started > 1000U) return failure(8U);
    }
    Xil_Out32(CAPTURE_BASE + 4U, 4U);
    usleep(300000U);
    ut_rgb_diag.video_status = Xil_In32(CAPTURE_BASE + 8U);
    ut_rgb_diag.vdma_status = Xil_In32(VDMA_BASE + 4U);
    if ((ut_rgb_diag.video_status & 0x600U) != 0x200U || (ut_rgb_diag.vdma_status & 0xff1U)) return failure(9U);
    return 1;
}

static void update_demo(void)
{
    ut_generate_demo(&frame, ut_rgb_diag.ui_updates, true);
    strcpy(frame.source_name, "synthetic / PS RGB / HV OFF");
    ut_ui_update(&frame);
    ut_rgb_diag.ui_updates++;
}

static void handle_action(ut_action_t action)
{
#ifdef UT_WITH_TOUCH
    if (action >= UT_ACTION_TOUCH_RETRY && action <= UT_ACTION_TOUCH_RESET) { ut_zynq_touch_action(action); return; }
#endif
    if (action == UT_ACTION_SCAN_POINT) {
        size_t index = ut_ui_scan_index();
        ut_generate_demo(&frame, ut_rgb_diag.ui_updates, demo_scan.points[index].status == UT_SCAN_SUSPECT);
        strcpy(frame.source_name, "synthetic scan point / HV OFF");
        ut_ui_update(&frame);
    } else if (action == UT_ACTION_DEMO) {
        ut_ui_update_map(&demo_map);
        ut_ui_show_map(true);
    } else if (action == UT_ACTION_EXPORT || action == UT_ACTION_OPEN || action == UT_ACTION_SCAN_OPEN) {
        ut_ui_notify("Board DEMO only / SD and network exchange are not connected");
    } else ut_ui_notify("Display-only preview / no transmit or motion controls");
}

int main(void)
{
    Xil_ICacheEnable();
    Xil_DCacheEnable();
    XTime_GetTime(&start_time);
    memset((void *)&ut_rgb_diag, 0, sizeof(ut_rgb_diag));
    ut_rgb_diag.magic = 0x55545247U;
    ut_rgb_diag.version = 2U;
    ut_rgb_diag.phase = 1U;
    ut_rgb_diag.mode = 1U;
#ifdef UT_WITH_TOUCH
    ut_rgb_diag.version = 3U;
    ut_rgb_diag.mode = 2U;
    memset((void *)ut_touch_command, 0, sizeof(ut_touch_command));
    Xil_DCacheFlushRange((UINTPTR)ut_touch_command, sizeof(ut_touch_command));
#endif
    Xil_Out32(CAPTURE_BASE + 4U, 0U);
    if (Xil_In32(CAPTURE_BASE) != 0x4d443131U) failure(1U);
    else if (check_dma()) {
        ut_rgb_diag.phase = 2U;
        memset((void *)FRAMEBUFFER_BASE, 0, FRAMEBUFFER_BYTES);
        Xil_DCacheFlushRange(FRAMEBUFFER_BASE, FRAMEBUFFER_BYTES);
        lv_init();
        lv_tick_set_cb(milliseconds);
        lv_display_t *display = lv_display_create(800, 480);
        if (!display) failure(10U);
        else {
            lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
            lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
            lv_display_set_flush_cb(display, flush_display);
            ut_ui_create(handle_action);
            update_demo();
            ut_board_demo_create(&demo_map, &demo_scan);
            ut_ui_update_scan(&demo_scan);
            ut_ui_update_map(&demo_map);
            ut_ui_show_map(true);
            ut_rgb_diag.map_cells = demo_map.columns * demo_map.rows;
            ut_rgb_diag.map_suspects = demo_map.suspect_cells;
#ifdef UT_WITH_TOUCH
            ut_zynq_touch_start();
#endif
            lv_refr_now(display);
            if (!ut_rgb_diag.error && start_display()) ut_rgb_diag.phase = 4U;
        }
    }
    uint32_t last_update = milliseconds();
    while (1) {
        uint32_t started = milliseconds();
        if (ut_rgb_diag.phase == 4U) {
            if (started - last_update >= 250U) {
                update_demo();
                last_update = started;
            }
            lv_timer_handler();
            ut_rgb_diag.last_render_ms = milliseconds() - started;
            if (ut_rgb_diag.last_render_ms > ut_rgb_diag.maximum_render_ms) ut_rgb_diag.maximum_render_ms = ut_rgb_diag.last_render_ms;
        }
        ut_rgb_diag.control = Xil_In32(CAPTURE_BASE + 4U);
        ut_rgb_diag.video_status = Xil_In32(CAPTURE_BASE + 8U);
        ut_rgb_diag.vdma_status = Xil_In32(VDMA_BASE + 4U);
        if ((ut_rgb_diag.control & 1U) || (ut_rgb_diag.video_status & 0xc0U)) failure(11U);
        if (ut_rgb_diag.phase == 4U && ((ut_rgb_diag.video_status & 0x600U) != 0x200U || (ut_rgb_diag.vdma_status & 0xff1U))) failure(9U);
        ut_rgb_diag.heartbeat++;
#ifdef UT_WITH_TOUCH
        Xil_DCacheInvalidateRange((UINTPTR)ut_touch_command, sizeof(ut_touch_command));
        uint32_t command = ut_touch_command[0];
        uint32_t request_nonce = ut_touch_command[1];
        bool command_applied = false;
        if (command >= 1U && command <= 6U && ut_rgb_diag.phase == 4U) {
            if (command == 6U) ut_ui_open_touch();
            else {
                ut_action_t action = (ut_action_t)(UT_ACTION_TOUCH_RETRY + command - 1U);
                ut_zynq_touch_action(action);
            }
            ut_rgb_diag.touch_last_command = command;
            command_applied = true;
        }
        const ut_touch_t *touch = ut_zynq_touch_state();
        ut_rgb_diag.touch_kind = touch->kind;
        ut_rgb_diag.touch_address = touch->address;
        ut_rgb_diag.touch_reads = touch->reads;
        ut_rgb_diag.touch_errors = touch->errors;
        ut_rgb_diag.touch_pressed = touch->sample.pressed;
        ut_rgb_diag.touch_raw_xy = ((uint32_t)touch->sample.raw_x << 16) | touch->sample.raw_y;
        ut_rgb_diag.touch_screen_xy = ((uint32_t)touch->sample.x << 16) | touch->sample.y;
        ut_rgb_diag.touch_bus_status = ut_zynq_touch_bus_status();
        ut_rgb_diag.touch_flags = (touch->transform.swap_xy ? 1U : 0U) | (touch->transform.invert_x ? 2U : 0U) | (touch->transform.invert_y ? 4U : 0U);
        ut_rgb_diag.touch_probe_ack = touch->probe_ack_mask;
        ut_rgb_diag.touch_ft_version = touch->ft_version;
        ut_rgb_diag.touch_ft_chip_id = touch->ft_chip_id;
        ut_rgb_diag.touch_product_code = touch->goodix_product_code;
#endif
        Xil_DCacheFlushRange((UINTPTR)&ut_rgb_diag, sizeof(ut_rgb_diag));
#ifdef UT_WITH_TOUCH
        if (command != 0U) {
            ut_touch_command[2] = request_nonce;
            ut_touch_command[3] = command_applied ? 1U : 2U;
            ut_touch_command[0] = 0U;
            Xil_DCacheFlushRange((UINTPTR)ut_touch_command, sizeof(ut_touch_command));
        }
#endif
        usleep(5000U);
    }
}
