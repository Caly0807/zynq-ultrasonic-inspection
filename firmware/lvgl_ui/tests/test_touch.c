#include "ut_touch.h"
#include "ut_touch_input.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    bool goodix, focaltech, io_error, write_error, high_only, chip_read_error, advance_ft_frame;
    unsigned resets, writes, ft_snapshot_reads;
    uint8_t identification[10], goodix_packet[9], ft_packet[7];
    uint16_t ft_version;
    uint8_t ft_chip_id;
} fixture_t;

static bool read_register(void *context, uint8_t address, uint16_t register_address, unsigned register_bytes, uint8_t *data, size_t length)
{
    fixture_t *fixture = context;
    if (fixture->io_error) return false;
    if (fixture->goodix && (address == 0x5dU || address == 0x14U) && register_bytes == 2U && (!fixture->high_only || address == 0x14U)) {
        if (register_address == 0x8140U && length == 10U) { memcpy(data, fixture->identification, length); return true; }
        if (register_address == 0x814eU && length == 9U) { memcpy(data, fixture->goodix_packet, length); return true; }
    }
    if (fixture->focaltech && address == 0x38U && register_bytes == 1U) {
        if (register_address == 0xa1U && length == 2U) { data[0] = (uint8_t)(fixture->ft_version >> 8); data[1] = (uint8_t)fixture->ft_version; return true; }
        if (register_address == 0xa3U && length == 1U) { data[0] = fixture->ft_chip_id; return !fixture->chip_read_error; }
        if (register_address == 0U && length == 1U) { data[0] = fixture->ft_packet[0]; return true; }
        if (register_address == 2U && length == 1U) {
            data[0] = fixture->ft_packet[2];
            if (fixture->advance_ft_frame) memset(fixture->ft_packet, 0, sizeof(fixture->ft_packet));
            return true;
        }
        if (register_address == 3U && length == 4U) { memcpy(data, fixture->ft_packet + 3U, length); return true; }
        if (register_address == 0U && length == 7U) {
            fixture->ft_snapshot_reads++;
            memcpy(data, fixture->ft_packet, length);
            if (fixture->advance_ft_frame) memset(fixture->ft_packet, 0, sizeof(fixture->ft_packet));
            return true;
        }
    }
    return false;
}

static bool write_register(void *context, uint8_t address, uint16_t register_address, unsigned register_bytes, const uint8_t *data, size_t length)
{
    fixture_t *fixture = context;
    assert(address == 0x5dU || address == 0x14U);
    assert(register_address == 0x814eU && register_bytes == 2U && length == 1U && data[0] == 0U);
    fixture->writes++;
    return !fixture->write_error;
}

static bool reset_controller(void *context, bool high_address)
{
    fixture_t *fixture = context;
    (void)high_address;
    fixture->resets++;
    return !fixture->io_error;
}

