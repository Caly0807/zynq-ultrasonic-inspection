#include "ut_map.h"
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool reject(char *error, size_t capacity, const char *message)
{
    if (error && capacity) snprintf(error, capacity, "%s", message);
    return false;
}

static bool unsigned_value(const char *text, unsigned maximum, unsigned *value)
{
    if (!text[0]) return false;
    for (const char *cursor = text; *cursor; cursor++) if (*cursor < '0' || *cursor > '9') return false;
    char *end;
    errno = 0;
    unsigned long parsed = strtoul(text, &end, 10);
    if (errno || *end || parsed > maximum) return false;
    *value = (unsigned)parsed;
    return true;
}

static bool positive_float(const char *text, float *value)
{
    if (!text[0] || text[0] == ' ' || text[0] == '\t') return false;
    char *end;
    errno = 0;
    float parsed = strtof(text, &end);
    if (errno || *end || !isfinite(parsed) || parsed <= 0 || parsed > 10000) return false;
    *value = parsed;
    return true;
}

static unsigned fields(char *line, char **tokens, unsigned capacity)
{
    unsigned count = 0;
    char *cursor = line;
    while (count < capacity) {
        tokens[count++] = cursor;
        char *separator = strchr(cursor, ',');
        if (!separator) return count;
        *separator = 0;
        cursor = separator + 1;
    }
    return capacity + 1;
}

bool ut_map_validate(const ut_map_t *map)
{
    if (!map || !map->columns || !map->rows || map->columns > 32U || map->rows > 32U ||
        !memchr(map->source, 0, sizeof(map->source)) || (strcmp(map->source, "DEMO") && strcmp(map->source, "RECORDED") && strcmp(map->source, "LIVE"))) return false;
    const float dimensions[] = {map->length_mm, map->width_mm, map->thickness_mm, map->grid_mm};
    for (unsigned index = 0; index < 4U; index++) if (!isfinite(dimensions[index]) || dimensions[index] <= 0 || dimensions[index] > 10000) return false;
    if (ceilf(map->length_mm / map->grid_mm) != (float)map->columns || ceilf(map->width_mm / map->grid_mm) != (float)map->rows) return false;
    unsigned sampled = 0, suspect = 0, depth = 0, points = 0;
    for (unsigned index = 0; index < map->columns * map->rows; index++) {
        const ut_map_cell_t *cell = &map->cells[index];
        if ((unsigned)cell->status > UT_MAP_INVALID || cell->count > 2048U || ((cell->status == UT_MAP_UNSCANNED) != (cell->count == 0U))) return false;
        if (cell->depth_valid && (cell->status != UT_MAP_SUSPECT || !isfinite(cell->depth_mm) || cell->depth_mm <= 0 || cell->depth_mm >= map->thickness_mm)) return false;
        sampled += cell->count > 0;
        suspect += cell->status == UT_MAP_SUSPECT;
        depth += cell->depth_valid;
        points += cell->count;
        if (points > 2048U) return false;
    }
    return sampled == map->sampled_cells && suspect == map->suspect_cells && depth == map->depth_cells && points == map->point_count;
}

