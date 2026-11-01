#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ut_red_marker.h"

static void rectangle(uint8_t *pixels, unsigned width, unsigned left, unsigned top, unsigned right, unsigned bottom)
{
    for (unsigned row = top; row < bottom; row++) {
        for (unsigned column = left; column < right; column++) {
            uint8_t *pixel = pixels + (row * width + column) * 3;
            pixel[0] = 240;
            pixel[1] = 32;
            pixel[2] = 16;
        }
    }
}

int main(void)
{
    uint8_t *pixels = calloc(640U * 480U, 3);
    assert(pixels);
    assert(ut_red_marker(pixels, 640, 480).status == 2);
    rectangle(pixels, 160, 70, 50, 90, 70);
    ut_red_marker_t marker = ut_red_marker(pixels, 160, 120);
    assert(marker.status == 1 && marker.candidate_count == 1 && marker.area_pixels == 400);
    assert(marker.pixel_x == 79.5f && marker.pixel_y == 59.5f);
    rectangle(pixels, 160, 20, 20, 35, 35);
    assert(ut_red_marker(pixels, 160, 120).status == 3);
    memset(pixels, 0, 640U * 480U * 3U);
    rectangle(pixels, 640, 312, 232, 328, 248);
    marker = ut_red_marker(pixels, 640, 480);
    assert(marker.status == 1 && marker.area_pixels == 270);
    assert(marker.pixel_x == 319 && marker.pixel_y == 239.5f);
    const uint8_t skin_colors[][3] = {{220, 140, 110}, {180, 90, 60}, {200, 105, 85}, {150, 80, 55}};
    for (unsigned color = 0; color < sizeof(skin_colors) / sizeof(skin_colors[0]); color++) {
        memset(pixels, 0, 640U * 480U * 3U);
        for (unsigned row = 45; row < 75; row++) {
            for (unsigned column = 65; column < 95; column++)
                memcpy(pixels + (row * 160 + column) * 3, skin_colors[color], 3);
        }
        assert(ut_red_marker(pixels, 160, 120).status == 2);
        rectangle(pixels, 160, 110, 80, 130, 100);
        marker = ut_red_marker(pixels, 160, 120);
        assert(marker.status == 1 && marker.pixel_x == 119.5f && marker.pixel_y == 89.5f);
    }
    memset(pixels, 0, 640U * 480U * 3U);
    rectangle(pixels, 160, 40, 50, 120, 60);
    assert(ut_red_marker(pixels, 160, 120).status == 2);
    rectangle(pixels, 640, 0, 0, 640, 480);
    assert(ut_red_marker(pixels, 640, 480).status == 4);
    assert(ut_red_marker(pixels, 0, 480).status == 2);
    free(pixels);
    puts("NATIVE_MARKER_PASS: absent, unique, ambiguous, oversize and 640x480 sampled centroid");
    return 0;
}
