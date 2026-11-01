#ifndef UT_ECHO_SETUP_H
#define UT_ECHO_SETUP_H
#include <stdbool.h>
#include <stdint.h>
#define UT_ECHO_PARAMETER_COUNT 11U
typedef struct {
    bool fresh;
    uint32_t flags, phase, reason, collected, target, gain;
    float parameters[UT_ECHO_PARAMETER_COUNT];
    float flaw, wall, noise, proposed_zero, age_ms;
} ut_echo_setup_t;
typedef enum { UT_ECHO_EDIT, UT_ECHO_PREVIEW, UT_ECHO_REFERENCE, UT_ECHO_APPROVE } ut_echo_action_t;
typedef void (*ut_echo_action_cb_t)(ut_echo_action_t action, unsigned parameter, float value);
#endif
