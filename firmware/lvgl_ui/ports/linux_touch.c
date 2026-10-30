#define _POSIX_C_SOURCE 200809L
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include "linux_touch.h"
#include "ut_touch_input.h"

static int bus_descriptor = -1;
static ut_touch_t controller;
static ut_touch_input_t contact;
static lv_indev_t *pointer;
static uint32_t last_update;
static uint32_t press_events, release_events;
static uint32_t probes, raw_press_samples, cancel_events, last_press_ms, last_poll_ms;
static uint16_t last_press_x, last_press_y;
static uint8_t last_press_packet[7];

static bool read_register(void *context, uint8_t address, uint16_t reg_address, unsigned reg_bytes, uint8_t *data, size_t length)
{
    (void)context;
    if (bus_descriptor < 0 || reg_bytes < 1 || reg_bytes > 2 || length > 64) return false;
    uint8_t command[2] = {(uint8_t)(reg_address >> 8), (uint8_t)reg_address};
    struct i2c_msg messages[2] = {
        {.addr = address, .len = reg_bytes, .buf = command + (reg_bytes == 1)},
        {.addr = address, .flags = I2C_M_RD, .len = length, .buf = data},
    };
    struct i2c_rdwr_ioctl_data transaction = {.msgs = messages, .nmsgs = 2};
    return ioctl(bus_descriptor, I2C_RDWR, &transaction) == 2;
}

static bool write_register(void *context, uint8_t address, uint16_t reg_address, unsigned reg_bytes, const uint8_t *data, size_t length)
{
    (void)context;
    if (bus_descriptor < 0 || reg_bytes < 1 || reg_bytes > 2 || length > 64) return false;
    uint8_t command[66];
    if (reg_bytes == 2) command[0] = (uint8_t)(reg_address >> 8);
    command[reg_bytes - 1] = (uint8_t)reg_address;
    memcpy(command + reg_bytes, data, length);
    struct i2c_msg message = {.addr = address, .len = reg_bytes + length, .buf = command};
    struct i2c_rdwr_ioctl_data transaction = {.msgs = &message, .nmsgs = 1};
    return ioctl(bus_descriptor, I2C_RDWR, &transaction) == 1;
}

static bool probe(void)
{
    probes++;
    ut_touch_bus_t bus = {NULL, read_register, write_register, NULL};
    bool ready = ut_touch_probe(&controller, &bus, 800, 480) == UT_TOUCH_OK;
    ut_touch_input_disarm(&contact);
    ut_ui_update_touch(&controller);
    return ready;
}

static void read_pointer(lv_indev_t *input, lv_indev_data_t *data)
{
    uint32_t now = lv_tick_get();
    ut_touch_result_t result = controller.kind != UT_TOUCH_NONE ? ut_touch_poll(&controller, now) : UT_TOUCH_UNSUPPORTED;
    last_poll_ms = now;
    if (result == UT_TOUCH_OK && controller.sample.pressed) {
        raw_press_samples++;
        last_press_ms = now;
        last_press_x = controller.sample.x;
        last_press_y = controller.sample.y;
        memcpy(last_press_packet, controller.last_packet, sizeof(last_press_packet));
    }
    bool previous_pressed = contact.pressed;
    ut_touch_input_update(&contact, result, &controller.sample);
    if (contact.pressed && !previous_pressed) press_events++;
    if (!contact.pressed && previous_pressed) release_events++;
    if (contact.cancel) { cancel_events++; lv_indev_reset(input, NULL); }
    data->state = contact.pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
    data->point.x = contact.x;
    data->point.y = contact.y;
    if (now - last_update > 500U) {
        last_update = now;
        ut_ui_update_touch(&controller);
    }
}

bool ut_linux_touch_open(const char *path)
{
    bus_descriptor = open(path, O_RDWR | O_CLOEXEC);
    if (bus_descriptor < 0) return false;
    bool ready = probe();
    pointer = lv_indev_create();
    if (!pointer) { ut_linux_touch_close(); return false; }
    lv_indev_set_type(pointer, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(pointer, read_pointer);
    lv_timer_set_period(lv_indev_get_read_timer(pointer), 30);
    return ready;
}

void ut_linux_touch_action(ut_action_t action)
{
    if (action == UT_ACTION_TOUCH_RETRY) {
        bool ready = bus_descriptor >= 0 && probe();
        if (pointer) lv_indev_reset(pointer, NULL);
        ut_ui_notify(ready ? "Touch identity verified / release before use" : "Touch unavailable / no synthetic input");
        return;
    }
    if (action == UT_ACTION_TOUCH_SWAP) controller.transform.swap_xy = !controller.transform.swap_xy;
    if (action == UT_ACTION_TOUCH_INVERT_X) controller.transform.invert_x = !controller.transform.invert_x;
    if (action == UT_ACTION_TOUCH_INVERT_Y) controller.transform.invert_y = !controller.transform.invert_y;
    if (action == UT_ACTION_TOUCH_RESET) controller.transform.swap_xy = controller.transform.invert_x = controller.transform.invert_y = false;
    ut_touch_release(&controller);
    ut_touch_input_disarm(&contact);
    if (pointer) lv_indev_reset(pointer, NULL);
    ut_ui_update_touch(&controller);
}

void ut_linux_touch_close(void)
{
    if (pointer) { lv_indev_delete(pointer); pointer = NULL; }
    if (bus_descriptor >= 0) { close(bus_descriptor); bus_descriptor = -1; }
}

bool ut_linux_touch_write_status(FILE *output)
{
    return fprintf(output,
        "{\"bus_open\":%s,\"kind\":%u,\"address\":%u,\"probe_ack_mask\":%u,"
        "\"reads\":%u,\"errors\":%u,\"rejected\":%u,\"last_result\":%u,"
        "\"ft_chip_id\":%u,\"ft_version\":%u,\"goodix_product_code\":%u,"
        "\"pressed\":%s,\"press_events\":%u,\"release_events\":%u,"
        "\"raw_x\":%u,\"raw_y\":%u,\"x\":%u,\"y\":%u,"
        "\"last_packet\":[%u,%u,%u,%u,%u,%u,%u],"
        "\"probes\":%u,\"raw_press_samples\":%u,\"cancel_events\":%u,\"input_armed\":%s,"
        "\"last_poll_ms\":%u,\"last_press_ms\":%u,\"last_press_x\":%u,\"last_press_y\":%u,"
        "\"last_press_packet\":[%u,%u,%u,%u,%u,%u,%u]}",
        bus_descriptor >= 0 ? "true" : "false", (unsigned)controller.kind, controller.address,
        controller.probe_ack_mask, controller.reads, controller.errors, controller.rejected,
        (unsigned)controller.last_result, controller.ft_chip_id, controller.ft_version,
        controller.goodix_product_code, contact.pressed ? "true" : "false", press_events, release_events,
        controller.sample.raw_x, controller.sample.raw_y, contact.x, contact.y,
        controller.last_packet[0], controller.last_packet[1], controller.last_packet[2],
        controller.last_packet[3], controller.last_packet[4], controller.last_packet[5], controller.last_packet[6],
        probes, raw_press_samples, cancel_events, contact.armed ? "true" : "false",
        last_poll_ms, last_press_ms, last_press_x, last_press_y,
        last_press_packet[0], last_press_packet[1], last_press_packet[2], last_press_packet[3],
        last_press_packet[4], last_press_packet[5], last_press_packet[6]) > 0;
}
