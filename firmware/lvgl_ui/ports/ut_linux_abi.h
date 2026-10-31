#ifndef UT_LINUX_ABI_H
#define UT_LINUX_ABI_H
#include <linux/types.h>
#include <linux/ioctl.h>
#define UT_BOARD_MAGIC 0x314c5455U
#define UT_CAPTURE_MAGIC 0x31435455U
#define UT_LCD_WIDTH 800U
#define UT_LCD_HEIGHT 480U
#define UT_LCD_STRIDE 2400U
#define UT_LCD_BYTES (UT_LCD_STRIDE * UT_LCD_HEIGHT)
#define UT_CAPTURE_SAMPLES 4096U
#define UT_PIXEL_GRB888 1U
#define UT_LIVE_MAGIC 0x32435455U
#define UT_LIVE_CAPS_MAGIC 0x32564c55U
#define UT_CAPTURE_TEST 1U
#define UT_CAPTURE_PASSIVE 2U
#define UT_CAPTURE_PULSED 3U
struct ut_live_caps {
    __u32 magic, version, modes, samples, sample_period_ns, max_gain, lease_us, reserved;
};
struct ut_live_frame {
    __le32 magic, version, sequence, status, sample_period_ns, sample_count, gain, mode;
    __le64 monotonic_ns;
    __u8 samples[UT_CAPTURE_SAMPLES];
};
struct ut_board_status {
    __u32 magic, version, width, height, stride, pixel_format, capture_status, sequence;
};
struct ut_capture_header {
    __le32 magic, version, sequence, status, sample_period_ns, sample_count;
    __le64 monotonic_ns;
};
#define UT_BOARD_STATUS _IOR('U', 0x10, struct ut_board_status)
#define UT_DISPLAY_START _IO('U', 0x11)
#define UT_DISPLAY_STOP _IO('U', 0x12)
#define UT_LIVE_CAPS _IOR('U', 0x20, struct ut_live_caps)
#define UT_CAPTURE_ONCE _IOWR('U', 0x21, struct ut_live_frame)
#endif
