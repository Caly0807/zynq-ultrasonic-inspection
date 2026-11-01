#define _POSIX_C_SOURCE 200809L
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "linux_echo.h"

static const float minimums[UT_ECHO_PARAMETER_COUNT] = {0, .04f, .04f, .08f, 0, .04f, 1, 1, 1000, 0, 1};
static const float maximums[UT_ECHO_PARAMETER_COUNT] = {80, 100, 140, 160, 155, 163, 120, 120, 10000, 80, 100};

static uint32_t integer32(const uint8_t *data)
{
    return (uint32_t)data[0] | (uint32_t)data[1] << 8 | (uint32_t)data[2] << 16 | (uint32_t)data[3] << 24;
}

static float number32(const uint8_t *data)
{
    uint32_t bits = integer32(data);
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

bool ut_linux_echo_read(const char *directory, uint64_t now, ut_echo_setup_t *state)
{
    char path[1024];
    uint8_t data[101];
    struct stat information;
    if (!directory || !state || snprintf(path, sizeof(path), "%s/echo-status.bin", directory) >= (int)sizeof(path)) return false;
    int descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (descriptor < 0) return false;
    bool regular = !fstat(descriptor, &information) && S_ISREG(information.st_mode) && information.st_size == 100;
    ssize_t received = regular ? read(descriptor, data, sizeof(data)) : -1;
    close(descriptor);
    if (received != 100 || memcmp(data, "UTE1", 4)) return false;
    uint64_t timestamp = integer32(data + 8) | (uint64_t)integer32(data + 12) << 32;
    if (!timestamp || timestamp > now || now - timestamp > 3000000000ULL) return false;
    ut_echo_setup_t incoming = {.fresh = true, .flags = integer32(data + 4), .phase = integer32(data + 16),
        .reason = integer32(data + 20), .collected = integer32(data + 24), .target = integer32(data + 28),
        .gain = integer32(data + 32)};
    if (incoming.flags > 7 || incoming.phase > 5 || incoming.reason > 13 || incoming.target != 8 || incoming.collected > 8 || incoming.gain > 224) return false;
    for (unsigned index = 0; index < UT_ECHO_PARAMETER_COUNT; index++) {
        incoming.parameters[index] = number32(data + 36 + index * 4);
        if (!isfinite(incoming.parameters[index]) || incoming.parameters[index] < minimums[index] || incoming.parameters[index] > maximums[index]) return false;
    }
    incoming.flaw = number32(data + 80);
    incoming.wall = number32(data + 84);
    incoming.noise = number32(data + 88);
    incoming.proposed_zero = number32(data + 92);
    incoming.age_ms = number32(data + 96);
    if (!isfinite(incoming.flaw) || incoming.flaw < 0 || incoming.flaw > 255 ||
        !isfinite(incoming.wall) || incoming.wall < 0 || incoming.wall > 255 ||
        !isfinite(incoming.noise) || incoming.noise < 0 || incoming.noise > 255 ||
        !isfinite(incoming.proposed_zero) || incoming.proposed_zero < -200 || incoming.proposed_zero > 200 ||
        !isfinite(incoming.age_ms) || incoming.age_ms < -1 || incoming.age_ms > 1000000) return false;
    *state = incoming;
    return true;
}

bool ut_linux_echo_command(const char *directory, ut_echo_action_t action, unsigned parameter, float value, uint64_t now)
{
    char path[1024], temporary[1040], payload[160];
    const char *name;
    if (!directory || !now || snprintf(path, sizeof(path), "%s/scan-command.json", directory) >= (int)sizeof(path) ||
        snprintf(temporary, sizeof(temporary), "%s.tmp", path) >= (int)sizeof(temporary) || !access(path, F_OK)) return false;
    if (action == UT_ECHO_EDIT) {
        if (parameter >= UT_ECHO_PARAMETER_COUNT || !isfinite(value) || value < minimums[parameter] || value > maximums[parameter]) return false;
        name = "echo-set";
        snprintf(payload, sizeof(payload), "{\"parameter\":%u,\"value\":%.6f}", parameter, value);
    } else if (action == UT_ECHO_PREVIEW) {
        name = "echo-preview";
        strcpy(payload, "{}");
    } else if (action == UT_ECHO_REFERENCE) {
        name = "echo-reference";
        strcpy(payload, "{\"reference_confirmed\":true,\"normal_incidence_confirmed\":true}");
    } else if (action == UT_ECHO_APPROVE) {
        name = "echo-approve";
        strcpy(payload, "{\"review_confirmed\":true}");
    } else return false;
    int descriptor = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0600);
    if (descriptor < 0) return false;
    struct stat information;
    if (fstat(descriptor, &information) || !S_ISREG(information.st_mode)) { close(descriptor); return false; }
    FILE *output = fdopen(descriptor, "w");
    if (!output) { close(descriptor); return false; }
    bool written = fprintf(output, "{\"action\":\"%s\",\"payload\":%s,\"monotonic_ns\":%llu}\n", name, payload, (unsigned long long)now) > 0;
    int closed = fclose(output);
    return written && !closed && rename(temporary, path) == 0;
}
