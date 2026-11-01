#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "linux_echo.h"

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
    char directory[] = "/tmp/ut-echo-setup-XXXXXX", path[512];
    assert(mkdtemp(directory));
    snprintf(path, sizeof(path), "%s/echo-status.bin", directory);
    uint8_t data[101] = {0};
    memcpy(data, "UTE1", 4);
    store32(data + 8, 4000000000U);
    store32(data + 28, 8);
    const float parameters[] = {2.5f, 8, 8.5f, 12, 100, 110, 35, 12, 5900, 0, 30};
    for (unsigned index = 0; index < UT_ECHO_PARAMETER_COUNT; index++) store_float(data + 36 + index * 4, parameters[index]);
    store_float(data + 96, -1);
    write_data(path, data, 100);
    ut_echo_setup_t state = {0};
    assert(ut_linux_echo_read(directory, 4100000000ULL, &state));
    assert(state.fresh && state.target == 8 && state.parameters[4] == 100 && state.flags == 0);
    assert(!ut_linux_echo_read(directory, 3999999999ULL, &state));
    assert(!ut_linux_echo_read(directory, 7000000001ULL, &state));
    write_data(path, data, 101);
    assert(!ut_linux_echo_read(directory, 4100000000ULL, &state));
    store_float(data + 36, NAN);
    write_data(path, data, 100);
    assert(!ut_linux_echo_read(directory, 4100000000ULL, &state));
    store_float(data + 36, 2.5f);
    store32(data + 24, 9);
    write_data(path, data, 100);
    assert(!ut_linux_echo_read(directory, 4100000000ULL, &state));
    assert(unlink(path) == 0 && mkfifo(path, 0600) == 0);
    assert(!ut_linux_echo_read(directory, 4100000000ULL, &state));
    assert(unlink(path) == 0 && symlink("/etc/passwd", path) == 0);
    assert(!ut_linux_echo_read(directory, 4100000000ULL, &state));
    assert(unlink(path) == 0);
    assert(!ut_linux_echo_command(directory, UT_ECHO_EDIT, 11, 40, 4100000000ULL));
    assert(!ut_linux_echo_command(directory, UT_ECHO_EDIT, 0, NAN, 4100000000ULL));
    assert(!ut_linux_echo_command(directory, UT_ECHO_EDIT, 0, 100, 4100000000ULL));
    assert(!ut_linux_echo_command(directory, UT_ECHO_PREVIEW, 0, 0, 0));
    const char *names[] = {"echo-set", "echo-preview", "echo-reference", "echo-approve"};
    snprintf(path, sizeof(path), "%s/scan-command.json", directory);
    for (unsigned index = 0; index < 4; index++) {
        assert(ut_linux_echo_command(directory, (ut_echo_action_t)index, 0, 3, 4100000000ULL));
        assert(!ut_linux_echo_command(directory, UT_ECHO_PREVIEW, 0, 0, 4100000000ULL));
        FILE *input = fopen(path, "r");
        assert(input);
        char command[512];
        assert(fgets(command, sizeof(command), input));
        assert(fclose(input) == 0);
        assert(strstr(command, names[index]));
        assert(!strstr(command, "start") && !strstr(command, "safe_to_pulse") && !strstr(command, "gain"));
        assert(unlink(path) == 0);
    }
    snprintf(path, sizeof(path), "%s/scan-command.json.tmp", directory);
    assert(symlink("/etc/passwd", path) == 0);
    assert(!ut_linux_echo_command(directory, UT_ECHO_PREVIEW, 0, 0, 4100000000ULL));
    assert(unlink(path) == 0 && rmdir(directory) == 0);
    puts("ECHO_SETUP_PORT_PASS: fresh bounded 100-byte status, unsafe files rejected, no transmitting commands");
    return 0;
}
