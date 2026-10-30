import argparse
import json
import signal
import sys
import threading
import uuid
from pathlib import Path

import cv2
import numpy as np

from .camera import Camera
from .device_app import DeviceHTTPServer, runtime_status
from .network import NetworkAccess, load_token, private_address
from .workpiece import VisualSettings, WorkpieceSpec
from .workpiece_app import WorkpieceSession, make_handler
from .workpiece_config import read_profile


def create_vision_server(address, port, peer, token, session):
    access = NetworkAccess(private_address(address), private_address(peer), token)
    status = runtime_status()
    status.update({"vision_active": session.camera is not None, "image_source": session.image_source,
                   "coordinate_source": "VISUAL_PLANAR_UNVERIFIED_UNTIL_CONFIRMED"})
    server = DeviceHTTPServer((address, port), make_handler(session, lambda: server.shutdown(),
                                                          access_policy=access, device_status=status))
    server.device_status = status
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Board-side V4L2 workpiece/probe vision; no HV or motion outputs")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--peer", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--profile", type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--camera-index", type=int)
    inputs.add_argument("--image", type=Path)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    arguments = parser.parse_args(argv)
    private_address(arguments.bind)
    private_address(arguments.peer)
    if not 1024 <= arguments.port <= 65535 or arguments.width <= 0 or arguments.height <= 0:
        raise ValueError("Invalid port or camera resolution")
    if arguments.bind == arguments.peer and arguments.bind != "127.0.0.1":
        raise ValueError("The device and PC must have different IPv4 addresses")
    if arguments.camera_index is not None and (sys.platform != "linux" or arguments.camera_index < 0):
        raise ValueError("Live board vision requires Linux and a non-negative V4L2 capture-node index")
    profile = arguments.profile or arguments.data / "workpiece-profile.json"
    spec, settings, reference = WorkpieceSpec(), VisualSettings(), None
    if profile.is_file():
        spec, settings, reference = read_profile(profile)
    token = load_token(arguments.token_file or arguments.data / "operator.token")
    cv2.setNumThreads(1)
    camera = session = server = None
    try:
        if arguments.image:
            image = cv2.imdecode(np.fromfile(arguments.image, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("Could not decode the recorded input image")
        else:
            camera = Camera(arguments.camera_index, arguments.width, arguments.height)
            _, image = camera.read()
        output = arguments.data / ("vision_" + uuid.uuid4().hex)
        session = WorkpieceSession(image, output, spec, camera, settings, profile)
        session.saved_face_reference = reference
        server = create_vision_server(arguments.bind, arguments.port, arguments.peer, token, session)
        signal.signal(signal.SIGTERM, lambda signum, frame: threading.Thread(target=server.shutdown, daemon=True).start())
        session.start()
        print(f"UT_DEVICE_VISION http://{arguments.bind}:{arguments.port} SOURCE={session.image_source}", flush=True)
        print(json.dumps(server.device_status, ensure_ascii=False), flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        if session:
            session.close()
        elif camera:
            camera.release()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, cv2.error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
