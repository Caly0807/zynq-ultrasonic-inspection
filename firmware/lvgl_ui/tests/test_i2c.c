#include "ut_i2c.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    bool data_bits[1024];
    size_t count, consumed;
    bool clock_released, data_released, clock_stuck;
    unsigned clock_stretch_reads, delayed_us, changes;
} fixture_t;

static void release_lines(void *context, bool clock, bool data)
{
    fixture_t *fixture = context;
    assert(!(fixture->clock_released && !clock && !fixture->data_released && data));
    fixture->clock_released = clock;
    fixture->data_released = data;
    fixture->changes++;
}
static bool read_clock(void *context)
{
    fixture_t *fixture = context;
    if (fixture->clock_stretch_reads) { fixture->clock_stretch_reads--; return false; }
    return !fixture->clock_stuck && fixture->clock_released;
}
static bool read_data(void *context)
{
    fixture_t *fixture = context;
    assert(fixture->consumed < fixture->count && fixture->clock_released);
    return fixture->data_bits[fixture->consumed++];
}
static void delay_us(void *context, unsigned microseconds) { ((fixture_t *)context)->delayed_us += microseconds; }
static void data_bit(fixture_t *fixture, bool value) { fixture->data_bits[fixture->count++] = value; }
static void written_byte(fixture_t *fixture, uint8_t value, bool acknowledged)
{
    for (unsigned bit = 0; bit < 8U; bit++) { if (value & 0x80U) data_bit(fixture, true); value <<= 1; }
    data_bit(fixture, !acknowledged);
}
static void received_byte(fixture_t *fixture, uint8_t value)
{
    for (unsigned bit = 0; bit < 8U; bit++) { data_bit(fixture, (value & 0x80U) != 0); value <<= 1; }
}
static void completed(const fixture_t *fixture)
{
    assert(fixture->consumed == fixture->count && fixture->clock_released && fixture->data_released);
    assert(fixture->changes > 0U && fixture->delayed_us > 0U);
}
int main(void)
{
    fixture_t fixture = {0};
    ut_i2c_t bus = {&fixture, release_lines, read_clock, read_data, delay_us, 5U, 500U};
    data_bit(&fixture, true);
    written_byte(&fixture, 0xbaU, true);
    written_byte(&fixture, 0x81U, true);
    written_byte(&fixture, 0x40U, true);
    data_bit(&fixture, true);
    written_byte(&fixture, 0xbbU, true);
    received_byte(&fixture, 0x91U);
    received_byte(&fixture, 0x42U);
    data_bit(&fixture, true);
    uint8_t data[2] = {0};
    fixture.clock_stretch_reads = 4U;
    assert(ut_i2c_read(&bus, 0x5dU, 0x8140U, 2U, data, 2U) == UT_I2C_OK && data[0] == 0x91U && data[1] == 0x42U);
    completed(&fixture);
    memset(&fixture, 0, sizeof(fixture));
    data_bit(&fixture, true);
    written_byte(&fixture, 0x70U, true);
    written_byte(&fixture, 0xa4U, true);
    written_byte(&fixture, 0U, true);
    data_bit(&fixture, true);
    data[0] = 0U;
    assert(ut_i2c_write(&bus, 0x38U, 0xa4U, 1U, data, 1U) == UT_I2C_OK);
    completed(&fixture);
    memset(&fixture, 0, sizeof(fixture));
    data_bit(&fixture, true);
    written_byte(&fixture, 0x70U, false);
    data_bit(&fixture, true);
    assert(ut_i2c_read(&bus, 0x38U, 0U, 1U, data, 1U) == UT_I2C_NACK);
    completed(&fixture);
    memset(&fixture, 0, sizeof(fixture));
    fixture.clock_stuck = true;
    assert(ut_i2c_read(&bus, 0x38U, 0U, 1U, data, 1U) == UT_I2C_TIMEOUT);
    assert(fixture.clock_released && fixture.data_released && fixture.delayed_us <= 520U);
    memset(&fixture, 0, sizeof(fixture));
    data_bit(&fixture, false);
    assert(ut_i2c_read(&bus, 0x38U, 0U, 1U, data, 1U) == UT_I2C_BUS_BUSY);
    completed(&fixture);
    memset(&fixture, 0, sizeof(fixture));
    for (unsigned pulse = 0; pulse < 4U; pulse++) data_bit(&fixture, false);
    data_bit(&fixture, true);
    data_bit(&fixture, true);
    assert(ut_i2c_recover(&bus) == UT_I2C_OK);
    completed(&fixture);
    memset(&fixture, 0, sizeof(fixture));
    for (unsigned pulse = 0; pulse < 10U; pulse++) data_bit(&fixture, false);
    assert(ut_i2c_recover(&bus) == UT_I2C_BUS_BUSY);
    completed(&fixture);
    memset(&fixture, 0, sizeof(fixture));
    assert(ut_i2c_read(&bus, 0x38U, 0U, 0U, data, 1U) == UT_I2C_INVALID);
    assert(ut_i2c_read(&bus, 0x38U, 0x100U, 1U, data, 1U) == UT_I2C_INVALID);
    assert(ut_i2c_write(&bus, 0x78U, 0U, 1U, data, 1U) == UT_I2C_INVALID);
    assert(ut_i2c_read(&bus, 0x38U, 0U, 1U, NULL, 1U) == UT_I2C_INVALID);
    assert(ut_i2c_read(&bus, 0x38U, 0U, 1U, data, 65U) == UT_I2C_INVALID && fixture.changes == 0U);
    puts("PASS: I2C 8/16-bit registers, repeated START, burst read, ACK/NACK, clock stretching, bounded timeout, recovery and released failure states; hardware NOT tested");
    return 0;
}
