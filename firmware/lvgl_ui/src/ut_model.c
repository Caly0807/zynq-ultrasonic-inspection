#include "ut_model.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void ut_settings_default(ut_settings_t *settings)
{
    *settings = (ut_settings_t){2.5f, 8.0f, 8.5f, 14.0f, 30.0f, 5900.0f, true};
}

void ut_analyze(const ut_frame_t *frame, const ut_settings_t *settings, ut_metrics_t *metrics)
{
    memset(metrics, 0, sizeof(*metrics));
    if (!frame->count || frame->sample_period_us <= 0.0f) return;
    size_t baseline_count = frame->trigger_index > 16 ? frame->trigger_index - 8 : 32;
    if (baseline_count > 256) baseline_count = 256;
    if (baseline_count > frame->count) baseline_count = frame->count;
    for (size_t index = 0; index < baseline_count; index++) metrics->baseline += frame->samples[index];
    metrics->baseline /= (float)baseline_count;
    if (frame->source == UT_SOURCE_LIVE)
        metrics->baseline = frame->diagnosis.present && frame->diagnosis.baseline_valid ? frame->diagnosis.baseline : 128.0f;
    metrics->minimum = 255;
    for (size_t index = 0; index < frame->count; index++) {
        unsigned value = frame->samples[index];
        float time_us = ((float)index - (float)frame->trigger_index) * frame->sample_period_us;
        float amplitude = fabsf((float)value - metrics->baseline);
        if (time_us >= 2.0f) {
            if (value < metrics->minimum) metrics->minimum = value;
            if (value > metrics->maximum) metrics->maximum = value;
            if (value == 0 || value == 255) metrics->clipped = true;
        }
        if (time_us >= settings->gate_a_start_us && time_us <= settings->gate_a_end_us && amplitude > metrics->gate_a_amplitude) {
            metrics->gate_a_amplitude = amplitude;
            metrics->gate_a_time_us = time_us;
        }
        if (time_us >= settings->gate_b_start_us && time_us <= settings->gate_b_end_us && amplitude > metrics->gate_b_amplitude) {
            metrics->gate_b_amplitude = amplitude;
            metrics->gate_b_time_us = time_us;
        }
    }
    if (metrics->minimum > metrics->maximum) metrics->minimum = metrics->maximum;
    metrics->peak_to_peak = metrics->maximum - metrics->minimum;
    metrics->signal_present = metrics->peak_to_peak >= 12;
    metrics->clipped = metrics->clipped || frame->overrange;
    metrics->gate_a_crossed = metrics->signal_present && metrics->gate_a_amplitude >= settings->threshold_percent * 1.28f;
    metrics->gate_b_visible = metrics->signal_present && metrics->gate_b_amplitude >= 6.0f;
    metrics->reference_thickness_mm = metrics->gate_b_time_us * settings->sound_speed_mps / 2000.0f;
    if (frame->source == UT_SOURCE_LIVE) {
        const ut_diagnosis_t *diagnosis = &frame->diagnosis;
        metrics->gate_a_crossed = diagnosis->present && diagnosis->flaw_crossed;
        metrics->gate_b_visible = diagnosis->present && diagnosis->backwall_visible;
        metrics->gate_a_amplitude = diagnosis->flaw_amplitude;
        metrics->gate_a_time_us = diagnosis->flaw_time_us;
        metrics->gate_b_amplitude = diagnosis->backwall_amplitude;
        metrics->gate_b_time_us = diagnosis->backwall_time_us;
        metrics->reference_thickness_mm = diagnosis->reference_thickness_mm;
        metrics->clipped = metrics->clipped || diagnosis->clipped;
    }
}

void ut_generate_demo(ut_frame_t *frame, unsigned phase, bool reflector)
{
    memset(frame, 0, sizeof(*frame));
    frame->count = UT_SAMPLE_CAPACITY;
    frame->trigger_index = 512;
    frame->sample_period_us = 0.02f;
    frame->gain = 0x80;
    frame->source = UT_SOURCE_DEMO;
    snprintf(frame->source_name, sizeof(frame->source_name), "DEMO / synthetic waveform");
    for (size_t index = 0; index < frame->count; index++) {
        float time_us = ((float)index - 512.0f) * 0.02f;
        float value = 128.0f + 0.7f * sinf((float)(index * 17 + phase * 3));
        for (unsigned echo = 0; echo < 5; echo++) {
            float distance = time_us - (float)echo * 10.2f;
            float amplitude = echo == 0 ? 96.0f : 79.0f * powf(0.69f, (float)(echo - 1));
            value += amplitude * expf(-distance * distance / 0.055f) * cosf(distance * 26.0f);
        }
        if (reflector) {
            float distance = time_us - 5.65f;
            value += 53.0f * expf(-distance * distance / 0.040f) * cosf(distance * 27.0f);
        }
        if (value < 0.0f) value = 0.0f;
        if (value > 255.0f) value = 255.0f;
        frame->samples[index] = (uint8_t)(value + 0.5f);
    }
}

static size_t split_csv(char *line, char **columns, size_t capacity)
{
    size_t count = 0;
    char *cursor = line;
    while (count < capacity) {
        columns[count++] = cursor;
        char *separator = strchr(cursor, ',');
        if (!separator) break;
        *separator = '\0';
        cursor = separator + 1;
    }
    return count;
}

