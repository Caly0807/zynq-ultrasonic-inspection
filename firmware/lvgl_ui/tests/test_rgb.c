#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "ut_rgb.h"

int main(void)
{
    static const uint32_t colors[] = {0xffffffffU, 0xaaff0000U, 0x5500ff00U, 0x110000ffU,
                                      0x22ffff00U, 0x3300ffffU, 0x44ff00ffU, 0xff000000U};
    static const uint8_t expected[] = {255,255,255, 0,0,255, 255,0,0, 0,255,0,
                                      255,0,255, 255,255,0, 0,255,255, 0,0,0};
    uint8_t destination[sizeof(expected) + 2U];
    memset(destination, 0x5a, sizeof(destination));
    ut_rgb_copy_row(destination + 1U, colors, 8U);
    assert(destination[0] == 0x5a && destination[sizeof(destination) - 1U] == 0x5a);
    assert(memcmp(destination + 1U, expected, sizeof(expected)) == 0);
    ut_rgb_copy_row(NULL, NULL, 0U);
    for (uint32_t index = 0; index < 1024U; index++) {
        uint32_t pixel = index * 0x10204081U;
        ut_rgb_copy_row(destination + 1U, &pixel, 1U);
        assert(destination[1] == ((pixel >> 8) & 255U));
        assert(destination[2] == (pixel & 255U));
        assert(destination[3] == ((pixel >> 16) & 255U));
    }
    puts("PASS: XRGB8888 to platform G/B/R byte order, alpha ignored, guards and zero pixels");
    return 0;
}
