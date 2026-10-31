#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "linux_live.h"

static void put32(uint8_t *bytes, uint32_t value)
{
    for (unsigned index = 0; index < 4; index++) bytes[index] = (uint8_t)(value >> (index * 8));
}

static void save(const char *path, const uint8_t *payload, size_t length)
{
    FILE *output = fopen(path, "wb");
    assert(output && fwrite(payload, 1, length, output) == length);
    assert(fclose(output) == 0);
}

static void put_float(uint8_t *bytes, float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    put32(bytes, bits);
}

int main(void)
{
    char directory[] = "/tmp/ut-live-test-XXXXXX", path[256];
    uint8_t payload[4137] = {0};
    ut_frame_t frame;
    assert(mkdtemp(directory));
    snprintf(path, sizeof(path), "%s/live.utc2", directory);
    memcpy(payload, "UTC2", 4);
    put32(payload + 4, 2);
    put32(payload + 8, 17);
    put32(payload + 12, 0x204);
    put32(payload + 16, 40);
    put32(payload + 20, 4096);
    put32(payload + 24, 128);
    put32(payload + 28, 3);
    put32(payload + 32, 1000000000);
    memset(payload + 40, 190, 4096);
    save(path, payload, 4136);
    assert(ut_linux_live_read(directory, 1100000000ULL, &frame));
    assert(frame.source == UT_SOURCE_LIVE && frame.count == 4096 && frame.samples[4095] == 190 && frame.gain == 128);
    assert(!frame.diagnosis.present);
    char result_path[256];
    uint8_t result[72] = {0};
    snprintf(result_path, sizeof(result_path), "%s/live-result.bin", directory);
    memcpy(result, "UTD1", 4);
    put32(result + 4, 1);
    put32(result + 8, 17);
    put32(result + 20, 1);
    put32(result + 24, 1000000000);
    put_float(result + 32, 128);
    put_float(result + 68, 5900);
    save(result_path, result, sizeof(result));
    assert(ut_linux_live_read(directory, 1100000000ULL, &frame) && frame.diagnosis.present);
    assert(frame.diagnosis.status == UT_DIAG_INVALID && !frame.diagnosis.calibrated && frame.diagnosis.reason == 1);
    put32(result + 8, 18);
    save(result_path, result, sizeof(result));
    assert(ut_linux_live_read(directory, 1100000000ULL, &frame) && !frame.diagnosis.present);
    put32(result + 8, 17);
    put32(result + 12, UT_DIAG_SUSPECT);
    save(result_path, result, sizeof(result));
    assert(ut_linux_live_read(directory, 1100000000ULL, &frame) && !frame.diagnosis.present);
    assert(!ut_linux_live_read(directory, 999999999ULL, &frame));
    assert(!ut_linux_live_read(directory, 1500000001ULL, &frame));
    for (unsigned flag = 0; flag < 32; flag++) {
        uint32_t mask = 1U << flag;
        if (!(mask & 0xfffff9c9U)) continue;
        put32(payload + 12, 0x204 | mask);
        save(path, payload, 4136);
        assert(!ut_linux_live_read(directory, 1100000000ULL, &frame));
    }
    put32(payload + 12, 0x204);
    put32(payload + 28, 1);
    save(path, payload, 4136);
    assert(!ut_linux_live_read(directory, 1100000000ULL, &frame));
    put32(payload + 28, 3);
    save(path, payload, 4137);
    assert(!ut_linux_live_read(directory, 1100000000ULL, &frame));
    assert(!ut_linux_live_command(directory, "start", "pc-invalid", 128, 1));
    assert(!ut_linux_live_command(directory, "start", "lcd-11111111111111111111111111111111", 225, 1));
    assert(ut_linux_live_command(directory, "start", "lcd-11111111111111111111111111111111", 128, 1100000000));
    snprintf(path, sizeof(path), "%s/live-command.json", directory);
    FILE *command = fopen(path, "r");
    char text[1024];
    assert(command && fgets(text, sizeof(text), command));
    assert(fclose(command) == 0);
    assert(strstr(text, "\"safe_to_pulse\":true") && strstr(text, "\"monotonic_ns\":1100000000"));
    assert(unlink(path) == 0);
    snprintf(path, sizeof(path), "%s/live.utc2", directory);
    assert(unlink(path) == 0 && unlink(result_path) == 0 && rmdir(directory) == 0);
    puts("LINUX_LIVE_UI_PASS: fresh binary ADC only, no TEST/replay/unsafe flags, command validation");
    return 0;
}
