#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "linux_scan.h"

static void store32(uint8_t *data, uint32_t value)
{
    for (unsigned index = 0; index < 4; index++) data[index] = (uint8_t)(value >> (index * 8));
}

static void store_float(uint8_t *data, float value)
{
    uint32_t bits;
    memcpy(&bits, &value, 4);
    store32(data, bits);
}

static void write_data(const char *path, const void *data, size_t length)
{
    FILE *output = fopen(path, "wb");
    assert(output);
    assert(fwrite(data, 1, length, output) == length);
    assert(fclose(output) == 0);
}

int main(void)
{
    char directory[] = "/tmp/ut-local-scan-XXXXXX";
    assert(mkdtemp(directory));
    char path[512];
    snprintf(path, sizeof(path), "%s/scan-status.bin", directory);
    uint8_t data[112] = {0};
    memcpy(data, "UTS1", 4);
    store32(data + 4, 7);
    store32(data + 8, 4000000000U);
    store32(data + 24, 640);
    store32(data + 28, 480);
    store32(data + 40, 256);
    store_float(data + 64, 40.0f);
    store_float(data + 68, 30.0f);
    store_float(data + 76, -1.0f);
    for (unsigned index = 0; index < 8; index++) store_float(data + 80 + index * 4, -1.0f);
    write_data(path, data, sizeof(data));
    ut_local_scan_t state;
    assert(ut_linux_scan_read(directory, 4100000000ULL, &state));
    assert(state.total == 256 && state.position_x == 40 && state.position_y == 30);
    assert(!ut_linux_scan_read(directory, 3999999999ULL, &state));
    assert(!ut_linux_scan_read(directory, 7000000001ULL, &state));
    store_float(data + 64, NAN);
    write_data(path, data, sizeof(data));
    assert(!ut_linux_scan_read(directory, 4100000000ULL, &state));
    store_float(data + 64, 40);
    store32(data + 36, 257);
    write_data(path, data, sizeof(data));
    assert(!ut_linux_scan_read(directory, 4100000000ULL, &state));
    assert(unlink(path) == 0);
    assert(mkfifo(path, 0600) == 0);
    assert(!ut_linux_scan_read(directory, 4100000000ULL, &state));
    assert(unlink(path) == 0);
    assert(symlink("/etc/passwd", path) == 0);
    assert(!ut_linux_scan_read(directory, 4100000000ULL, &state));
    assert(unlink(path) == 0);
    assert(!ut_linux_scan_command(directory, UT_LOCAL_LIVE, 0, 0, 4100000000ULL));
    assert(!ut_linux_scan_command(directory, UT_LOCAL_CORNER, NAN, 0, 4100000000ULL));
    assert(!ut_linux_scan_command(directory, UT_LOCAL_CORNER, 1, 0, 4100000000ULL));
    assert(ut_linux_scan_command(directory, UT_LOCAL_START, 0, 0, 4100000000ULL));
    assert(!ut_linux_scan_command(directory, UT_LOCAL_FINISH, 0, 0, 4100000000ULL));
    snprintf(path, sizeof(path), "%s/scan-command.json", directory);
    FILE *request = fopen(path, "r");
    assert(request);
    char command[512];
    assert(fgets(command, sizeof(command), request));
    fclose(request);
    assert(strstr(command, "scan-start") && strstr(command, "tracking_confirmed\":true"));
    assert(!strstr(command, "safe_to_pulse") && !strstr(command, "gain"));
    assert(unlink(path) == 0);
    assert(ut_linux_scan_command(directory, UT_LOCAL_FINISH, 0, 0, 4100000000ULL));
    assert(unlink(path) == 0);
    assert(rmdir(directory) == 0);
    puts("LOCAL_SCAN_PORT_PASS: bounded fresh status, FIFO/symlink rejection, explicit non-transmitting commands");
    return 0;
}
