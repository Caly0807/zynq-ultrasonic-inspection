#ifndef UT_SCAN_H
#define UT_SCAN_H
#include "ut_model.h"

#define UT_SCAN_CAPACITY 256

typedef enum { UT_SCAN_NO_INDICATION, UT_SCAN_SUSPECT, UT_SCAN_UNCALIBRATED, UT_SCAN_INVALID } ut_scan_status_t;
typedef struct {
    char point_id[96];
    char waveform[96];
    float stage_x_mm, world_x_mm, world_y_mm, world_z_mm, depth_mm;
    bool depth_valid;
    ut_source_t source;
    ut_scan_status_t status;
} ut_scan_point_t;
typedef struct {
    ut_scan_point_t points[UT_SCAN_CAPACITY];
    size_t count, suspect_count, no_indication_count, uncalibrated_count, invalid_count;
    float minimum_x_mm, maximum_x_mm;
    bool contains_demo, contains_recorded;
} ut_scan_t;

bool ut_scan_load_csv(const char *path, ut_scan_t *scan, char *error, size_t error_capacity);
bool ut_scan_waveform_path(const char *summary_path, const ut_scan_point_t *point, char *output, size_t capacity);
#endif
