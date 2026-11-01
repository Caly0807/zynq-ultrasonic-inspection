#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <time.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
#include "ut_red_marker.h"

static volatile sig_atomic_t running = 1;
static void stop_capture(int signal_number) { (void)signal_number; running = 0; }
static int video_ioctl(int descriptor, unsigned long request, void *argument)
{
    int result;
    do result = ioctl(descriptor, request, argument); while (result < 0 && errno == EINTR && running);
    return result;
}
static uint8_t clipped(int value) { return value < 0 ? 0 : value > 255 ? 255 : (uint8_t)value; }
static uint64_t monotonic_ns(void)
{
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (uint64_t)value.tv_sec * 1000000000ULL + (uint64_t)value.tv_nsec;
}

struct camera_status {
    const char *state, *stage;
    int error_number;
    unsigned width, height, stride, pixel_format, sequence, fps_numerator, fps_denominator;
    bool supports_yuyv, supports_mjpeg;
    uint64_t published_frames, dropped_frames, last_frame_ns;
};

static void capture_error(struct camera_status *status, const char *stage, int error_number)
{
    status->state = "FAILED";
    status->stage = stage;
    status->error_number = error_number;
    fprintf(stderr, "UVC %s: %s\n", stage, strerror(error_number));
}

static void publish_status(const char *directory, const struct camera_status *status)
{
    char temporary[1024], destination[1024];
    if (snprintf(temporary, sizeof(temporary), "%s/status.json.tmp", directory) >= (int)sizeof(temporary) ||
        snprintf(destination, sizeof(destination), "%s/status.json", directory) >= (int)sizeof(destination)) return;
    int descriptor = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
    FILE *output = descriptor < 0 ? NULL : fdopen(descriptor, "w");
    if (!output) { if (descriptor >= 0) close(descriptor); return; }
    int written = fprintf(output,
        "{\"schema\":\"ut-uvc-status/1\",\"state\":\"%s\",\"stage\":\"%s\",\"errno\":%d,"
        "\"width\":%u,\"height\":%u,\"stride\":%u,\"pixel_format\":%u,\"sequence\":%u,"
        "\"fps_numerator\":%u,\"fps_denominator\":%u,\"supports_yuyv\":%s,\"supports_mjpeg\":%s,"
        "\"published_frames\":%llu,\"dropped_frames\":%llu,\"last_frame_ns\":%llu,\"updated_monotonic_ns\":%llu}\n",
        status->state, status->stage, status->error_number, status->width, status->height,
        status->stride, status->pixel_format, status->sequence, status->fps_numerator, status->fps_denominator,
        status->supports_yuyv ? "true" : "false", status->supports_mjpeg ? "true" : "false",
        (unsigned long long)status->published_frames, (unsigned long long)status->dropped_frames,
        (unsigned long long)status->last_frame_ns, (unsigned long long)monotonic_ns());
    int closed = fclose(output);
    if (written > 0 && !closed) rename(temporary, destination);
}

