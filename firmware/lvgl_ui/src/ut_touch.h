#ifndef UT_TOUCH_H
#define UT_TOUCH_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum { UT_TOUCH_NONE, UT_TOUCH_GOODIX, UT_TOUCH_FT5206, UT_TOUCH_FT5X46 } ut_touch_kind_t;
typedef enum { UT_TOUCH_OK, UT_TOUCH_NOT_READY, UT_TOUCH_IO_ERROR, UT_TOUCH_BAD_PACKET, UT_TOUCH_UNSUPPORTED } ut_touch_result_t;
typedef struct {
    void *context;
    bool (*read)(void *context, uint8_t address, uint16_t register_address, unsigned register_bytes, uint8_t *data, size_t length);
    bool (*write)(void *context, uint8_t address, uint16_t register_address, unsigned register_bytes, const uint8_t *data, size_t length);
    bool (*reset)(void *context, bool high_address);
} ut_touch_bus_t;
typedef struct {
    uint16_t raw_width, raw_height;
    uint16_t screen_width, screen_height;
    bool swap_xy, invert_x, invert_y;
} ut_touch_transform_t;
typedef struct {
    bool pressed;
    uint8_t fingers;
    uint16_t raw_x, raw_y, x, y;
} ut_touch_sample_t;
typedef struct {
    ut_touch_bus_t bus;
    ut_touch_transform_t transform;
    ut_touch_kind_t kind;
    uint8_t address;
    char product[8];
    ut_touch_sample_t sample;
    uint32_t reads, errors, rejected, last_ready_ms;
    uint32_t probe_ack_mask, goodix_product_code;
    uint16_t ft_version;
    uint8_t ft_chip_id;
    uint8_t last_packet[7];
    ut_touch_result_t last_result;
} ut_touch_t;
bool ut_touch_transform(const ut_touch_transform_t *transform, uint16_t raw_x, uint16_t raw_y, uint16_t *screen_x, uint16_t *screen_y);
ut_touch_result_t ut_touch_probe(ut_touch_t *touch, const ut_touch_bus_t *bus, uint16_t screen_width, uint16_t screen_height);
ut_touch_result_t ut_touch_poll(ut_touch_t *touch, uint32_t now_ms);
void ut_touch_release(ut_touch_t *touch);
#endif
