#include "ut_board_demo.h"
#include <string.h>

void ut_board_demo_create(ut_map_t *map, ut_scan_t *scan)
{
    if (!map || !scan) return;
    memset(map, 0, sizeof(*map));
    memset(scan, 0, sizeof(*scan));
    map->columns = map->rows = 16U;
    map->length_mm = map->width_mm = 80.0f;
    map->thickness_mm = 30.0f;
    map->grid_mm = 5.0f;
    memcpy(map->source, "DEMO", 5U);
    for (unsigned row = 0; row < map->rows; row++) {
        for (unsigned column = 0; column < map->columns; column++) {
            ut_map_cell_t *cell = &map->cells[row * map->columns + column];
            if (row >= 13U) continue;
            cell->count = 1U;
            cell->status = UT_MAP_NO_INDICATION;
            int offset_x = (int)column - 9;
            int offset_y = (int)row - 7;
            if (offset_x * offset_x * 2 + offset_y * offset_y * 3 <= 19) {
                cell->status = UT_MAP_SUSPECT;
                cell->depth_valid = true;
                cell->depth_mm = 16.5f + (float)(offset_x * offset_x + offset_y * offset_y) * 0.18f;
            } else if (column == 0U && row >= 7U && row <= 9U) cell->status = UT_MAP_UNCALIBRATED;
            else if (column == 15U && row >= 4U && row <= 5U) cell->status = UT_MAP_INVALID;
            map->point_count++;
            map->sampled_cells++;
            map->suspect_cells += cell->status == UT_MAP_SUSPECT;
            map->depth_cells += cell->depth_valid;
        }
    }
    scan->count = 16U;
    scan->contains_demo = true;
    scan->minimum_x_mm = 2.5f;
    scan->maximum_x_mm = 77.5f;
    for (unsigned index = 0; index < scan->count; index++) {
        const ut_map_cell_t *cell = &map->cells[7U * map->columns + index];
        ut_scan_point_t *point = &scan->points[index];
        point->source = UT_SOURCE_DEMO;
        point->stage_x_mm = 2.5f + (float)index * 5.0f;
        point->status = cell->status == UT_MAP_SUSPECT ? UT_SCAN_SUSPECT : cell->status == UT_MAP_UNCALIBRATED ? UT_SCAN_UNCALIBRATED : UT_SCAN_NO_INDICATION;
        point->depth_valid = cell->depth_valid;
        point->depth_mm = cell->depth_mm;
        scan->suspect_count += point->status == UT_SCAN_SUSPECT;
        scan->uncalibrated_count += point->status == UT_SCAN_UNCALIBRATED;
        scan->no_indication_count += point->status == UT_SCAN_NO_INDICATION;
    }
}
