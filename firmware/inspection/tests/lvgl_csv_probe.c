#include "ut_model.h"
#include <stdio.h>

int main(int argument_count, char **arguments)
{
    ut_frame_t frame;
    char error[256];
    if (argument_count != 2) return 2;
    if (!ut_load_csv(arguments[1], &frame, error, sizeof(error))) {
        fprintf(stderr, "%s\n", error);
        return 3;
    }
    if (frame.count != 4096 || frame.trigger_index != 256 || frame.source != UT_SOURCE_DEMO) return 4;
    if (frame.sample_period_us < 0.03999f || frame.sample_period_us > 0.04001f) return 5;
    printf("LVGL_CSV_COMPATIBLE: 4096 samples; 40ns; source=DEMO\n");
    return 0;
}
