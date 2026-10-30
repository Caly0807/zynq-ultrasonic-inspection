#ifndef UT_I2C_H
#define UT_I2C_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum { UT_I2C_OK, UT_I2C_NACK, UT_I2C_TIMEOUT, UT_I2C_BUS_BUSY, UT_I2C_INVALID } ut_i2c_result_t;
typedef struct {
    void *context;
    void (*release)(void *context, bool clock, bool data);
    bool (*read_clock)(void *context);
    bool (*read_data)(void *context);
    void (*delay_us)(void *context, unsigned microseconds);
    unsigned half_period_us;
    unsigned stretch_limit_us;
} ut_i2c_t;
ut_i2c_result_t ut_i2c_recover(ut_i2c_t *bus);
ut_i2c_result_t ut_i2c_read(ut_i2c_t *bus, uint8_t address, uint16_t register_address, unsigned register_bytes, uint8_t *data, size_t length);
ut_i2c_result_t ut_i2c_write(ut_i2c_t *bus, uint8_t address, uint16_t register_address, unsigned register_bytes, const uint8_t *data, size_t length);
#endif
