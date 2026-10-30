#include "ut_touch.h"
#include <string.h>

bool ut_touch_transform(const ut_touch_transform_t *transform, uint16_t raw_x, uint16_t raw_y, uint16_t *screen_x, uint16_t *screen_y)
{
    if (!transform || !screen_x || !screen_y || transform->raw_width < 2U || transform->raw_height < 2U ||
        transform->screen_width < 2U || transform->screen_height < 2U || raw_x >= transform->raw_width || raw_y >= transform->raw_height) return false;
    uint32_t position_x = transform->swap_xy ? raw_y : raw_x;
    uint32_t position_y = transform->swap_xy ? raw_x : raw_y;
    uint32_t width = transform->swap_xy ? transform->raw_height : transform->raw_width;
    uint32_t height = transform->swap_xy ? transform->raw_width : transform->raw_height;
    position_x = (position_x * (transform->screen_width - 1U) + (width - 1U) / 2U) / (width - 1U);
    position_y = (position_y * (transform->screen_height - 1U) + (height - 1U) / 2U) / (height - 1U);
    *screen_x = (uint16_t)(transform->invert_x ? transform->screen_width - 1U - position_x : position_x);
    *screen_y = (uint16_t)(transform->invert_y ? transform->screen_height - 1U - position_y : position_y);
    return true;
}

void ut_touch_release(ut_touch_t *touch)
{
    if (touch) { touch->sample.pressed = false; touch->sample.fingers = 0; }
}

static bool goodix_product(const uint8_t *product)
{
    return !memcmp(product, "911\0", 4) || !memcmp(product, "9110", 4) || !memcmp(product, "9147", 4) ||
           !memcmp(product, "9271", 4) || !memcmp(product, "917S", 4);
}

static bool probe_goodix(ut_touch_t *touch, uint8_t address)
{
    uint8_t identification[10];
    if (!touch->bus.read(touch->bus.context, address, 0x8140U, 2U, identification, sizeof(identification))) return false;
    touch->probe_ack_mask |= address == 0x5dU ? 1U : 2U;
    touch->goodix_product_code = ((uint32_t)identification[0] << 24) | ((uint32_t)identification[1] << 16) |
                                 ((uint32_t)identification[2] << 8) | identification[3];
    if (!goodix_product(identification)) return false;
    unsigned width = identification[6] | ((unsigned)identification[7] << 8);
    unsigned height = identification[8] | ((unsigned)identification[9] << 8);
    if (width < 100U || height < 100U || width > 4096U || height > 4096U) return false;
    touch->kind = UT_TOUCH_GOODIX;
    touch->address = address;
    memcpy(touch->product, identification, 4U);
    touch->transform.raw_width = (uint16_t)width;
    touch->transform.raw_height = (uint16_t)height;
    return true;
}

static bool probe_focaltech(ut_touch_t *touch)
{
    uint8_t version[2], mode, chip_id;
    if (!touch->bus.read(touch->bus.context, 0x38U, 0xa1U, 1U, version, 2U)) return false;
    touch->probe_ack_mask |= 4U;
    touch->ft_version = (uint16_t)(((uint16_t)version[0] << 8) | version[1]);
    bool chip_identified = touch->bus.read(touch->bus.context, 0x38U, 0xa3U, 1U, &chip_id, 1U);
    if (chip_identified) touch->ft_chip_id = chip_id;
    ut_touch_kind_t kind;
    const char *product;
    if (touch->ft_version == 0x3003U) { kind = UT_TOUCH_FT5206; product = "FT5206"; }
    else if (touch->ft_version == 0x0001U && chip_identified && chip_id == 0x54U) { kind = UT_TOUCH_FT5X46; product = "FT5x46"; }
    else return false;
    if (!touch->bus.read(touch->bus.context, 0x38U, 0x00U, 1U, &mode, 1U) || (mode & 0x70U)) return false;
    touch->kind = kind;
    touch->address = 0x38U;
    memcpy(touch->product, product, 7U);
    touch->transform.raw_width = touch->transform.screen_width;
    touch->transform.raw_height = touch->transform.screen_height;
    return true;
}

