#define _POSIX_C_SOURCE 200809L
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "linux_live.h"

static uint32_t little32(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 | (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
}

static float little_float(const uint8_t *bytes)
{
    uint32_t bits = little32(bytes);
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static bool read_diagnosis(const char *directory, uint32_t sequence, uint64_t timestamp, ut_diagnosis_t *diagnosis)
{
    char path[1024];
    uint8_t data[73];
    struct stat metadata;
    if (snprintf(path, sizeof(path), "%s/live-result.bin", directory) >= (int)sizeof(path)) return false;
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) return false;
    bool regular = fstat(descriptor, &metadata) == 0 && S_ISREG(metadata.st_mode) && metadata.st_size == 72;
    ssize_t received = regular ? read(descriptor, data, sizeof(data)) : -1;
    close(descriptor);
    if (received != 72 || memcmp(data, "UTD1", 4) || little32(data + 4) != 1 || little32(data + 8) != sequence ||
        (little32(data + 24) | (uint64_t)little32(data + 28) << 32) != timestamp) return false;
    uint32_t status = little32(data + 12), flags = little32(data + 16);
    float values[10];
    for (size_t index = 0; index < 10; index++) {
        values[index] = little_float(data + 32 + index * 4);
        if (!isfinite(values[index])) return false;
    }
    if (status > 3 || flags & ~63U || little32(data + 20) > 8 ||
        ((status == UT_DIAG_SUSPECT || status == UT_DIAG_NO_INDICATION) && (flags & 3U) != 3U) ||
        ((flags & 4U) && (!(flags & 1U) || status != UT_DIAG_SUSPECT || values[2] <= 0)) ||
        values[0] < 0 || values[0] > 255 || values[1] < 0 || values[2] < 0 || values[3] < 0 ||
        values[4] < 0 || values[5] < 0 || values[6] < 0 || values[7] < 0 || values[8] < 0 ||
        values[9] < 1000 || values[9] > 20000) return false;
    *diagnosis = (ut_diagnosis_t){.present = true, .calibrated = (flags & 1U) != 0,
        .baseline_valid = (flags & 2U) != 0, .depth_valid = (flags & 4U) != 0,
        .flaw_crossed = (flags & 8U) != 0, .backwall_visible = (flags & 16U) != 0, .clipped = (flags & 32U) != 0,
        .status = (ut_diagnosis_status_t)status, .reason = little32(data + 20),
        .baseline = values[0], .noise = values[1], .depth_mm = values[2],
        .flaw_amplitude = values[3], .flaw_time_us = values[4], .backwall_amplitude = values[5],
        .backwall_time_us = values[6], .reference_thickness_mm = values[7], .time_zero_us = values[8],
        .sound_speed_mps = values[9]};
    return true;
}

bool ut_linux_live_read(const char *directory, uint64_t now_ns, ut_frame_t *frame)
{
    char path[1024];
    uint8_t payload[4137];
    struct stat metadata;
    if (!directory || !frame || snprintf(path, sizeof(path), "%s/live.utc2", directory) >= (int)sizeof(path)) return false;
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) return false;
    bool regular = fstat(descriptor, &metadata) == 0 && S_ISREG(metadata.st_mode) && metadata.st_size == 4136;
    size_t received = 0;
    while (regular && received < sizeof(payload)) {
        ssize_t count = read(descriptor, payload + received, sizeof(payload) - received);
        if (count <= 0) { if (count < 0) regular = false; break; }
        received += (size_t)count;
    }
    close(descriptor);
    if (!regular || received != 4136 || memcmp(payload, "UTC2", 4) || little32(payload + 4) != 2 ||
        little32(payload + 16) != 40 || little32(payload + 20) != 4096 || little32(payload + 24) > 224) return false;
    uint32_t flags = little32(payload + 12), mode = little32(payload + 28);
    uint64_t timestamp = little32(payload + 32) | (uint64_t)little32(payload + 36) << 32;
    if (mode != 3 || flags & ~0x3fffU || flags & 0x39c9U || !(flags & 4U) || timestamp == 0 ||
        timestamp > now_ns || now_ns - timestamp > 500000000ULL) return false;
    memset(frame, 0, sizeof(*frame));
    memcpy(frame->samples, payload + 40, 4096);
    frame->count = 4096;
    frame->sample_period_us = 0.04f;
    frame->gain = (uint8_t)little32(payload + 24);
    frame->overrange = (flags & 0x10U) != 0;
    frame->source = UT_SOURCE_LIVE;
    read_diagnosis(directory, little32(payload + 8), timestamp, &frame->diagnosis);
    snprintf(frame->source_name, sizeof(frame->source_name), "ARM Linux / guarded ADC LIVE / frame %u", little32(payload + 8));
    return true;
}

bool ut_linux_live_command(const char *directory, const char *action, const char *owner, unsigned gain, uint64_t now_ns)
{
    char path[1024], temporary[1040], payload[256];
    if (!directory || !action || !owner || strlen(owner) != 36 || strncmp(owner, "lcd-", 4) ||
        strspn(owner + 4, "0123456789abcdef") != 32 || gain > 224 || now_ns == 0 ||
        snprintf(path, sizeof(path), "%s/live-command.json", directory) >= (int)sizeof(path) ||
        snprintf(temporary, sizeof(temporary), "%s.tmp", path) >= (int)sizeof(temporary)) return false;
    if (!strcmp(action, "start"))
        snprintf(payload, sizeof(payload), "{\"owner\":\"%s\",\"gain\":%u,\"probe_coupled\":true,\"safe_to_pulse\":true}", owner, gain);
    else if (!strcmp(action, "keepalive"))
        snprintf(payload, sizeof(payload), "{\"owner\":\"%s\"}", owner);
    else if (!strcmp(action, "stop"))
        strcpy(payload, "{}");
    else return false;
    int descriptor = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
    FILE *output = descriptor < 0 ? NULL : fdopen(descriptor, "w");
    if (!output) { if (descriptor >= 0) close(descriptor); return false; }
    bool written = fprintf(output, "{\"action\":\"%s\",\"payload\":%s,\"monotonic_ns\":%llu}\n", action, payload,
                           (unsigned long long)now_ns) > 0;
    int closed = fclose(output);
    return written && !closed && rename(temporary, path) == 0;
}