int main(int count, char **arguments)
{
    if (count != 3) { fprintf(stderr, "Usage: ut_uvc_capture /dev/videoN PRIVATE_OUTPUT_DIRECTORY\n"); return 2; }
    int descriptor = -1, result = 1;
    void *buffers[4] = {0};
    size_t lengths[4] = {0};
    uint8_t *rgb = NULL;
    char temporary[1024], destination[1024];
    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    bool streaming = false;
    struct camera_status status = {.state = "STARTING", .stage = "OPEN"};
    if (snprintf(temporary, sizeof(temporary), "%s/frame.ppm.tmp", arguments[2]) >= (int)sizeof(temporary) ||
        snprintf(destination, sizeof(destination), "%s/frame.ppm", arguments[2]) >= (int)sizeof(destination)) return 2;
    signal(SIGTERM, stop_capture);
    signal(SIGINT, stop_capture);
    publish_status(arguments[2], &status);
    descriptor = open(arguments[1], O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (descriptor < 0) { capture_error(&status, "OPEN", errno); goto cleanup; }
    struct v4l2_capability capability = {0};
    if (video_ioctl(descriptor, VIDIOC_QUERYCAP, &capability) < 0) { capture_error(&status, "QUERY_CAPABILITIES", errno); goto cleanup; }
    unsigned capabilities = capability.capabilities & V4L2_CAP_DEVICE_CAPS ? capability.device_caps : capability.capabilities;
    if (!(capabilities & V4L2_CAP_VIDEO_CAPTURE) || !(capabilities & V4L2_CAP_STREAMING)) {
        capture_error(&status, "CAPTURE_CAPABILITIES", ENOTSUP); goto cleanup;
    }
    for (unsigned index = 0; index < 64; index++) {
        struct v4l2_fmtdesc description = {.index = index, .type = type};
        if (video_ioctl(descriptor, VIDIOC_ENUM_FMT, &description) < 0) break;
        if (description.pixelformat == V4L2_PIX_FMT_YUYV) status.supports_yuyv = true;
        if (description.pixelformat == V4L2_PIX_FMT_MJPEG) status.supports_mjpeg = true;
    }
    struct v4l2_format format = {.type = type};
    format.fmt.pix.width = 640;
    format.fmt.pix.height = 480;
    format.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV;
    format.fmt.pix.field = V4L2_FIELD_NONE;
    if (video_ioctl(descriptor, VIDIOC_S_FMT, &format) < 0) { capture_error(&status, "SET_FORMAT", errno); goto cleanup; }
    status.width = format.fmt.pix.width;
    status.height = format.fmt.pix.height;
    status.stride = format.fmt.pix.bytesperline;
    status.pixel_format = format.fmt.pix.pixelformat;
    if (format.fmt.pix.pixelformat != V4L2_PIX_FMT_YUYV ||
        format.fmt.pix.width < 160 || format.fmt.pix.width > 1280 || format.fmt.pix.height < 120 ||
        format.fmt.pix.height > 720 || (format.fmt.pix.width & 1)) {
        capture_error(&status, "ACCEPT_FORMAT", EPROTONOSUPPORT); goto cleanup;
    }
    unsigned width = format.fmt.pix.width, height = format.fmt.pix.height;
    unsigned stride = format.fmt.pix.bytesperline;
    if (stride < width * 2 || stride > width * 2 + 4096) { capture_error(&status, "ROW_STRIDE", EPROTO); goto cleanup; }
    struct v4l2_streamparm parameters = {.type = type};
    parameters.parm.capture.timeperframe.numerator = 1;
    parameters.parm.capture.timeperframe.denominator = 10;
    if (video_ioctl(descriptor, VIDIOC_S_PARM, &parameters) == 0) {
        status.fps_numerator = parameters.parm.capture.timeperframe.numerator;
        status.fps_denominator = parameters.parm.capture.timeperframe.denominator;
    }
    struct v4l2_requestbuffers request = {.count = 4, .type = type, .memory = V4L2_MEMORY_MMAP};
    if (video_ioctl(descriptor, VIDIOC_REQBUFS, &request) < 0) { capture_error(&status, "REQUEST_BUFFERS", errno); goto cleanup; }
    if (request.count < 2 || request.count > 4) { capture_error(&status, "BUFFER_COUNT", EPROTO); goto cleanup; }
    for (unsigned index = 0; index < request.count; index++) {
        struct v4l2_buffer buffer = {.type = type, .memory = V4L2_MEMORY_MMAP, .index = index};
        if (video_ioctl(descriptor, VIDIOC_QUERYBUF, &buffer) < 0) { capture_error(&status, "QUERY_BUFFER", errno); goto cleanup; }
        lengths[index] = buffer.length;
        buffers[index] = mmap(NULL, buffer.length, PROT_READ | PROT_WRITE, MAP_SHARED, descriptor, buffer.m.offset);
        if (buffers[index] == MAP_FAILED) { buffers[index] = NULL; capture_error(&status, "MAP_BUFFER", errno); goto cleanup; }
        if (video_ioctl(descriptor, VIDIOC_QBUF, &buffer) < 0) { capture_error(&status, "QUEUE_BUFFER", errno); goto cleanup; }
    }
    rgb = malloc((size_t)width * height * 3);
    if (!rgb) { capture_error(&status, "ALLOCATE_RGB", ENOMEM); goto cleanup; }
    if (video_ioctl(descriptor, VIDIOC_STREAMON, &type) < 0) { capture_error(&status, "STREAM_ON", errno); goto cleanup; }
    streaming = true;
    status.stage = "WAIT_FRAME";
    publish_status(arguments[2], &status);
    printf("UT_UVC_CAPTURE actual_stream=%ux%u YUYV source=USB_CAMERA no_HV_or_motion\n", width, height);
    fflush(stdout);
    uint64_t previous_publish = 0;
    uint64_t stream_id = monotonic_ns();
    unsigned consecutive_errors = 0, consecutive_timeouts = 0;
    while (running) {
        struct pollfd waiter = {.fd = descriptor, .events = POLLIN};
        int available = poll(&waiter, 1, 2000);
        if (available < 0 && errno == EINTR) continue;
        if (available == 0 && ++consecutive_timeouts < 5) continue;
        if (available <= 0 || waiter.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            capture_error(&status, "POLL_FRAME", available == 0 ? ETIMEDOUT : available < 0 ? errno : ENODEV); goto cleanup;
        }
        consecutive_timeouts = 0;
        struct v4l2_buffer buffer = {.type = type, .memory = V4L2_MEMORY_MMAP};
        if (video_ioctl(descriptor, VIDIOC_DQBUF, &buffer) < 0) { if (errno == EAGAIN) continue; capture_error(&status, "DEQUEUE_BUFFER", errno); goto cleanup; }
        uint64_t now = monotonic_ns();
        size_t needed = (size_t)(height - 1) * stride + width * 2;
        if (buffer.index >= request.count || buffer.bytesused > lengths[buffer.index]) {
            capture_error(&status, "BUFFER_BOUNDS", EPROTO); goto cleanup;
        }
        status.sequence = buffer.sequence;
        if (buffer.bytesused < needed || buffer.flags & V4L2_BUF_FLAG_ERROR) {
            status.dropped_frames++;
            if (++consecutive_errors >= 20) { capture_error(&status, "BAD_FRAME_LIMIT", EIO); goto cleanup; }
            status.state = "RECOVERING";
            status.stage = "DROP_BAD_FRAME";
            publish_status(arguments[2], &status);
            if (video_ioctl(descriptor, VIDIOC_QBUF, &buffer) < 0) { capture_error(&status, "REQUEUE_BUFFER", errno); goto cleanup; }
            continue;
        }
        consecutive_errors = 0;
        if (now - previous_publish >= 100000000ULL) {
            for (unsigned row = 0; row < height; row++) {
                const uint8_t *input = (const uint8_t *)buffers[buffer.index] + (size_t)row * stride;
                uint8_t *output = rgb + (size_t)row * width * 3;
                for (unsigned column = 0; column < width; column += 2) {
                    int chroma_u = input[1] - 128, chroma_v = input[3] - 128;
                    for (unsigned half = 0; half < 2; half++) {
                        int luma = input[half * 2] - 16;
                        output[half * 3] = clipped((298 * luma + 409 * chroma_v + 128) >> 8);
                        output[half * 3 + 1] = clipped((298 * luma - 100 * chroma_u - 208 * chroma_v + 128) >> 8);
                        output[half * 3 + 2] = clipped((298 * luma + 516 * chroma_u + 128) >> 8);
                    }
                    input += 4;
                    output += 6;
                }
            }
            int output_descriptor = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
            FILE *output = output_descriptor < 0 ? NULL : fdopen(output_descriptor, "wb");
            if (!output) { int error_number = errno; if (output_descriptor >= 0) close(output_descriptor); capture_error(&status, "OPEN_OUTPUT", error_number); goto cleanup; }
            ut_red_marker_t marker = ut_red_marker(rgb, width, height);
            bool written = fprintf(output, "P6\n# UTCAM2 %u %llu %llu %u %u %u %u %u\n%u %u\n255\n",
                                   buffer.sequence, (unsigned long long)now, (unsigned long long)stream_id,
                                   marker.status, marker.candidate_count, (unsigned)(marker.pixel_x * 1000.0f + .5f),
                                   (unsigned)(marker.pixel_y * 1000.0f + .5f), marker.area_pixels, width, height) > 0;
            written = fwrite(rgb, 1, (size_t)width * height * 3, output) == (size_t)width * height * 3 && written;
            int closed = fclose(output);
            if (closed || !written || rename(temporary, destination)) { capture_error(&status, "PUBLISH_FRAME", errno ? errno : EIO); goto cleanup; }
            previous_publish = now;
            status.published_frames++;
            status.last_frame_ns = now;
            status.state = "RUNNING";
            status.stage = "PUBLISH_FRAME";
            publish_status(arguments[2], &status);
        }
        if (video_ioctl(descriptor, VIDIOC_QBUF, &buffer) < 0) { capture_error(&status, "REQUEUE_BUFFER", errno); goto cleanup; }
    }
    result = 0;
cleanup:
    if (!result) { status.state = "STOPPED"; status.stage = "SIGNAL"; }
    publish_status(arguments[2], &status);
    if (streaming) video_ioctl(descriptor, VIDIOC_STREAMOFF, &type);
    for (unsigned index = 0; index < 4; index++) if (buffers[index]) munmap(buffers[index], lengths[index]);
    if (descriptor >= 0) close(descriptor);
    free(rgb);
    return result;
}