bool ut_load_csv(const char *path, ut_frame_t *frame, char *error, size_t error_capacity)
{
    FILE *input = fopen(path, "rb");
    if (!input) { snprintf(error, error_capacity, "Cannot open CSV"); return false; }
    char line[2048];
    char *columns[64];
    int data_column = -1, trigger_column = -1, ina_column = -1, gain_column = -1, otr_column = -1, period_column = -1, source_column = -1;
    int sample_radix = 16;
    if (!fgets(line, sizeof(line), input)) { fclose(input); snprintf(error, error_capacity, "Empty CSV"); return false; }
    size_t header_count = split_csv(line, columns, 64);
    for (size_t column = 0; column < header_count; column++) {
        if (strstr(columns[column], "ad_data")) data_column = (int)column;
        if (!strncmp(columns[column], "TRIGGER", 7)) trigger_column = (int)column;
        if (strstr(columns[column], "ina")) ina_column = (int)column;
        if (strstr(columns[column], "gain")) gain_column = (int)column;
        if (strstr(columns[column], "otr")) otr_column = (int)column;
        if (strstr(columns[column], "Sample Period ns")) period_column = (int)column;
        if (strstr(columns[column], "Source Kind")) source_column = (int)column;
    }
    if (data_column < 0 || !fgets(line, sizeof(line), input)) {
        fclose(input); snprintf(error, error_capacity, "Expected Vivado ILA CSV and radix header"); return false;
    }
    size_t radix_count = split_csv(line, columns, 64);
    if ((size_t)data_column < radix_count && strstr(columns[data_column], "UNSIGNED")) sample_radix = 10;
    if ((size_t)data_column < radix_count && strstr(columns[data_column], "BINARY")) sample_radix = 2;
    memset(frame, 0, sizeof(*frame));
    frame->sample_period_us = 0.02f;
    frame->source = UT_SOURCE_RECORDED;
    frame->gain = 0x80;
    bool trigger_found = false, previous_ina = false;
    while (fgets(line, sizeof(line), input) && frame->count < UT_SAMPLE_CAPACITY) {
        size_t column_count = split_csv(line, columns, 64);
        if (column_count < header_count) { fclose(input); snprintf(error, error_capacity, "Incomplete CSV frame"); return false; }
        char *end_pointer;
        unsigned long value = strtoul(columns[data_column], &end_pointer, sample_radix);
        if (end_pointer == columns[data_column] || value > 255 || (*end_pointer && *end_pointer != '\r' && *end_pointer != '\n')) {
            fclose(input); snprintf(error, error_capacity, "Invalid ADC code in CSV"); return false;
        }
        bool triggered = trigger_column >= 0 && strtoul(columns[trigger_column], NULL, 10) != 0;
        bool ina_active = ina_column >= 0 && strtoul(columns[ina_column], NULL, 16) != 0;
        if (!trigger_found && (triggered || (ina_active && !previous_ina))) { frame->trigger_index = frame->count; trigger_found = true; }
        previous_ina = ina_active;
        if (period_column >= 0) {
            float period_ns = strtof(columns[period_column], NULL);
            if (!(period_ns > 0.0f && period_ns <= 10000.0f)) { fclose(input); snprintf(error, error_capacity, "Invalid sample period" ); return false; }
            frame->sample_period_us = period_ns / 1000.0f;
        }
        if (source_column >= 0 && !strncmp(columns[source_column], "DEMO", 4)) frame->source = UT_SOURCE_DEMO;
        if (gain_column >= 0) frame->gain = (uint8_t)strtoul(columns[gain_column], NULL, 16);
        if (otr_column >= 0 && strtoul(columns[otr_column], NULL, 16) != 0) frame->overrange = true;
        frame->samples[frame->count++] = (uint8_t)value;
    }
    fclose(input);
    if (frame->count != UT_SAMPLE_CAPACITY || !trigger_found) { snprintf(error, error_capacity, "CSV needs a complete 4096-sample frame and a trigger marker"); return false; }
    const char *filename = strrchr(path, '\\');
    if (!filename) filename = strrchr(path, '/');
    snprintf(frame->source_name, sizeof(frame->source_name), "%s", filename ? filename + 1 : path);
    return true;
}

bool ut_save_csv(const char *path, const ut_frame_t *frame)
{
    FILE *output = fopen(path, "wb");
    if (!output) return false;
    fprintf(output, "Sample in Buffer,Sample in Window,TRIGGER,ad_data_r[7:0],ad_otr_r,md1213_ina_OBUF,gain_val[7:0],Sample Period ns,Source Kind\n");
    fprintf(output, "Radix - UNSIGNED,UNSIGNED,UNSIGNED,HEX,HEX,HEX,HEX,UNSIGNED,ASCII\n");
    for (size_t index = 0; index < frame->count; index++) {
        fprintf(output, "%zu,%zu,%u,%02X,%u,%u,%02X,%.3f,%s\n", index, index, index == frame->trigger_index,
                frame->samples[index], frame->overrange ? 1 : 0, index == frame->trigger_index, frame->gain,
                (double)(frame->sample_period_us * 1000.0f), frame->source == UT_SOURCE_DEMO ? "DEMO" : "RECORDED" );
    }
    return fclose(output) == 0;
}