bool ut_map_decode(const char *data, size_t length, ut_map_t *output, char *error, size_t error_capacity)
{
    if (!data || !output || !length || length > UT_MAP_MAX_TEXT || memchr(data, 0, length)) return reject(error, error_capacity, "Invalid map buffer");
    ut_map_t *candidate = calloc(1, sizeof(*candidate));
    if (!candidate) return reject(error, error_capacity, "Map memory allocation failed");
    bool seen[UT_MAP_CAPACITY] = {false};
    bool valid = true;
    size_t offset = 0;
    unsigned line_number = 0, cell_count = 0;
    while (offset < length && valid) {
        char line[180];
        size_t start = offset;
        while (offset < length && data[offset] != '\n') offset++;
        size_t size = offset - start;
        if (offset < length) offset++;
        if (size && data[start + size - 1] == '\r') size--;
        if (!size || size >= sizeof(line)) { valid = false; break; }
        memcpy(line, data + start, size);
        line[size] = 0;
        char *tokens[10];
        unsigned count = fields(line, tokens, 10);
        if (!line_number++) {
            unsigned version;
            valid = count == 10 && !strcmp(tokens[0], "UTMAP") && unsigned_value(tokens[1], 1, &version) && version == 1 &&
                unsigned_value(tokens[2], 32, &candidate->columns) && candidate->columns &&
                unsigned_value(tokens[3], 32, &candidate->rows) && candidate->rows &&
                positive_float(tokens[4], &candidate->length_mm) && positive_float(tokens[5], &candidate->width_mm) &&
                positive_float(tokens[6], &candidate->thickness_mm) && positive_float(tokens[7], &candidate->grid_mm) &&
                (!strcmp(tokens[8], "DEMO") || !strcmp(tokens[8], "RECORDED") || !strcmp(tokens[8], "LIVE")) &&
                !strcmp(tokens[9], "MANUAL_UNVERIFIED");
            if (valid) {
                valid = ceilf(candidate->length_mm / candidate->grid_mm) == (float)candidate->columns &&
                        ceilf(candidate->width_mm / candidate->grid_mm) == (float)candidate->rows;
                snprintf(candidate->source, sizeof(candidate->source), "%s", tokens[8]);
            }
            continue;
        }
        unsigned column, row, samples;
        if (count != 6 || strcmp(tokens[0], "CELL") || !unsigned_value(tokens[1], 31, &column) || !unsigned_value(tokens[2], 31, &row) ||
            column >= candidate->columns || row >= candidate->rows || !unsigned_value(tokens[4], 2048, &samples)) { valid = false; break; }
        unsigned index = row * candidate->columns + column;
        if (seen[index]) { valid = false; break; }
        seen[index] = true;
        cell_count++;
        const char *names[] = {"UNSCANNED", "NO_INDICATION", "SUSPECT", "UNCALIBRATED", "INVALID"};
        unsigned status;
        for (status = 0; status < 5; status++) if (!strcmp(tokens[3], names[status])) break;
        if (status == 5 || ((status == UT_MAP_UNSCANNED) != (samples == 0))) { valid = false; break; }
        ut_map_cell_t *cell = &candidate->cells[index];
        cell->status = (ut_map_status_t)status;
        cell->count = samples;
        cell->depth_valid = tokens[5][0] != 0;
        if (cell->depth_valid && (status != UT_MAP_SUSPECT || !positive_float(tokens[5], &cell->depth_mm) || cell->depth_mm >= candidate->thickness_mm)) { valid = false; break; }
        candidate->point_count += samples;
        candidate->sampled_cells += samples > 0;
        candidate->suspect_cells += status == UT_MAP_SUSPECT;
        candidate->depth_cells += cell->depth_valid;
        if (candidate->point_count > 2048) valid = false;
    }
    valid = valid && line_number > 1 && cell_count == candidate->rows * candidate->columns && ut_map_validate(candidate);
    if (valid) *output = *candidate;
    free(candidate);
    return valid ? true : reject(error, error_capacity, "Invalid, incomplete or inconsistent UTMAP/1 snapshot");
}

bool ut_map_load_csv(const char *path, ut_map_t *output, char *error, size_t error_capacity)
{
    FILE *stream = fopen(path, "rb");
    if (!stream) return reject(error, error_capacity, "Cannot open map snapshot");
    char *data = malloc(UT_MAP_MAX_TEXT + 1);
    if (!data) { fclose(stream); return reject(error, error_capacity, "Map memory allocation failed"); }
    size_t length = fread(data, 1, UT_MAP_MAX_TEXT + 1, stream);
    bool readable = !ferror(stream);
    fclose(stream);
    bool valid = readable ? ut_map_decode(data, length, output, error, error_capacity) :
                            reject(error, error_capacity, "Cannot read map snapshot");
    free(data);
    return valid;
}
