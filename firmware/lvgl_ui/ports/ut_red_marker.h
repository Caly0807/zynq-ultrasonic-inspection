#ifndef UT_RED_MARKER_H
#define UT_RED_MARKER_H
#include <stdint.h>
#include <string.h>

typedef struct {
    unsigned status, candidate_count, area_pixels;
    float pixel_x, pixel_y;
} ut_red_marker_t;

static ut_red_marker_t ut_red_marker(const uint8_t *pixels, unsigned width, unsigned height)
{
    static uint8_t mask[240U * 240U];
    static unsigned queue[240U * 240U];
    ut_red_marker_t result = {.status = 2};
    if (!pixels || width < 160 || width > 1280 || height < 120 || height > 720) return result;
    unsigned maximum = width > height ? width : height;
    unsigned step = (maximum + 239U) / 240U;
    unsigned columns = width / step, rows = height / step;
    for (unsigned row = 0; row < rows; row++) {
        for (unsigned column = 0; column < columns; column++) {
            const uint8_t *pixel = pixels + ((row * step + step / 2U) * width + column * step + step / 2U) * 3U;
            unsigned red = pixel[0], green = pixel[1], blue = pixel[2];
            mask[row * columns + column] = red >= 100 && red >= green + 55 && red >= blue + 45 && red * 100 > green * 145 && red * 100 > blue * 145;
        }
    }
    for (unsigned start = 0; start < columns * rows; start++) {
        if (!mask[start]) continue;
        unsigned head = 0, tail = 1, count = 0, sum_x = 0, sum_y = 0;
        queue[0] = start;
        mask[start] = 0;
        while (head < tail) {
            unsigned current = queue[head++], row = current / columns, column = current % columns;
            count++;
            sum_x += column;
            sum_y += row;
            unsigned neighbors[4], neighbor_count = 0;
            if (row) neighbors[neighbor_count++] = current - columns;
            if (row + 1 < rows) neighbors[neighbor_count++] = current + columns;
            if (column) neighbors[neighbor_count++] = current - 1;
            if (column + 1 < columns) neighbors[neighbor_count++] = current + 1;
            for (unsigned index = 0; index < neighbor_count; index++) {
                unsigned next = neighbors[index];
                if (mask[next]) { mask[next] = 0; queue[tail++] = next; }
            }
        }
        if (count >= 6 && (uint64_t)count * 100000 >= (uint64_t)columns * rows * 15) {
            result.candidate_count++;
            result.area_pixels = count * step * step;
            result.pixel_x = (float)sum_x / count * step + step / 2U;
            result.pixel_y = (float)sum_y / count * step + step / 2U;
        }
    }
    if (!result.candidate_count) result.status = 2;
    else if (result.candidate_count > 1) result.status = 3;
    else if ((uint64_t)result.area_pixels * 100 > (uint64_t)width * height * 12) result.status = 4;
    else result.status = 1;
    return result;
}
#endif
