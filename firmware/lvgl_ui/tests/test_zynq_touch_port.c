#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "zynq_touch.h"
#include "ut_ui.h"

static uint32_t gpio_output, gpio_tristate;
static unsigned reset_phase, resets, recoveries, updates, register_reads;
static uint8_t available_address, selected_address;
static bool recovery_fails;

uint32_t Xil_In32(uint32_t address)
{
    assert(address == 0x41210000U);
    return gpio_output | gpio_tristate;
}

void Xil_Out32(uint32_t address, uint32_t value)
{
    assert(address == 0x41210000U || address == 0x41210004U);
    assert((value & ~15U) == 0U);
    if (address == 0x41210000U) gpio_output = value;
    else gpio_tristate = value;
    assert((gpio_output & 3U) == 0U);
}

void usleep(unsigned microseconds)
{
    assert((gpio_tristate & 3U) == 3U);
    if (microseconds == 20000U) {
        assert(reset_phase == 0U && (gpio_tristate & 12U) == 0U && !(gpio_output & 4U));
        reset_phase = 1U;
    } else if (microseconds == 6000U) {
        assert(reset_phase == 1U && (gpio_tristate & 12U) == 0U && (gpio_output & 4U));
        selected_address = (gpio_output & 8U) ? 0x14U : 0x5dU;
        reset_phase = 2U;
    } else if (microseconds == 50000U) {
        assert(reset_phase == 2U && (gpio_tristate & 12U) == 0U && (gpio_output & 4U) && !(gpio_output & 8U));
        reset_phase = 3U;
    } else assert(false);
}

ut_i2c_result_t ut_i2c_recover(ut_i2c_t *bus)
{
    assert(bus && reset_phase == 3U && (gpio_tristate & 15U) == 11U && (gpio_output & 4U));
    assert(bus->half_period_us == 5U && bus->stretch_limit_us == 500U);
    resets++;
    recoveries++;
    reset_phase = 0U;
    return recovery_fails ? UT_I2C_BUS_BUSY : UT_I2C_OK;
}

ut_i2c_result_t ut_i2c_read(ut_i2c_t *bus, uint8_t address, uint16_t register_address, unsigned register_bytes, uint8_t *data, size_t length)
{
    assert(bus && reset_phase == 0U && (gpio_tristate & 8U) && !(gpio_tristate & 4U));
    assert(data && length);
    register_reads++;
    if (address != available_address || address != selected_address || register_address != 0x8140U) return UT_I2C_NACK;
    assert(register_bytes == 2U && length == 10U);
    const uint8_t identification[10] = {'9', '1', '1', 0, 1, 0, 0x20, 3, 0xe0, 1};
    memcpy(data, identification, sizeof(identification));
    return UT_I2C_OK;
}

ut_i2c_result_t ut_i2c_write(ut_i2c_t *bus, uint8_t address, uint16_t register_address, unsigned register_bytes, const uint8_t *data, size_t length)
{
    (void)bus; (void)address; (void)register_address; (void)register_bytes; (void)data; (void)length;
    assert(false);
    return UT_I2C_INVALID;
}

void ut_ui_update_touch(const ut_touch_t *touch)
{
    assert(touch);
    updates++;
}

void ut_ui_notify(const char *message) { assert(message && message[0]); }

static void test_address(uint8_t address, bool fail_recovery)
{
    gpio_output = 0U;
    gpio_tristate = 15U;
    reset_phase = resets = recoveries = updates = register_reads = 0U;
    available_address = address;
    selected_address = 0U;
    recovery_fails = fail_recovery;
    lv_init();
    assert(lv_display_create(800, 480));
    assert(ut_zynq_touch_start() == !fail_recovery);
    const ut_touch_t *touch = ut_zynq_touch_state();
    unsigned expected_resets = address == 0x14U && !fail_recovery ? 2U : 1U;
    assert(resets == expected_resets && recoveries == resets && updates == 1U);
    assert(ut_zynq_touch_bus_status() == (fail_recovery ? UT_I2C_BUS_BUSY : UT_I2C_OK));
    if (fail_recovery) assert(touch->kind == UT_TOUCH_NONE && !register_reads);
    else {
        assert(touch->kind == UT_TOUCH_GOODIX && touch->address == address);
        assert(touch->transform.raw_width == 800U && touch->transform.raw_height == 480U);
        ut_zynq_touch_action(UT_ACTION_TOUCH_INVERT_X);
        assert(touch->transform.invert_x);
        ut_zynq_touch_action(UT_ACTION_TOUCH_RESET);
        assert(!touch->transform.invert_x && !touch->transform.invert_y && !touch->transform.swap_xy);
        unsigned before_retry = resets;
        ut_zynq_touch_action(UT_ACTION_TOUCH_RETRY);
        assert(resets == before_retry + expected_resets && touch->address == address);
    }
    assert(reset_phase == 0U && (gpio_tristate & 8U));
    lv_deinit();
}

int main(void)
{
    test_address(0x5dU, false);
    test_address(0x14U, false);
    test_address(0x5dU, true);
    puts("PASS: actual Zynq touch port with mocked GPIO, reset/address timing, 50ms INT-low sync, released INT before I2C, both Goodix addresses, recovery failure, retry and RAM-only direction; NO_HARDWARE_ACCESSED");
    return 0;
}
