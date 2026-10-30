#include "ut_model.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

int main(int argument_count, char **arguments)
{
    assert(argument_count == 3);
    ut_settings_t settings;
    ut_settings_default(&settings);
    ut_frame_t frame, restored;
    ut_metrics_t metrics;
    char error[160];
    ut_generate_demo(&frame, 0, false);
    ut_analyze(&frame, &settings, &metrics);
    assert(frame.source == UT_SOURCE_DEMO);
    assert(metrics.signal_present && metrics.gate_b_visible && !metrics.gate_a_crossed);
    assert(fabsf(metrics.reference_thickness_mm - 30.09f) < 0.2f);
    ut_generate_demo(&frame, 0, true);
    ut_analyze(&frame, &settings, &metrics);
    assert(metrics.gate_a_crossed);
    assert(ut_save_csv(arguments[2], &frame));
    assert(ut_load_csv(arguments[2], &restored, error, sizeof(error)));
    assert(restored.source == UT_SOURCE_DEMO);
    assert(fabsf(restored.sample_period_us - frame.sample_period_us) < 0.00001f);
    assert(restored.trigger_index == 512 && restored.count == UT_SAMPLE_CAPACITY);
    assert(!memcmp(restored.samples, frame.samples, UT_SAMPLE_CAPACITY));
    frame.sample_period_us = 0.04f;
    frame.trigger_index = 0;
    assert(ut_save_csv(arguments[2], &frame));
    assert(ut_load_csv(arguments[2], &restored, error, sizeof(error)));
    assert(fabsf(restored.sample_period_us - 0.04f) < 0.00001f);
    assert(restored.trigger_index == 0 && restored.source == UT_SOURCE_DEMO);
    memset(frame.samples, 128, sizeof(frame.samples));
    frame.overrange = false;
    ut_analyze(&frame, &settings, &metrics);
    assert(!metrics.signal_present && !metrics.gate_b_visible && !metrics.gate_a_crossed);
    frame.overrange = true;
    ut_analyze(&frame, &settings, &metrics);
    assert(metrics.clipped);
    assert(ut_load_csv(arguments[1], &restored, error, sizeof(error)));
    assert(restored.count == 4096 && restored.trigger_index == 512);
    assert(restored.source == UT_SOURCE_RECORDED && fabsf(restored.sample_period_us - 0.02f) < 0.00001f);
    ut_analyze(&restored, &settings, &metrics);
    assert(!metrics.signal_present);
    FILE *truncated = fopen(arguments[2], "wb");
    assert(truncated);
    fprintf(truncated, "Sample in Buffer,TRIGGER,ad_data_r[7:0]\nUNSIGNED,UNSIGNED,HEX\n0,1,80\n");
    fclose(truncated);
    assert(!ut_load_csv(arguments[2], &restored, error, sizeof(error)));
    remove(arguments[2]);
    puts("PASS: no-signal, gate detection, thickness units, clipping, real CSV, incomplete writes, DEMO provenance, 20/40ns round trips");
    return 0;
}