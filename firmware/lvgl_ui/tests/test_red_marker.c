#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include "../ports/ut_red_marker.h"

static uint8_t pixels[160U * 120U * 3U];

static void rectangle(unsigned left, unsigned top, unsigned right, unsigned bottom,
                      unsigned red, unsigned green, unsigned blue)
{
    for (unsigned row = top; row < bottom; row++) {
        for (unsigned column = left; column < right; column++) {
            unsigned offset = (row * 160U + column) * 3U;
            pixels[offset] = red;
            pixels[offset + 1] = green;
            pixels[offset + 2] = blue;
        }
    }
}

int main(int argument_count, char **arguments)
{
    if (argument_count == 4) {
        unsigned width = (unsigned)strtoul(arguments[2], NULL, 10);
        unsigned height = (unsigned)strtoul(arguments[3], NULL, 10);
        assert(width >= 160 && width <= 1280 && height >= 120 && height <= 720);
        size_t length = (size_t)width * height * 3U;
        uint8_t *input = malloc(length);
        FILE *source = fopen(arguments[1], "rb");
        assert(input && source && fread(input, 1, length, source) == length && fgetc(source) == EOF);
        assert(fclose(source) == 0);
        ut_red_marker_t result = ut_red_marker(input, width, height);
        printf("{\"status\":%u,\"candidate_count\":%u,\"pixel\":[%.3f,%.3f],\"area_pixels\":%u}\n",
               result.status, result.candidate_count, result.pixel_x, result.pixel_y, result.area_pixels);
        free(input);
        return 0;
    }
    rectangle(0, 0, 160, 120, 90, 100, 120);
    rectangle(60, 40, 100, 80, 180, 45, 5);
    rectangle(82, 56, 86, 60, 20, 20, 20);
    ut_red_marker_t result = ut_red_marker(pixels, 160, 120);
    assert(result.status == 5 && result.candidate_count == 1 && result.area_pixels == 16);
    assert(fabsf(result.pixel_x - 83.5f) < .01f && fabsf(result.pixel_y - 57.5f) < .01f);
    rectangle(20, 20, 35, 35, 240, 32, 16);
    result = ut_red_marker(pixels, 160, 120);
    assert(result.status == 5 && result.candidate_count == 1);
    rectangle(70, 62, 74, 66, 20, 20, 20);
    result = ut_red_marker(pixels, 160, 120);
    assert(result.status == 3 && result.candidate_count == 2);
    rectangle(0, 0, 160, 120, 90, 100, 120);
    rectangle(65, 45, 95, 75, 180, 90, 60);
    assert(ut_red_marker(pixels, 160, 120).status == 2);
    rectangle(0, 0, 160, 120, 90, 100, 120);
    rectangle(70, 50, 90, 70, 240, 32, 16);
    result = ut_red_marker(pixels, 160, 120);
    assert(result.status == 1 && result.candidate_count == 1);
    assert(fabsf(result.pixel_x - 79.5f) < .01f && fabsf(result.pixel_y - 59.5f) < .01f);
    puts("BLACK_DOT_MARKER_PASS: red surround, dot center, distractor rejection, ambiguity, legacy fallback");
    return 0;
}
