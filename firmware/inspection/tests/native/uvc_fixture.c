#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int camera_descriptor = -1;
static unsigned sequence;

int open(const char *path, int flags, ...)
{
    int (*real_open)(const char *, int, ...) = dlsym(RTLD_NEXT, "open");
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list arguments;
        va_start(arguments, flags);
        mode = va_arg(arguments, mode_t);
        va_end(arguments);
    }
    if (!strcmp(path, "/dev/ut-uvc-fixture")) {
        camera_descriptor = real_open("/dev/zero", O_RDWR | O_CLOEXEC);
        return camera_descriptor;
    }
    return real_open(path, flags, mode);
}

int ioctl(int descriptor, unsigned long request, ...)
{
    va_list arguments;
    va_start(arguments, request);
    void *value = va_arg(arguments, void *);
    va_end(arguments);
    if (descriptor != camera_descriptor) {
        int (*real_ioctl)(int, unsigned long, ...) = dlsym(RTLD_NEXT, "ioctl");
        return real_ioctl(descriptor, request, value);
    }
    const char *scenario = getenv("UT_UVC_FIXTURE");
    if (request == VIDIOC_QUERYCAP) {
        ((struct v4l2_capability *)value)->capabilities = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING;
    } else if (request == VIDIOC_ENUM_FMT) {
        struct v4l2_fmtdesc *format = value;
        if (format->index > 1) { errno = EINVAL; return -1; }
        format->pixelformat = format->index ? V4L2_PIX_FMT_MJPEG : V4L2_PIX_FMT_YUYV;
    } else if (request == VIDIOC_S_FMT) {
        struct v4l2_pix_format *format = &((struct v4l2_format *)value)->fmt.pix;
        format->width = 160;
        format->height = 120;
        format->bytesperline = 320;
        format->pixelformat = !strcmp(scenario, "mjpeg") ? V4L2_PIX_FMT_MJPEG : V4L2_PIX_FMT_YUYV;
    } else if (request == VIDIOC_QUERYBUF) {
        ((struct v4l2_buffer *)value)->length = 38400;
        ((struct v4l2_buffer *)value)->m.offset = 0;
    } else if (request == VIDIOC_DQBUF) {
        struct v4l2_buffer *buffer = value;
        buffer->sequence = ++sequence;
        buffer->index = !strcmp(scenario, "bounds") ? 99 : 0;
        buffer->bytesused = sequence == 2 && !strcmp(scenario, "recover") ? 2 : 38400;
        if (!strcmp(scenario, "badframes") || (sequence == 1 && !strcmp(scenario, "recover")))
            buffer->flags = V4L2_BUF_FLAG_ERROR;
    } else if (request != VIDIOC_S_PARM && request != VIDIOC_REQBUFS && request != VIDIOC_QBUF &&
               request != VIDIOC_STREAMON && request != VIDIOC_STREAMOFF) {
        errno = EINVAL;
        return -1;
    }
    return 0;
}

int poll(struct pollfd *descriptors, nfds_t count, int timeout)
{
    if (count == 1 && descriptors[0].fd == camera_descriptor) {
        if (sequence >= 3 && !strcmp(getenv("UT_UVC_FIXTURE"), "recover")) {
            raise(SIGTERM);
            errno = EINTR;
            return -1;
        }
        descriptors[0].revents = POLLIN;
        return 1;
    }
    int (*real_poll)(struct pollfd *, nfds_t, int) = dlsym(RTLD_NEXT, "poll");
    return real_poll(descriptors, count, timeout);
}
