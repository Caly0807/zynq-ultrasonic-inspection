#include "ut_rgb.h"

void ut_rgb_copy_row(uint8_t *destination, const uint32_t *source, size_t count)
{
    for (size_t index = 0; index < count; index++) {
        uint32_t pixel = source[index];
        destination[index * 3U] = (uint8_t)(pixel >> 8);
        destination[index * 3U + 1U] = (uint8_t)pixel;
        destination[index * 3U + 2U] = (uint8_t)(pixel >> 16);
    }
}
