#include "ut_i2c.h"

static bool valid_bus(const ut_i2c_t *bus)
{
    return bus && bus->release && bus->read_clock && bus->read_data && bus->delay_us &&
           bus->half_period_us >= 2U && bus->half_period_us <= 100U &&
           bus->stretch_limit_us >= bus->half_period_us && bus->stretch_limit_us <= 2000U;
}

static void delay(ut_i2c_t *bus) { bus->delay_us(bus->context, bus->half_period_us); }

static ut_i2c_result_t clock_high(ut_i2c_t *bus, bool data)
{
    bus->release(bus->context, true, data);
    for (unsigned elapsed = 0; elapsed <= bus->stretch_limit_us; elapsed += bus->half_period_us) {
        if (bus->read_clock(bus->context)) { delay(bus); return UT_I2C_OK; }
        delay(bus);
    }
    return UT_I2C_TIMEOUT;
}

static ut_i2c_result_t stop(ut_i2c_t *bus)
{
    bus->release(bus->context, false, false);
    delay(bus);
    ut_i2c_result_t result = clock_high(bus, false);
    bus->release(bus->context, true, true);
    delay(bus);
    if (result == UT_I2C_OK && !bus->read_data(bus->context)) result = UT_I2C_BUS_BUSY;
    return result;
}

static ut_i2c_result_t start(ut_i2c_t *bus)
{
    bus->release(bus->context, false, true);
    delay(bus);
    ut_i2c_result_t result = clock_high(bus, true);
    if (result != UT_I2C_OK) return result;
    if (!bus->read_data(bus->context)) return UT_I2C_BUS_BUSY;
    bus->release(bus->context, true, false);
    delay(bus);
    bus->release(bus->context, false, false);
    return UT_I2C_OK;
}

static ut_i2c_result_t write_byte(ut_i2c_t *bus, uint8_t value)
{
    for (unsigned bit = 0; bit < 8U; bit++) {
        bool data = (value & 0x80U) != 0;
        bus->release(bus->context, false, data);
        delay(bus);
        ut_i2c_result_t result = clock_high(bus, data);
        if (result != UT_I2C_OK) return result;
        if (data && !bus->read_data(bus->context)) return UT_I2C_BUS_BUSY;
        bus->release(bus->context, false, data);
        value <<= 1;
    }
    bus->release(bus->context, false, true);
    delay(bus);
    ut_i2c_result_t result = clock_high(bus, true);
    if (result != UT_I2C_OK) return result;
    bool acknowledged = !bus->read_data(bus->context);
    bus->release(bus->context, false, true);
    return acknowledged ? UT_I2C_OK : UT_I2C_NACK;
}

static ut_i2c_result_t read_byte(ut_i2c_t *bus, uint8_t *value, bool acknowledge)
{
    *value = 0;
    for (unsigned bit = 0; bit < 8U; bit++) {
        bus->release(bus->context, false, true);
        delay(bus);
        ut_i2c_result_t result = clock_high(bus, true);
        if (result != UT_I2C_OK) return result;
        *value = (uint8_t)((*value << 1) | (bus->read_data(bus->context) ? 1U : 0U));
        bus->release(bus->context, false, true);
    }
    bus->release(bus->context, false, !acknowledge);
    delay(bus);
    ut_i2c_result_t result = clock_high(bus, !acknowledge);
    bus->release(bus->context, false, !acknowledge);
    delay(bus);
    bus->release(bus->context, false, true);
    return result;
}

static bool valid_transfer(ut_i2c_t *bus, uint8_t address, uint16_t register_address, unsigned register_bytes, const uint8_t *data, size_t length)
{
    return valid_bus(bus) && address >= 8U && address <= 0x77U && data && length && length <= 64U &&
           (register_bytes == 2U || (register_bytes == 1U && register_address <= 255U));
}

static ut_i2c_result_t select_register(ut_i2c_t *bus, uint8_t address, uint16_t register_address, unsigned register_bytes)
{
    ut_i2c_result_t result = start(bus);
    if (result == UT_I2C_OK) result = write_byte(bus, (uint8_t)(address << 1));
    if (result == UT_I2C_OK && register_bytes == 2U) result = write_byte(bus, (uint8_t)(register_address >> 8));
    if (result == UT_I2C_OK) result = write_byte(bus, (uint8_t)register_address);
    return result;
}

static ut_i2c_result_t finish(ut_i2c_t *bus, ut_i2c_result_t result)
{
    if (result == UT_I2C_TIMEOUT || result == UT_I2C_BUS_BUSY) {
        bus->release(bus->context, true, true);
        return result;
    }
    ut_i2c_result_t stopped = stop(bus);
    return result == UT_I2C_OK ? stopped : result;
}

ut_i2c_result_t ut_i2c_read(ut_i2c_t *bus, uint8_t address, uint16_t register_address, unsigned register_bytes, uint8_t *data, size_t length)
{
    if (!valid_transfer(bus, address, register_address, register_bytes, data, length)) return UT_I2C_INVALID;
    ut_i2c_result_t result = select_register(bus, address, register_address, register_bytes);
    if (result == UT_I2C_OK) result = start(bus);
    if (result == UT_I2C_OK) result = write_byte(bus, (uint8_t)((address << 1) | 1U));
    for (size_t index = 0; index < length && result == UT_I2C_OK; index++) result = read_byte(bus, &data[index], index + 1U < length);
    return finish(bus, result);
}

ut_i2c_result_t ut_i2c_write(ut_i2c_t *bus, uint8_t address, uint16_t register_address, unsigned register_bytes, const uint8_t *data, size_t length)
{
    if (!valid_transfer(bus, address, register_address, register_bytes, data, length)) return UT_I2C_INVALID;
    ut_i2c_result_t result = select_register(bus, address, register_address, register_bytes);
    for (size_t index = 0; index < length && result == UT_I2C_OK; index++) result = write_byte(bus, data[index]);
    return finish(bus, result);
}

ut_i2c_result_t ut_i2c_recover(ut_i2c_t *bus)
{
    if (!valid_bus(bus)) return UT_I2C_INVALID;
    ut_i2c_result_t result = clock_high(bus, true);
    if (result != UT_I2C_OK) return finish(bus, result);
    for (unsigned pulse = 0; pulse < 9U && !bus->read_data(bus->context); pulse++) {
        bus->release(bus->context, false, true);
        delay(bus);
        result = clock_high(bus, true);
        if (result != UT_I2C_OK) return finish(bus, result);
    }
    return stop(bus);
}
