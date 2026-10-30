#include "ut_scan.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static const char *header = "point_id,stage_x_mm,world_x_mm,world_y_mm,world_z_mm,source,status,depth_mm,waveform\n";
static void write_fixture(const char *path, const char *row)
{
    FILE *output = fopen(path, "wb");
    assert(output);
    fputs(header, output);
    fputs(row, output);
    assert(fclose(output) == 0);
}

int main(int argument_count, char **arguments)
{
    assert(argument_count == 3);
    ut_scan_t scan;
    char error[160], waveform[512];
    assert(ut_scan_load_csv(arguments[1], &scan, error, sizeof(error)));
    assert(scan.count == 11 && scan.suspect_count == 3 && scan.no_indication_count == 8);
    assert(scan.contains_demo && !scan.contains_recorded);
    assert(scan.minimum_x_mm == 0 && scan.maximum_x_mm == 50);
    assert(scan.points[4].status == UT_SCAN_SUSPECT && scan.points[4].depth_valid);
    assert(fabsf(scan.points[4].depth_mm - 16.52f) < 0.01f);
    assert(ut_scan_waveform_path(arguments[1], &scan.points[4], waveform, sizeof(waveform)));
    assert(strstr(waveform, "waveforms/00004.csv"));
    assert(!ut_scan_waveform_path(arguments[1], &scan.points[4], waveform, 4));
    write_fixture(arguments[2], "p0,0,70,0,0,LIVE,SUSPECT,12,waveforms/00000.csv\n");
    assert(ut_scan_load_csv(arguments[2], &scan, error, sizeof(error)));
    assert(scan.points[0].source == UT_SOURCE_RECORDED && !scan.contains_demo && scan.contains_recorded);
    write_fixture(arguments[2], "\"point,0\",0,70,0,0,LIVE,DEMO_SUSPECT,12,waveforms/00000.csv\n");
    assert(ut_scan_load_csv(arguments[2], &scan, error, sizeof(error)));
    assert(!strcmp(scan.points[0].point_id, "point,0") && scan.points[0].source == UT_SOURCE_DEMO);
    ut_scan_t previous = scan;
    const char *invalid[] = {
        "p0,nan,70,0,0,DEMO,DEMO_SUSPECT,12,waveforms/00000.csv\n",
        "p0,0,70,0,0,LIVE,SUSPECT,inf,waveforms/00000.csv\n",
        "p0,0,70,0,0,LIVE,SUSPECT,-1,waveforms/00000.csv\n",
        "p0,0,70,0,0,LIVE,NO_INDICATION,12,waveforms/00000.csv\n",
        "p0,0,70,0,0,LIVE,HEALTHY,,waveforms/00000.csv\n",
        "p0,0,70,0,0,UNKNOWN,SUSPECT,12,waveforms/00000.csv\n",
        "p0,0,70,0,0,LIVE,SUSPECT,12,../waveforms/00000.csv\n",
        "p0,0,70,0,0,LIVE,SUSPECT,12,waveforms/../00000.csv\n",
        "p0,0,70,0,0,LIVE,SUSPECT,12,D:/00000.csv\n",
        "p0,0,70,0,0,LIVE,SUSPECT,12\n",
        "\"unclosed,0,70,0,0,LIVE,SUSPECT,12,waveforms/00000.csv\n",
        "p0,0,70,0,0,LIVE,SUSPECT,12,waveforms/00000.csv\np0,5,75,0,0,LIVE,SUSPECT,12,waveforms/00001.csv\n"
    };
    for (size_t index = 0; index < sizeof(invalid) / sizeof(invalid[0]); index++) {
        write_fixture(arguments[2], invalid[index]);
        assert(!ut_scan_load_csv(arguments[2], &scan, error, sizeof(error)));
        assert(!memcmp(&scan, &previous, sizeof(scan)));
        assert(error[0]);
    }
    FILE *output = fopen(arguments[2], "wb");
    assert(output);
    fputs(header, output);
    for (size_t index = 0; index <= UT_SCAN_CAPACITY; index++) fprintf(output, "p%zu,%zu,70,0,0,DEMO,DEMO_INVALID,,waveforms/%05zu.csv\n", index, index, index);
    fclose(output);
    assert(!ut_scan_load_csv(arguments[2], &scan, error, sizeof(error)));
    puts("PASS: scan-summary contract, per-point provenance, DEMO retention, depth, strict CSV, duplicate/capacity rejection and transactional loading");
    return 0;
}
