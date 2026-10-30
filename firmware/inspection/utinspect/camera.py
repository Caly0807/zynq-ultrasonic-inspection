import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import cv2
import numpy as np


def find_ffmpeg(explicit=None):
    if explicit:
        executable = Path(explicit)
        if not executable.is_file():
            raise ValueError("The specified FFmpeg executable does not exist")
        return str(executable.resolve())
    installed = shutil.which("ffmpeg")
    if installed:
        return installed
    binaries = Path(sys.base_prefix) / "Lib/site-packages/imageio_ffmpeg/binaries"
    candidates = sorted(binaries.glob("ffmpeg-*.exe"))
    if candidates:
        return str(candidates[0])
    raise ValueError("Named Windows camera capture needs FFmpeg; supply --ffmpeg with an installed executable")


def read_exact(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


class Camera:
    def __init__(self, index=0, width=640, height=480, device_name=None, ffmpeg=None):
        if index < 0 or width <= 0 or height <= 0:
            raise ValueError("Invalid camera index or image dimensions")
        if device_name and (os.name != "nt" or not device_name.strip()):
            raise ValueError("Named DirectShow capture is supported only on Windows")
        self.width, self.height = width, height
        self.timestamp_ns = 0
        self._capture = self._process = self._thread = self._log = None
        self._device_name = device_name
        self._ffmpeg_executable = None
        self._reader_error = None
        self._stopped = threading.Event()
        self._frames = queue.Queue(maxsize=2)
        try:
            if device_name:
                self.backend = "FFMPEG_DSHOW:" + device_name
                self._log = tempfile.TemporaryFile()
                self._ffmpeg_executable = find_ffmpeg(ffmpeg)
                command = [self._ffmpeg_executable, "-nostdin", "-hide_banner", "-loglevel", "error",
                           "-f", "dshow", "-video_size", f"{width}x{height}", "-framerate", "10",
                           "-i", "video=" + device_name, "-an", "-vf", f"scale={width}:{height}",
                           "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
                self._process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=self._log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self._thread = threading.Thread(target=self._read_frames, daemon=True)
                self._thread.start()
            else:
                self.backend = "OPENCV_DSHOW" if os.name == "nt" else "OPENCV_V4L2"
                self._capture = cv2.VideoCapture(index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_V4L2)
                if not self._capture.isOpened():
                    raise OSError('Camera cannot open. On Windows try --camera-name "PC Camera"; on Linux verify /dev/videoN.')
                self._capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                self._capture.set(cv2.CAP_PROP_FPS, 10)
        except BaseException:
            self.release()
            raise

    def _publish(self, item):
        try:
            self._frames.put_nowait(item)
        except queue.Full:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                pass
            self._frames.put_nowait(item)

    def _read_frames(self):
        try:
            while not self._stopped.is_set():
                data = read_exact(self._process.stdout, self.width * self.height * 3)
                if data is None:
                    break
                frame = np.frombuffer(data, np.uint8).reshape(self.height, self.width, 3).copy()
                self._publish((time.monotonic_ns(), frame))
        except (OSError, ValueError) as error:
            self._reader_error = str(error)
        finally:
            self._publish((0, None))

    def _stream_error(self, message, ended=False):
        if self._stopped.is_set():
            return OSError("Camera was closed")
        process, log = self._process, self._log
        return_code = process.poll() if process is not None else None
        if ended and process is not None and return_code is None:
            try:
                return_code = process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        stderr = ""
        if log is not None:
            try:
                length = log.seek(0, os.SEEK_END)
                log.seek(max(0, length - 8192))
                stderr = log.read().decode("utf-8", errors="replace")
                log.seek(0, os.SEEK_END)
            except (OSError, ValueError):
                pass
        stderr = "\n".join(line for line in stderr.splitlines() if line.strip())
        details = [message,
                   f"Camera: {self._device_name!r}; requested {self.width}x{self.height} @ 10 fps",
                   f"FFmpeg: {self._ffmpeg_executable}; exit code: {return_code if return_code is not None else 'running'}",
                   "FFmpeg stderr:\n" + (stderr or "(empty)")]
        if self._reader_error:
            details.append("Frame reader: " + self._reader_error)
        return OSError("\n".join(details))

    def read(self, timeout=5):
        if self._stopped.is_set():
            raise OSError("Camera was closed")
        if self._capture is not None:
            success, frame = self._capture.read()
            self.timestamp_ns = time.monotonic_ns()
            if not success or frame is None:
                raise OSError("Camera returned no image")
            return True, frame
        try:
            timestamp_ns, frame = self._frames.get(timeout=timeout)
        except queue.Empty:
            raise self._stream_error("Camera frame timeout; check cable, selected format and camera ownership") from None
        if frame is None:
            raise self._stream_error("Named camera stream ended", ended=True)
        if time.monotonic_ns() - timestamp_ns > 1_000_000_000:
            raise OSError("Camera frame is stale")
        self.timestamp_ns = timestamp_ns
        return True, frame

    def release(self):
        self._stopped.set()
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
            if self._thread is not None:
                self._thread.join(timeout=1)
            self._process.stdout.close()
            self._process = None
        if self._log is not None:
            self._log.close()
            self._log = None
