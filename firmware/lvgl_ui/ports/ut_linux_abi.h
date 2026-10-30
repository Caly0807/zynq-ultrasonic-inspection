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
#endif
