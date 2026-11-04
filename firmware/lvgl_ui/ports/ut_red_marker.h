#ifndef UT_RED_MARKER_H
#define UT_RED_MARKER_H
#include <stdint.h>
#include <string.h>

typedef struct {
    unsigned status, candidate_count, area_pixels;
    float pixel_x, pixel_y;
} ut_red_marker_t;

static int ut_marker_red_pixel(const uint8_t *pixel)
{
    unsigned red = pixel[0], green = pixel[1], blue = pixel[2];
    return red >= 110 && red >= green + 65 && red >= blue + 55 &&
        red * 100 > green * 170 && red * 100 > blue * 170;
}

static ut_red_marker_t ut_center_dot(const uint8_t *pixels, unsigned width, unsigned height,
                                    unsigned step, unsigned columns, unsigned rows,
                                    uint8_t *mask, unsigned *queue)
{
    ut_red_marker_t result = {.status = 2};
    for (unsigned row = 0; row < rows; row++) {
        for (unsigned column = 0; column < columns; column++) {
            const uint8_t *pixel = pixels + ((row * step + step / 2U) * width + column * step + step / 2U) * 3U;
            mask[row * columns + column] = pixel[0] <= 80 && pixel[1] <= 80 && pixel[2] <= 80;
        }
    }
    for (unsigned start = 0; start < columns * rows; start++) {
        if (!mask[start]) continue;
        unsigned head = 0, tail = 1, count = 0, sum_x = 0, sum_y = 0;
        unsigned minimum_x = columns, minimum_y = rows, maximum_x = 0, maximum_y = 0;
        queue[0] = start;
        mask[start] = 0;
        while (head < tail) {
            unsigned current = queue[head++], row = current / columns, column = current % columns;
            count++;
            sum_x += column;
            sum_y += row;
            if (column < minimum_x) minimum_x = column;
            if (column > maximum_x) maximum_x = column;
            if (row < minimum_y) minimum_y = row;
            if (row > maximum_y) maximum_y = row;
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
        unsigned extent_x = maximum_x - minimum_x + 1, extent_y = maximum_y - minimum_y + 1;
        unsigned area = count * step * step;
        if (count < 3 || area < 9 || (uint64_t)area * 200 > (uint64_t)width * height ||
            extent_x > extent_y * 2 || extent_y > extent_x * 2 || count * 4 < extent_x * extent_y) continue;
        float center_x = (float)sum_x / count * step + step / 2U;
        float center_y = (float)sum_y / count * step + step / 2U;
        unsigned red_ring = 0;
        for (int direction_y = -1; direction_y <= 1; direction_y++) {
            for (int direction_x = -1; direction_x <= 1; direction_x++) {
                if (!direction_x && !direction_y) continue;
                int sample_x = (int)center_x + direction_x * (int)(extent_x * step / 2U + step * 2U);
                int sample_y = (int)center_y + direction_y * (int)(extent_y * step / 2U + step * 2U);
                if (sample_x >= 0 && sample_y >= 0 && sample_x < (int)width && sample_y < (int)height &&
                    ut_marker_red_pixel(pixels + ((unsigned)sample_y * width + (unsigned)sample_x) * 3U)) red_ring++;
            }
        }
        if (red_ring < 6) continue;
        result.candidate_count++;
        result.pixel_x = center_x;
        result.pixel_y = center_y;
        result.area_pixels = area;
    }
    if (result.candidate_count) result.status = result.candidate_count == 1 ? 5 : 3;
    return result;
}

static ut_red_marker_t ut_red_marker(const uint8_t *pixels, unsigned width, unsigned height)
{
    static uint8_t mask[240U * 240U];
    static unsigned queue[240U * 240U];
    ut_red_marker_t result = {.status = 2};
    if (!pixels || width < 160 || width > 1280 || height < 120 || height > 720) return result;
    unsigned maximum = width > height ? width : height;
    unsigned step = (maximum + 239U) / 240U;
    unsigned columns = width / step, rows = height / step;
    result = ut_center_dot(pixels, width, height, step, columns, rows, mask, queue);
    if (result.candidate_count) return result;
    for (unsigned row = 0; row < rows; row++) {
        for (unsigned column = 0; column < columns; column++) {
            const uint8_t *pixel = pixels + ((row * step + step / 2U) * width + column * step + step / 2U) * 3U;
            unsigned red = pixel[0], green = pixel[1], blue = pixel[2];
            mask[row * columns + column] = red >= 110 && red >= green + 65 && red >= blue + 55 &&
                red * 100 > green * 170 && red * 100 > blue * 170 &&
                ((int)green - (int)blue) * 100 <= ((int)red - (int)blue) * 15;
        }
    }
    for (unsigned start = 0; start < columns * rows; start++) {
        if (!mask[start]) continue;
        unsigned head = 0, tail = 1, count = 0, sum_x = 0, sum_y = 0;
        unsigned minimum_x = columns, minimum_y = rows, maximum_x = 0, maximum_y = 0;
        queue[0] = start;
        mask[start] = 0;
        while (head < tail) {
            unsigned current = queue[head++], row = current / columns, column = current % columns;
            count++;
            sum_x += column;
            sum_y += row;
            if (column < minimum_x) minimum_x = column;
            if (column > maximum_x) maximum_x = column;
            if (row < minimum_y) minimum_y = row;
            if (row > maximum_y) maximum_y = row;
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
            unsigned extent_x = maximum_x - minimum_x + 1, extent_y = maximum_y - minimum_y + 1;
            unsigned larger = extent_x > extent_y ? extent_x : extent_y;
            unsigned smaller = extent_x < extent_y ? extent_x : extent_y;
            if (larger * 10 > smaller * 18 || count * 100 < extent_x * extent_y * 50) continue;
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