ut_touch_result_t ut_touch_probe(ut_touch_t *touch, const ut_touch_bus_t *bus, uint16_t screen_width, uint16_t screen_height)
{
    if (!touch) return UT_TOUCH_UNSUPPORTED;
    if (!bus || !bus->read || !bus->write || screen_width < 2U || screen_height < 2U) {
        memset(touch, 0, sizeof(*touch));
        return UT_TOUCH_UNSUPPORTED;
    }
    ut_touch_bus_t registers = *bus;
    memset(touch, 0, sizeof(*touch));
    touch->bus = registers;
    touch->transform.screen_width = screen_width;
    touch->transform.screen_height = screen_height;
    if (registers.reset && !registers.reset(registers.context, false)) return UT_TOUCH_IO_ERROR;
    if (probe_goodix(touch, 0x5dU) || probe_goodix(touch, 0x14U) || probe_focaltech(touch)) return UT_TOUCH_OK;
    if (registers.reset && registers.reset(registers.context, true) && probe_goodix(touch, 0x14U)) return UT_TOUCH_OK;
    return UT_TOUCH_UNSUPPORTED;
}

static ut_touch_result_t poll_goodix(ut_touch_t *touch, uint32_t now_ms)
{
    uint8_t packet[9];
    if (!touch->bus.read(touch->bus.context, touch->address, 0x814eU, 2U, packet, sizeof(packet))) return UT_TOUCH_IO_ERROR;
    if (!(packet[0] & 0x80U)) {
        if (now_ms - touch->last_ready_ms >= 120U) ut_touch_release(touch);
        return UT_TOUCH_NOT_READY;
    }
    uint8_t cleared = 0;
    if (!touch->bus.write(touch->bus.context, touch->address, 0x814eU, 2U, &cleared, 1U)) return UT_TOUCH_IO_ERROR;
    touch->last_ready_ms = now_ms;
    unsigned fingers = packet[0] & 15U;
    if ((packet[0] & 0x60U) || fingers > 5U) return UT_TOUCH_BAD_PACKET;
    touch->sample.fingers = (uint8_t)fingers;
    if (fingers != 1U) { touch->sample.pressed = false; return UT_TOUCH_OK; }
    if ((packet[1] & 0xf0U) || (packet[1] & 15U) >= 5U) return UT_TOUCH_BAD_PACKET;
    touch->sample.raw_x = (uint16_t)(packet[2] | ((uint16_t)packet[3] << 8));
    touch->sample.raw_y = (uint16_t)(packet[4] | ((uint16_t)packet[5] << 8));
    if (!ut_touch_transform(&touch->transform, touch->sample.raw_x, touch->sample.raw_y, &touch->sample.x, &touch->sample.y)) return UT_TOUCH_BAD_PACKET;
    touch->sample.pressed = true;
    return UT_TOUCH_OK;
}

static ut_touch_result_t poll_focaltech(ut_touch_t *touch, uint32_t now_ms)
{
    uint8_t packet[7] = {0};
    if (!touch->bus.read(touch->bus.context, touch->address, 0x00U, 1U, packet, sizeof(packet))) return UT_TOUCH_IO_ERROR;
    memcpy(touch->last_packet, packet, sizeof(packet));
    touch->last_ready_ms = now_ms;
    unsigned fingers = packet[2] & 15U;
    if ((packet[0] & 0x70U) || (packet[2] & 0xf0U) || fingers > 5U) return UT_TOUCH_BAD_PACKET;
    touch->sample.fingers = (uint8_t)fingers;
    if (fingers != 1U) { touch->sample.pressed = false; return UT_TOUCH_OK; }
    unsigned event = packet[3] >> 6;
    if (event == 3U || (packet[3] & 0x30U)) return UT_TOUCH_BAD_PACKET;
    if (event == 1U) { touch->sample.pressed = false; return UT_TOUCH_OK; }
    touch->sample.raw_x = (uint16_t)(((uint16_t)(packet[3] & 15U) << 8) | packet[4]);
    touch->sample.raw_y = (uint16_t)(((uint16_t)(packet[5] & 15U) << 8) | packet[6]);
    if (!ut_touch_transform(&touch->transform, touch->sample.raw_x, touch->sample.raw_y, &touch->sample.x, &touch->sample.y)) return UT_TOUCH_BAD_PACKET;
    touch->sample.pressed = true;
    return UT_TOUCH_OK;
}

ut_touch_result_t ut_touch_poll(ut_touch_t *touch, uint32_t now_ms)
{
    if (!touch || touch->kind == UT_TOUCH_NONE) return UT_TOUCH_UNSUPPORTED;
    touch->reads++;
    ut_touch_result_t result = touch->kind == UT_TOUCH_GOODIX ? poll_goodix(touch, now_ms) : poll_focaltech(touch, now_ms);
    touch->last_result = result;
    if (result == UT_TOUCH_IO_ERROR || result == UT_TOUCH_BAD_PACKET) {
        ut_touch_release(touch);
        touch->errors++;
        if (result == UT_TOUCH_BAD_PACKET) touch->rejected++;
    }
    return result;
}
