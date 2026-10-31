#ifndef UT_MODEL_H
#define UT_MODEL_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define UT_SAMPLE_CAPACITY 4096

typedef enum { UT_SOURCE_WAITING, UT_SOURCE_RECORDED, UT_SOURCE_LIVE, UT_SOURCE_DEMO } ut_source_t;
typedef enum { UT_DIAG_INVALID, UT_DIAG_UNCALIBRATED, UT_DIAG_SUSPECT, UT_DIAG_NO_INDICATION } ut_diagnosis_status_t;
typedef struct {
    bool present, calibrated, baseline_valid, depth_valid, flaw_crossed, backwall_visible, clipped;
    ut_diagnosis_status_t status;
    uint32_t reason;
    float baseline, noise, depth_mm, flaw_amplitude, flaw_time_us;
    float backwall_amplitude, backwall_time_us, reference_thickness_mm, time_zero_us, sound_speed_mps;
} ut_diagnosis_t;
typedef struct {
    uint8_t samples[UT_SAMPLE_CAPACITY];
    size_t count;
    size_t trigger_index;
    float sample_period_us;
    uint8_t gain;
    bool overrange;
    ut_source_t source;
    char source_name[96];
    ut_diagnosis_t diagnosis;
} ut_frame_t;
typedef struct {
    float gate_a_start_us, gate_a_end_us;
    float gate_b_start_us, gate_b_end_us;
    float threshold_percent, sound_speed_mps;
    bool auto_scale;
} ut_settings_t;
typedef struct {
    unsigned minimum, maximum, peak_to_peak;
    float baseline;
    float gate_a_amplitude, gate_b_amplitude;
    float gate_a_time_us, gate_b_time_us;
    float reference_thickness_mm;
    bool signal_present, gate_a_crossed, gate_b_visible, clipped;
} ut_metrics_t;
void ut_settings_default(ut_settings_t *settings);
void ut_analyze(const ut_frame_t *frame, const ut_settings_t *settings, ut_metrics_t *metrics);
void ut_generate_demo(ut_frame_t *frame, unsigned phase, bool reflector);
bool ut_load_csv(const char *path, ut_frame_t *frame, char *error, size_t error_capacity);
bool ut_save_csv(const char *path, const ut_frame_t *frame);
#endif
