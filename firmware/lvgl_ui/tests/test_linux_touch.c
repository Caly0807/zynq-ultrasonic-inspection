#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "linux_touch.h"

static uint32_t ticks;
static uint8_t packet[7];
static bool read_error;
static unsigned snapshots, register_writes, closed;
static uint32_t draw_buffer[800U * 8U];

int __wrap_open(const char *path, int flags, ...)
{
    assert(!strcmp(path, "/fixture/touch-i2c") && (flags & O_RDWR));
    return 23;
}

int __wrap_close(int descriptor)
{
    assert(descriptor == 23);
    closed++;
    return 0;
}

int __wrap_ioctl(int descriptor, unsigned long request, ...)
{
    assert(descriptor == 23 && request == I2C_RDWR);
    va_list arguments;
    va_start(arguments, request);
    struct i2c_rdwr_ioctl_data *transaction = va_arg(arguments, struct i2c_rdwr_ioctl_data *);
    va_end(arguments);
    if (transaction->nmsgs == 1U) { register_writes++; return -1; }
    assert(transaction->nmsgs == 2U);
    struct i2c_msg *command = &transaction->msgs[0], *reply = &transaction->msgs[1];
    assert(!command->flags && reply->flags == I2C_M_RD && command->addr == reply->addr);
    if (command->addr != 0x38U || read_error) { errno = EIO; return -1; }
    assert(command->len == 1U);
    if (command->buf[0] == 0xa1U && reply->len == 2U) { reply->buf[0] = 0; reply->buf[1] = 1; return 2; }
    if (command->buf[0] == 0xa3U && reply->len == 1U) { reply->buf[0] = 0x54U; return 2; }
    if (command->buf[0] == 0U && reply->len == 1U) { reply->buf[0] = 0; return 2; }
    assert(command->buf[0] == 0U && reply->len == 7U);
    memcpy(reply->buf, packet, sizeof(packet));
    snapshots++;
    return 2;
}

void ut_ui_update_touch(const ut_touch_t *touch) { assert(touch); }
void ut_ui_notify(const char *message) { assert(message); }
static uint32_t now_ms(void) { return ticks; }
static void flush(lv_display_t *display, const lv_area_t *area, uint8_t *pixels)
{
    (void)area;
    (void)pixels;
    lv_display_flush_ready(display);
}

static void pump(void)
{
    ticks += 35U;
    lv_timer_handler();
}

static void status_has(const char *expected)
{
    FILE *output = tmpfile();
    assert(output && ut_linux_touch_write_status(output));
    rewind(output);
    char status[2048] = {0};
    assert(fread(status, 1, sizeof(status) - 1U, output) > 0);
    assert(strstr(status, expected));
    assert(!fclose(output));
}

int main(void)
{
    lv_init();
    lv_tick_set_cb(now_ms);
    lv_display_t *display = lv_display_create(800, 480);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(display, draw_buffer, NULL, sizeof(draw_buffer), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(display, flush);
    assert(ut_linux_touch_open("/fixture/touch-i2c"));
    pump();
    status_has("\"input_armed\":true");
    status_has("\"raw_width\":480,\"raw_height\":800,\"screen_width\":800,\"screen_height\":480");
    status_has("\"swap_xy\":true,\"invert_x\":false,\"invert_y\":false");
    packet[2] = 1U;
    packet[3] = 0x80U;
    packet[4] = 105U;
    packet[5] = 0x02U;
    packet[6] = 0x64U;
    pump();
    status_has("\"press_events\":1");
    status_has("\"pressed\":true");
    memset(packet, 0, sizeof(packet));
    pump();
    status_has("\"release_events\":1");
    status_has("\"last_press_x\":612,\"last_press_y\":105");
    status_has("\"last_press_packet\":[0,0,1,128,105,2,100]");
    packet[2] = 1U;
    pump();
    read_error = true;
    pump();
    status_has("\"cancel_events\":1");
    status_has("\"input_armed\":false");
    read_error = false;
    pump();
    status_has("\"press_events\":2");
    status_has("\"pressed\":false");
    memset(packet, 0, sizeof(packet));
    pump();
    packet[2] = 1U;
    pump();
    status_has("\"press_events\":3");
    packet[2] = 2U;
    pump();
    status_has("\"cancel_events\":2");
    packet[2] = 1U;
    pump();
    status_has("\"press_events\":3");
    memset(packet, 0, sizeof(packet));
    pump();
    ut_linux_touch_action(UT_ACTION_TOUCH_SWAP);
    status_has("\"swap_xy\":false");
    ut_linux_touch_action(UT_ACTION_TOUCH_INVERT_X);
    status_has("\"invert_x\":true");
    ut_linux_touch_action(UT_ACTION_TOUCH_RESET);
    status_has("\"swap_xy\":true,\"invert_x\":false,\"invert_y\":false");
    status_has("\"input_armed\":false");
    pump();
    packet[2] = 1U;
    packet[3] = 0x80U;
    packet[4] = 105U;
    packet[5] = 2U;
    packet[6] = 0x64U;
    pump();
    status_has("\"press_events\":4");
    status_has("\"last_press_x\":612,\"last_press_y\":105");
    packet[3] = 0x81U;
    packet[4] = 0xe0U;
    pump();
    status_has("\"rejected\":1");
    status_has("\"pressed\":false");
    memset(packet, 0, sizeof(packet));
    pump();
    packet[2] = 1U;
    packet[4] = 105U;
    packet[5] = 3U;
    packet[6] = 0x20U;
    pump();
    status_has("\"rejected\":2");
    status_has("\"press_events\":4");
    ut_linux_touch_close();
    status_has("\"bus_open\":false");
    assert(snapshots >= 8U && !register_writes && closed == 1U);
    lv_deinit();
    puts("PASS: Linux I2C_RDWR snapshots, short-press telemetry, release-before-rearm, I/O and multitouch cancellation; NO real I2C or physical acceptance");
    return 0;
}
