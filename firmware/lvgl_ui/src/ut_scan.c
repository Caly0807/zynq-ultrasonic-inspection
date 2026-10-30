#include "ut_scan.h"
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static size_t split_row(char *line, char **fields, size_t capacity)
{
    char *reader = line, *writer = line;
    size_t count = 0;
    while (count < capacity) {
        fields[count++] = writer;
        if (*reader == '"') {
            reader++;
            bool closed = false;
            while (*reader) {
                if (*reader == '"') {
                    reader++;
                    if (*reader != '"') { closed = true; break; }
                }
                *writer++ = *reader++;
            }
            if (!closed || (*reader && *reader != ',')) return 0;
        } else {
            while (*reader && *reader != ',') {
                if (*reader == '"') return 0;
                *writer++ = *reader++;
            }
        }
        bool next = *reader == ',';
        if (next) reader++;
        *writer++ = '\0';
        if (!next) return count;
    }
    return 0;
}

static bool number(const char *field, float *value)
{
    char *end;
    errno = 0;
    *value = strtof(field, &end);
    return end != field && !*end && errno != ERANGE && isfinite(*value);
}

static bool safe_waveform(const char *path)
{
    const char *prefix = "waveforms/";
    size_t length = strlen(path), prefix_length = strlen(prefix);
    if (length <= prefix_length + 4 || strncmp(path, prefix, prefix_length) || strcmp(path + length - 4, ".csv")) return false;
    const char *filename = path + prefix_length;
    if (strstr(filename, "..")) return false;
    while (*filename) {
        unsigned char character = (unsigned char)*filename++;
        if (!isalnum(character) && character != '_' && character != '-' && character != '.') return false;
    }
    return true;
}

bool ut_scan_load_csv(const char *path, ut_scan_t *scan, char *error, size_t error_capacity)
{
    static const char *header[] = {"point_id", "stage_x_mm", "world_x_mm", "world_y_mm", "world_z_mm", "source", "status", "depth_mm", "waveform"};
    FILE *input = fopen(path, "rb");
    if (!input) { snprintf(error, error_capacity, "Cannot open scan summary"); return false; }
    ut_scan_t incoming = {0};
    char line[2048], *fields[9];
    unsigned line_number = 0;
    const char *failure = NULL;
    while (fgets(line, sizeof(line), input)) {
        line_number++;
        if (!strchr(line, '\n') && !feof(input)) { failure = "Scan row is incomplete or too long"; break; }
        line[strcspn(line, "\r\n")] = '\0';
        char *row = line;
        if (line_number == 1 && !strncmp(row, "\xEF\xBB\xBF", 3)) row += 3;
        if (split_row(row, fields, 9) != 9) { failure = "Expected nine complete scan columns"; break; }
        if (line_number == 1) {
            for (size_t index = 0; index < 9; index++) if (strcmp(fields[index], header[index])) failure = "Unsupported scan summary header";
            if (failure) break;
            continue;
        }
        if (incoming.count == UT_SCAN_CAPACITY) { failure = "Scan exceeds 256-point capacity"; break; }
        ut_scan_point_t *point = &incoming.points[incoming.count];
        if (!fields[0][0] || strlen(fields[0]) >= sizeof(point->point_id) || strlen(fields[8]) >= sizeof(point->waveform) || !safe_waveform(fields[8])) {
            failure = "Invalid point identifier or unsafe waveform path"; break;
        }
        for (size_t index = 0; index < incoming.count; index++) if (!strcmp(incoming.points[index].point_id, fields[0])) failure = "Duplicate scan point identifier";
        if (failure) break;
        if (!number(fields[1], &point->stage_x_mm) || !number(fields[2], &point->world_x_mm) ||
            !number(fields[3], &point->world_y_mm) || !number(fields[4], &point->world_z_mm)) {
            failure = "Scan coordinates must be finite numbers"; break;
        }
        if (strcmp(fields[5], "LIVE") && strcmp(fields[5], "RECORDED") && strcmp(fields[5], "DEMO")) { failure = "Unknown scan provenance"; break; }
        const char *status = fields[6];
        bool demo_status = !strncmp(status, "DEMO_", 5);
        if (demo_status) status += 5;
        point->source = !strcmp(fields[5], "DEMO") || demo_status ? UT_SOURCE_DEMO : UT_SOURCE_RECORDED;
        if (!strcmp(status, "SUSPECT")) { point->status = UT_SCAN_SUSPECT; incoming.suspect_count++; }
        else if (!strcmp(status, "NO_INDICATION")) { point->status = UT_SCAN_NO_INDICATION; incoming.no_indication_count++; }
        else if (!strcmp(status, "UNCALIBRATED")) { point->status = UT_SCAN_UNCALIBRATED; incoming.uncalibrated_count++; }
        else if (!strcmp(status, "INVALID")) { point->status = UT_SCAN_INVALID; incoming.invalid_count++; }
        else { failure = "Unknown scan diagnosis"; break; }
        if (fields[7][0]) {
            if (!number(fields[7], &point->depth_mm) || point->depth_mm < 0 || point->status != UT_SCAN_SUSPECT) {
                failure = "Invalid suspect depth estimate"; break;
            }
            point->depth_valid = true;
        }
        strcpy(point->point_id, fields[0]);
        strcpy(point->waveform, fields[8]);
        incoming.contains_demo |= point->source == UT_SOURCE_DEMO;
        incoming.contains_recorded |= point->source == UT_SOURCE_RECORDED;
        if (!incoming.count || point->stage_x_mm < incoming.minimum_x_mm) incoming.minimum_x_mm = point->stage_x_mm;
        if (!incoming.count || point->stage_x_mm > incoming.maximum_x_mm) incoming.maximum_x_mm = point->stage_x_mm;
        incoming.count++;
    }
    if (ferror(input)) failure = "Scan summary read failed";
    fclose(input);
    if (!failure && !incoming.count) failure = "Scan summary has no committed points";
    if (failure) { snprintf(error, error_capacity, "Row %u: %s", line_number, failure); return false; }
    *scan = incoming;
    if (error_capacity) error[0] = '\0';
    return true;
}

bool ut_scan_waveform_path(const char *summary_path, const ut_scan_point_t *point, char *output, size_t capacity)
{
    if (!safe_waveform(point->waveform)) return false;
    const char *separator = strrchr(summary_path, '/');
    const char *backslash = strrchr(summary_path, '\\');
    if (backslash && (!separator || backslash > separator)) separator = backslash;
    size_t prefix_length = separator ? (size_t)(separator - summary_path + 1) : 0;
    size_t waveform_length = strlen(point->waveform);
    if (prefix_length + waveform_length + 1 > capacity) return false;
    memcpy(output, summary_path, prefix_length);
    memcpy(output + prefix_length, point->waveform, waveform_length + 1);
    return true;
}
