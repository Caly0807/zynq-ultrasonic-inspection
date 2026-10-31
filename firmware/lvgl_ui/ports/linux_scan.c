#define _POSIX_C_SOURCE 200809L
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "linux_scan.h"

static uint32_t integer32(const uint8_t *data)
{
    return (uint32_t)data[0] | (uint32_t)data[1] << 8 | (uint32_t)data[2] << 16 | (uint32_t)data[3] << 24;
}

static uint64_t integer64(const uint8_t *data)
{
    return integer32(data) | (uint64_t)integer32(data + 4) << 32;
}

static float number32(const uint8_t *data)
{
    uint32_t bits = integer32(data);
    float result;
    memcpy(&result, &bits, sizeof(result));
    return result;
}

static FILE *open_file(const char *directory, const char *name, size_t length)
{
    char path[1024];
    if (!directory || snprintf(path, sizeof(path), "%s/%s", directory, name) >= (int)sizeof(path)) return NULL;
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    struct stat information;
    if (descriptor < 0) return NULL;
    if (fstat(descriptor, &information) || !S_ISREG(information.st_mode) || information.st_size != (off_t)length) {
        close(descriptor);
        return NULL;
    }
    FILE *source = fdopen(descriptor, "rb");
    if (!source) close(descriptor);
    return source;
}

static bool fresh(uint64_t timestamp, uint64_t now)
{
    return timestamp && timestamp <= now && now - timestamp <= 3000000000ULL;
}

bool ut_linux_scan_read(const char *directory, uint64_t now, ut_local_scan_t *state)
{
    uint8_t data[112];
    FILE *source = open_file(directory, "scan-status.bin", sizeof(data));
    if (!source || !state) { if (source) fclose(source); return false; }
    bool valid = fread(data, 1, sizeof(data), source) == sizeof(data) && fgetc(source) == EOF;
    fclose(source);
    if (!valid || memcmp(data, "UTS1", 4) || !fresh(integer64(data + 8), now)) return false;
    ut_local_scan_t incoming = {0};
    incoming.flags = integer32(data + 4);
    incoming.phase = integer32(data + 16);
    incoming.reason = integer32(data + 20);
    incoming.width = integer32(data + 24);
    incoming.height = integer32(data + 28);
    incoming.corner_count = integer32(data + 32);
    incoming.sampled = integer32(data + 36);
    incoming.total = integer32(data + 40);
    incoming.valid = integer32(data + 44);
    incoming.suspect = integer32(data + 48);
    incoming.depths = integer32(data + 52);
    incoming.sequence = integer32(data + 56);
    incoming.position_x = number32(data + 64);
    incoming.position_y = number32(data + 68);
    incoming.coverage = number32(data + 72);
    incoming.skew_ms = number32(data + 76);
    for (unsigned index = 0; index < 8; index++) {
        incoming.corners[index] = number32(data + 80 + index * 4);
        if (!isfinite(incoming.corners[index]) || incoming.corners[index] < -1 || incoming.corners[index] >= 1) return false;
    }
    if (incoming.flags > 63 || incoming.phase > 2 || incoming.reason > 14 || incoming.corner_count > 4 ||
        !incoming.total || incoming.total > 1024 || incoming.sampled > incoming.total || incoming.valid > incoming.sampled ||
        incoming.suspect > incoming.sampled || incoming.depths > 2048 ||
        !isfinite(incoming.coverage) || incoming.coverage < 0 || incoming.coverage > 100 ||
        !isfinite(incoming.skew_ms) || incoming.skew_ms < -1 || incoming.skew_ms > 100000 ||
        !isfinite(incoming.position_x) || !isfinite(incoming.position_y) ||
        incoming.position_x < -1 || incoming.position_y < -1 || incoming.position_x > 10000 || incoming.position_y > 10000) return false;
    if ((incoming.flags & 1U) && (incoming.width < 160 || incoming.width > 1280 || incoming.height < 120 || incoming.height > 720)) return false;
    *state = incoming;
    return true;
}

bool ut_linux_scan_preview(const char *directory, uint64_t now, uint8_t *pixels, size_t capacity)
{
    if (!pixels || capacity < UT_LOCAL_PREVIEW_BYTES) return false;
    uint8_t header[16];
    FILE *source = open_file(directory, "scan-preview.bin", sizeof(header) + UT_LOCAL_PREVIEW_BYTES);
    if (!source) return false;
    bool valid = fread(header, 1, sizeof(header), source) == sizeof(header) && !memcmp(header, "UTP1", 4) &&
                 integer32(header + 4) == UT_LOCAL_PREVIEW_BYTES && fresh(integer64(header + 8), now);
    valid = valid && fread(pixels, 1, UT_LOCAL_PREVIEW_BYTES, source) == UT_LOCAL_PREVIEW_BYTES && fgetc(source) == EOF;
    fclose(source);
    return valid;
}

bool ut_linux_scan_command(const char *directory, ut_local_action_t action, float position_x, float position_y, uint64_t now)
{
    char path[1024], temporary[1040], payload[192];
    const char *name;
    if (!directory || !now || snprintf(path, sizeof(path), "%s/scan-command.json", directory) >= (int)sizeof(path) ||
        snprintf(temporary, sizeof(temporary), "%s.tmp", path) >= (int)sizeof(temporary) || access(path, F_OK) == 0) return false;
    if (action == UT_LOCAL_FREEZE) { name = "freeze"; strcpy(payload, "{}"); }
    else if (action == UT_LOCAL_CORNER) {
        if (!isfinite(position_x) || !isfinite(position_y) || position_x < 0 || position_y < 0 || position_x >= 1 || position_y >= 1) return false;
        name = "corner";
        snprintf(payload, sizeof(payload), "{\"x\":%.8f,\"y\":%.8f}", (double)position_x, (double)position_y);
    } else if (action == UT_LOCAL_CALIBRATE) { name = "calibrate"; strcpy(payload, "{\"fixed_confirmed\":true}"); }
    else if (action == UT_LOCAL_START) {
        name = "scan-start";
        strcpy(payload, "{\"tracking_confirmed\":true,\"normal_incidence_confirmed\":true}");
    } else if (action == UT_LOCAL_FINISH) { name = "scan-finish"; strcpy(payload, "{}"); }
    else if (action == UT_LOCAL_NEW) { name = "new-scan"; strcpy(payload, "{\"archive_confirmed\":true}"); }
    else return false;
    int descriptor = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (descriptor < 0) return false;
    FILE *destination = fdopen(descriptor, "w");
    if (!destination) { close(descriptor); return false; }
    bool written = fprintf(destination, "{\"action\":\"%s\",\"payload\":%s,\"monotonic_ns\":%llu}\n", name, payload,
                           (unsigned long long)now) > 0;
    int closed = fclose(destination);
    return written && !closed && rename(temporary, path) == 0;
}
