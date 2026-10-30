#ifndef UT_MAP_H
#define UT_MAP_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UT_MAP_CAPACITY 1024
#define UT_MAP_MAX_TEXT 90000
typedef enum { UT_MAP_UNSCANNED, UT_MAP_NO_INDICATION, UT_MAP_SUSPECT, UT_MAP_UNCALIBRATED, UT_MAP_INVALID } ut_map_status_t;
typedef struct {
    ut_map_status_t status;
    uint32_t count;
    float depth_mm;
    bool depth_valid;
} ut_map_cell_t;
typedef struct {
    unsigned columns, rows;
    float length_mm, width_mm, thickness_mm, grid_mm;
    char source[12];
    ut_map_cell_t cells[UT_MAP_CAPACITY];
    unsigned sampled_cells, suspect_cells, depth_cells;
    uint32_t point_count;
} ut_map_t;
bool ut_map_validate(const ut_map_t *map);
bool ut_map_decode(const char *data, size_t length, ut_map_t *output, char *error, size_t error_capacity);
bool ut_map_load_csv(const char *path, ut_map_t *output, char *error, size_t error_capacity);
#endif