int main(void)
{
    fixture_t fixture = {0};
    fixture.goodix = true;
    memcpy(fixture.identification, "911", 4U);
    fixture.identification[6] = 0x20U;
    fixture.identification[7] = 3U;
    fixture.identification[8] = 0xe0U;
    fixture.identification[9] = 1U;
    ut_touch_bus_t bus = {&fixture, read_register, write_register, reset_controller};
    ut_touch_t touch;
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_OK);
    assert(touch.kind == UT_TOUCH_GOODIX && touch.address == 0x5dU && !strcmp(touch.product, "911"));
    assert(touch.transform.raw_width == 800U && touch.transform.raw_height == 480U && fixture.writes == 0U);
    assert(ut_touch_probe(&touch, &touch.bus, 800U, 480U) == UT_TOUCH_OK);
    fixture.goodix_packet[0] = 0x81U;
    fixture.goodix_packet[2] = 0x1fU;
    fixture.goodix_packet[3] = 3U;
    fixture.goodix_packet[4] = 0xdfU;
    fixture.goodix_packet[5] = 1U;
    assert(ut_touch_poll(&touch, 100U) == UT_TOUCH_OK && touch.sample.pressed && touch.sample.x == 799U && touch.sample.y == 479U);
    assert(fixture.writes == 1U);
    fixture.goodix_packet[0] = 0U;
    assert(ut_touch_poll(&touch, 120U) == UT_TOUCH_NOT_READY && touch.sample.pressed);
    assert(ut_touch_poll(&touch, 220U) == UT_TOUCH_NOT_READY && !touch.sample.pressed && fixture.writes == 1U);
    fixture.goodix_packet[0] = 0x82U;
    assert(ut_touch_poll(&touch, 240U) == UT_TOUCH_OK && !touch.sample.pressed && touch.sample.fingers == 2U);
    fixture.goodix_packet[0] = 0x81U;
    assert(ut_touch_poll(&touch, 260U) == UT_TOUCH_OK && touch.sample.pressed);
    fixture.io_error = true;
    assert(ut_touch_poll(&touch, 280U) == UT_TOUCH_IO_ERROR && !touch.sample.pressed);
    fixture.io_error = false;
    fixture.write_error = true;
    assert(ut_touch_poll(&touch, 300U) == UT_TOUCH_IO_ERROR && !touch.sample.pressed);
    fixture.write_error = false;
    fixture.goodix_packet[2] = 0x20U;
    assert(ut_touch_poll(&touch, 320U) == UT_TOUCH_BAD_PACKET && !touch.sample.pressed);
    fixture.goodix_packet[2] = 0x1fU;
    fixture.goodix_packet[0] = 0x86U;
    assert(ut_touch_poll(&touch, 340U) == UT_TOUCH_BAD_PACKET);
    fixture.goodix_packet[0] = 0xc1U;
    assert(ut_touch_poll(&touch, 360U) == UT_TOUCH_BAD_PACKET);
    fixture.goodix_packet[0] = 0x81U;
    fixture.goodix_packet[1] = 0x10U;
    assert(ut_touch_poll(&touch, 380U) == UT_TOUCH_BAD_PACKET);
    assert(touch.errors == 6U && touch.rejected == 4U);
    fixture.goodix_packet[1] = 0U;
    fixture.high_only = true;
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_OK && touch.address == 0x14U);
    memcpy(fixture.identification, "????", 4U);
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_UNSUPPORTED && touch.kind == UT_TOUCH_NONE);
    assert(ut_touch_poll(&touch, 400U) == UT_TOUCH_UNSUPPORTED);
    fixture.goodix = false;
    fixture.focaltech = true;
    fixture.ft_version = 0x3003U;
    fixture.ft_chip_id = 0x54U;
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_OK && touch.kind == UT_TOUCH_FT5206);
    assert(touch.probe_ack_mask == 4U && touch.ft_version == 0x3003U && touch.ft_chip_id == 0x54U);
    fixture.ft_packet[2] = 1U;
    fixture.ft_packet[3] = 0x83U;
    fixture.ft_packet[4] = 0x1fU;
    fixture.ft_packet[5] = 0x31U;
    fixture.ft_packet[6] = 0xdfU;
    assert(ut_touch_poll(&touch, 500U) == UT_TOUCH_OK && touch.sample.pressed && touch.sample.x == 799U && touch.sample.y == 479U);
    fixture.ft_packet[3] = 0x43U;
    assert(ut_touch_poll(&touch, 520U) == UT_TOUCH_OK && !touch.sample.pressed);
    fixture.ft_packet[3] = 0xc3U;
    assert(ut_touch_poll(&touch, 540U) == UT_TOUCH_BAD_PACKET && !touch.sample.pressed);
    fixture.ft_packet[2] = 2U;
    assert(ut_touch_poll(&touch, 560U) == UT_TOUCH_OK && !touch.sample.pressed);
    fixture.ft_packet[2] = 0x81U;
    assert(ut_touch_poll(&touch, 580U) == UT_TOUCH_BAD_PACKET);
    unsigned writes_before_ft5x46 = fixture.writes;
    fixture.ft_version = 0x0001U;
    memset(fixture.ft_packet, 0, sizeof(fixture.ft_packet));
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_OK && touch.kind == UT_TOUCH_FT5X46);
    assert(touch.address == 0x38U && touch.ft_version == 0x0001U && touch.ft_chip_id == 0x54U);
    assert(!strcmp(touch.product, "FT5x46") && touch.transform.raw_width == 800U && touch.transform.raw_height == 480U);
    assert(ut_touch_poll(&touch, 600U) == UT_TOUCH_OK && !touch.sample.pressed);
    fixture.ft_packet[2] = 1U;
    assert(ut_touch_poll(&touch, 620U) == UT_TOUCH_OK && touch.sample.pressed && touch.sample.x == 0U && touch.sample.y == 0U);
    fixture.ft_packet[3] = 0x83U;
    fixture.ft_packet[4] = 0x1fU;
    fixture.ft_packet[5] = 0x31U;
    fixture.ft_packet[6] = 0xdfU;
    assert(ut_touch_poll(&touch, 640U) == UT_TOUCH_OK && touch.sample.pressed && touch.sample.x == 799U && touch.sample.y == 479U);
    fixture.ft_packet[3] = 0x43U;
    assert(ut_touch_poll(&touch, 660U) == UT_TOUCH_OK && !touch.sample.pressed);
    fixture.ft_packet[2] = 2U;
    assert(ut_touch_poll(&touch, 680U) == UT_TOUCH_OK && !touch.sample.pressed && touch.sample.fingers == 2U);
    fixture.ft_packet[2] = 1U;
    fixture.ft_packet[3] = 0xc3U;
    assert(ut_touch_poll(&touch, 700U) == UT_TOUCH_BAD_PACKET && !touch.sample.pressed);
    assert(touch.last_result == UT_TOUCH_BAD_PACKET && !memcmp(touch.last_packet, fixture.ft_packet, sizeof(touch.last_packet)));
    fixture.ft_packet[3] = 0x83U;
    fixture.ft_packet[4] = 0x20U;
    assert(ut_touch_poll(&touch, 720U) == UT_TOUCH_BAD_PACKET && !touch.sample.pressed);
    fixture.ft_packet[3] = 0x81U;
    fixture.ft_packet[4] = 0x9aU;
    fixture.ft_packet[5] = 0U;
    fixture.ft_packet[6] = 0xdcU;
    fixture.advance_ft_frame = true;
    unsigned snapshots_before = fixture.ft_snapshot_reads;
    assert(ut_touch_poll(&touch, 740U) == UT_TOUCH_OK && touch.sample.pressed && touch.sample.x == 410U && touch.sample.y == 220U);
    assert(fixture.ft_snapshot_reads == snapshots_before + 1U);
    assert(ut_touch_poll(&touch, 760U) == UT_TOUCH_OK && !touch.sample.pressed);
    fixture.advance_ft_frame = false;
    fixture.ft_packet[2] = 0U;
    fixture.chip_read_error = true;
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_UNSUPPORTED && touch.kind == UT_TOUCH_NONE);
    fixture.chip_read_error = false;
    fixture.ft_chip_id = 0x55U;
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_UNSUPPORTED && touch.kind == UT_TOUCH_NONE);
    fixture.ft_chip_id = 0x54U;
    fixture.ft_packet[0] = 0x40U;
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_UNSUPPORTED && touch.kind == UT_TOUCH_NONE);
    fixture.ft_packet[0] = 0U;
    assert(fixture.writes == writes_before_ft5x46);
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_OK);
    ut_touch_reset_board_orientation(&touch);
    assert(touch.transform.raw_width == 480U && touch.transform.raw_height == 800U && touch.transform.swap_xy);
    const uint16_t portrait_points[][2] = {{105U, 612U}, {0U, 0U}, {479U, 799U}, {0U, 799U}, {479U, 0U}};
    for (unsigned index = 0; index < sizeof(portrait_points) / sizeof(portrait_points[0]); index++) {
        fixture.ft_packet[2] = 1U;
        fixture.ft_packet[3] = (uint8_t)(0x80U | (portrait_points[index][0] >> 8));
        fixture.ft_packet[4] = (uint8_t)portrait_points[index][0];
        fixture.ft_packet[5] = (uint8_t)(portrait_points[index][1] >> 8);
        fixture.ft_packet[6] = (uint8_t)portrait_points[index][1];
        assert(ut_touch_poll(&touch, 800U + index) == UT_TOUCH_OK && touch.sample.pressed);
        assert(touch.sample.x == portrait_points[index][1] && touch.sample.y == portrait_points[index][0]);
    }
    fixture.ft_packet[3] = 0x81U;
    fixture.ft_packet[4] = 0xe0U;
    assert(ut_touch_poll(&touch, 810U) == UT_TOUCH_BAD_PACKET && !touch.sample.pressed);
    fixture.ft_packet[3] = 0x80U;
    fixture.ft_packet[4] = 105U;
    fixture.ft_packet[5] = 3U;
    fixture.ft_packet[6] = 0x20U;
    assert(ut_touch_poll(&touch, 811U) == UT_TOUCH_BAD_PACKET && !touch.sample.pressed);
    touch.transform.swap_xy = false;
    touch.transform.invert_x = touch.transform.invert_y = true;
    ut_touch_reset_board_orientation(&touch);
    assert(touch.transform.swap_xy && !touch.transform.invert_x && !touch.transform.invert_y);
    touch.kind = UT_TOUCH_FT5206;
    ut_touch_reset_board_orientation(&touch);
    assert(touch.transform.raw_width == 800U && touch.transform.raw_height == 480U && !touch.transform.swap_xy);
    touch.kind = UT_TOUCH_GOODIX;
    touch.transform.raw_width = 1280U;
    touch.transform.raw_height = 720U;
    ut_touch_reset_board_orientation(&touch);
    assert(touch.transform.raw_width == 1280U && touch.transform.raw_height == 720U && !touch.transform.swap_xy);
    ut_touch_reset_board_orientation(NULL);
    uint16_t screen_x, screen_y;
    ut_touch_transform_t transform = {800U, 480U, 800U, 480U, false, false, false};
    assert(ut_touch_transform(&transform, 799U, 479U, &screen_x, &screen_y) && screen_x == 799U && screen_y == 479U);
    transform.invert_x = transform.invert_y = true;
    assert(ut_touch_transform(&transform, 0U, 0U, &screen_x, &screen_y) && screen_x == 799U && screen_y == 479U);
    transform.swap_xy = true;
    assert(ut_touch_transform(&transform, 799U, 0U, &screen_x, &screen_y) && screen_x == 799U && screen_y == 0U);
    transform.raw_width = 480U;
    transform.raw_height = 800U;
    transform.invert_x = transform.invert_y = false;
    assert(ut_touch_transform(&transform, 479U, 799U, &screen_x, &screen_y) && screen_x == 799U && screen_y == 479U);
    assert(!ut_touch_transform(&transform, 480U, 0U, &screen_x, &screen_y));
    transform.raw_width = 1U;
    assert(!ut_touch_transform(&transform, 0U, 0U, &screen_x, &screen_y));
    assert(!ut_touch_transform(NULL, 0U, 0U, &screen_x, &screen_y));
    assert(ut_touch_probe(&touch, NULL, 800U, 480U) == UT_TOUCH_UNSUPPORTED);
    assert(touch.kind == UT_TOUCH_NONE && !touch.sample.pressed);
    fixture.ft_version = 0x1234U;
    assert(ut_touch_probe(&touch, &bus, 800U, 480U) == UT_TOUCH_UNSUPPORTED);
    assert(touch.probe_ack_mask == 4U && touch.ft_version == 0x1234U && touch.ft_chip_id == 0x54U);
    ut_touch_input_t input = {0};
    ut_touch_sample_t sample = {true, 1U, 0U, 0U, 100U, 200U};
    ut_touch_input_update(&input, UT_TOUCH_OK, &sample);
    assert(!input.pressed && !input.armed);
    sample.pressed = false; sample.fingers = 0U;
    ut_touch_input_update(&input, UT_TOUCH_OK, &sample);
    assert(input.armed && !input.pressed);
    sample.pressed = true; sample.fingers = 1U;
    ut_touch_input_update(&input, UT_TOUCH_OK, &sample);
    assert(input.pressed && input.x == 100U && input.y == 200U);
    ut_touch_input_update(&input, UT_TOUCH_NOT_READY, &sample);
    assert(input.pressed && !input.cancel);
    sample.pressed = false;
    ut_touch_input_update(&input, UT_TOUCH_NOT_READY, &sample);
    assert(input.cancel && !input.pressed && !input.armed);
    sample.pressed = true;
    ut_touch_input_update(&input, UT_TOUCH_OK, &sample);
    assert(!input.pressed);
    sample.pressed = false; sample.fingers = 0U;
    ut_touch_input_update(&input, UT_TOUCH_OK, &sample);
    sample.pressed = true; sample.fingers = 1U;
    ut_touch_input_update(&input, UT_TOUCH_OK, &sample);
    ut_touch_input_update(&input, UT_TOUCH_IO_ERROR, &sample);
    assert(input.cancel && !input.armed && !input.pressed);
    puts("PASS: touch identification, read-only probe, Goodix ready/clear, FT5206 and FT5x46 packets, release, multi-touch suppression, bad data, timeout and transforms; hardware NOT tested");
    return 0;
}
