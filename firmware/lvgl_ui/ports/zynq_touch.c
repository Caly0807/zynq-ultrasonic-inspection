#include "zynq_touch.h"
#include "xil_io.h"
#include "xparameters.h"
#include "sleep.h"
#include "ut_touch_input.h"

#define TOUCH_SCL 1U
#define TOUCH_SDA 2U
#define TOUCH_RESET 4U
#define TOUCH_INTERRUPT 8U
#define TOUCH_GPIO_BASE XPAR_TOUCH_GPIO_BASEADDR

static ut_touch_t controller;
static uint32_t output_data, tristate = 15U;
static uint32_t bus_status, last_ui_update;
static lv_indev_t *pointer;
static ut_touch_input_t contact;

static void write_gpio(void)
{
    Xil_Out32(TOUCH_GPIO_BASE, output_data);
    Xil_Out32(TOUCH_GPIO_BASE + 4U, tristate);
}

static void release_lines(void *context, bool clock, bool data)
{
    (void)context;
    tristate = (tristate & ~3U) | (clock ? TOUCH_SCL : 0U) | (data ? TOUCH_SDA : 0U);
    write_gpio();
}
static bool read_clock(void *context) { (void)context; return (Xil_In32(TOUCH_GPIO_BASE) & TOUCH_SCL) != 0U; }
static bool read_data(void *context) { (void)context; return (Xil_In32(TOUCH_GPIO_BASE) & TOUCH_SDA) != 0U; }
static void delay_us(void *context, unsigned microseconds) { (void)context; usleep(microseconds); }
static ut_i2c_t bus = {NULL, release_lines, read_clock, read_data, delay_us, 5U, 500U};

static bool read_register(void *context, uint8_t address, uint16_t register_address, unsigned register_bytes, uint8_t *data, size_t length)
{
    (void)context;
    bus_status = ut_i2c_read(&bus, address, register_address, register_bytes, data, length);
    return bus_status == UT_I2C_OK;
}
static bool write_register(void *context, uint8_t address, uint16_t register_address, unsigned register_bytes, const uint8_t *data, size_t length)
{
    (void)context;
    bus_status = ut_i2c_write(&bus, address, register_address, register_bytes, data, length);
    return bus_status == UT_I2C_OK;
}
static bool reset_controller(void *context, bool high_address)
{
    (void)context;
    tristate = 15U;
    output_data = high_address ? TOUCH_INTERRUPT : 0U;
    write_gpio();
    tristate &= ~(TOUCH_RESET | TOUCH_INTERRUPT);
    write_gpio();
    usleep(20000U);
    output_data |= TOUCH_RESET;
    write_gpio();
    usleep(6000U);
    output_data &= ~TOUCH_INTERRUPT;
    write_gpio();
    usleep(50000U);
    tristate |= TOUCH_INTERRUPT;
    write_gpio();
    bus_status = ut_i2c_recover(&bus);
    return bus_status == UT_I2C_OK;
}

static void read_pointer(lv_indev_t *input, lv_indev_data_t *data)
{
    uint32_t now = lv_tick_get();
    ut_touch_result_t result = controller.kind != UT_TOUCH_NONE ? ut_touch_poll(&controller, now) : UT_TOUCH_UNSUPPORTED;
    ut_touch_input_update(&contact, result, &controller.sample);
    if (contact.cancel) lv_indev_reset(input, NULL);
    data->point.x = contact.x;
    data->point.y = contact.y;
    data->state = contact.pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
    if (now - last_ui_update >= 100U) { ut_ui_update_touch(&controller); last_ui_update = now; }
}

static bool probe(void)
{
    ut_touch_bus_t registers = {NULL, read_register, write_register, reset_controller};
    bool found = ut_touch_probe(&controller, &registers, 800U, 480U) == UT_TOUCH_OK;
    if (found) ut_touch_reset_board_orientation(&controller);
    ut_touch_input_disarm(&contact);
    ut_ui_update_touch(&controller);
    return found;
}

bool ut_zynq_touch_start(void)
{
    tristate = 15U;
    output_data = 0U;
    write_gpio();
    bool found = probe();
    pointer = lv_indev_create();
    if (!pointer) return false;
    lv_indev_set_type(pointer, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(pointer, read_pointer);
    lv_timer_set_period(lv_indev_get_read_timer(pointer), 20U);
    return found;
}

void ut_zynq_touch_action(ut_action_t action)
{
    if (action == UT_ACTION_TOUCH_RETRY) {
        bool found = probe();
        if (pointer) lv_indev_reset(pointer, NULL);
        ut_ui_notify(found ? "Touch detected / release finger before use" : "Touch not identified / display remains available");
        return;
    }
    if (action == UT_ACTION_TOUCH_SWAP) controller.transform.swap_xy = !controller.transform.swap_xy;
    if (action == UT_ACTION_TOUCH_INVERT_X) controller.transform.invert_x = !controller.transform.invert_x;
    if (action == UT_ACTION_TOUCH_INVERT_Y) controller.transform.invert_y = !controller.transform.invert_y;
    if (action == UT_ACTION_TOUCH_RESET) ut_touch_reset_board_orientation(&controller);
    ut_touch_release(&controller);
    ut_touch_input_disarm(&contact);
    if (pointer) lv_indev_reset(pointer, NULL);
    ut_ui_update_touch(&controller);
}

const ut_touch_t *ut_zynq_touch_state(void) { return &controller; }
uint32_t ut_zynq_touch_bus_status(void) { return bus_status; }
