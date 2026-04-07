#!/usr/bin/env python3
"""VNC CLI Daemon - maintains VNC connection and serves commands over Unix socket."""

import json
import math
import os
import signal
import socketserver
import sys
import tempfile
import time
import base64
import io
import threading
import traceback

DEFAULT_SESSION = "default"

# Claude API automatically downsamples images larger than these limits.
# We pre-downscale and report image dimensions so the agent uses correct coordinates.
# See: https://docs.anthropic.com/en/docs/build-with-claude/vision#evaluate-image-size
MAX_LONG_EDGE = 1568
MAX_PIXELS = 1.15 * 1024 * 1024  # 1.15 megapixels

# Delay before taking screenshot to let UI render
SCREENSHOT_DELAY = 1.0  # seconds


def session_socket_path(session):
    return f"/tmp/vnc-cli-{session}.sock"


def session_pid_file(session):
    return f"/tmp/vnc-cli-{session}.pid"


def session_log_file(session):
    return f"/tmp/vnc-cli-{session}.log"

# Threshold: texts shorter than this use keyPress; longer use paste()
PASTE_THRESHOLD = 32


def _get_api_scale(width, height):
    """Calculate scale factor to fit image within Claude API limits.

    Returns a value <= 1 representing how much to shrink the image.
    """
    long_edge = max(width, height)
    total_pixels = width * height

    long_edge_scale = MAX_LONG_EDGE / long_edge if long_edge > MAX_LONG_EDGE else 1.0
    pixel_scale = math.sqrt(MAX_PIXELS / total_pixels) if total_pixels > MAX_PIXELS else 1.0

    return min(long_edge_scale, pixel_scale)


def _draw_crosshair(image, x, y, size=20, color=(255, 0, 0), thickness=3):
    """Draw a crosshair on a PIL Image at (x, y)."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    w, h = image.size
    half_t = thickness // 2
    # Horizontal line
    x0 = max(0, x - size)
    x1 = min(w - 1, x + size)
    draw.rectangle([x0, y - half_t, x1, y + half_t], fill=color)
    # Vertical line
    y0 = max(0, y - size)
    y1 = min(h - 1, y + size)
    draw.rectangle([x - half_t, y0, x + half_t, y1], fill=color)


class VNCController:
    """Wraps vncdotool client with local cursor tracking and coordinate scaling."""

    def __init__(self, host, password=None, username=None, session=DEFAULT_SESSION):
        import vncdotool.api
        self._api = vncdotool.api
        self.client = self._api.connect(host, password=password, username=username)
        self.cursor_x = 0
        self.cursor_y = 0
        self.host = host
        self.session = session
        self._lock = threading.Lock()
        # Scale factor: image_coords * _api_to_screen_scale = screen_coords
        self._api_to_screen_scale = 1.0

    def shutdown(self):
        try:
            self.client.disconnect()
        except Exception:
            pass
        try:
            self._api.shutdown()
        except Exception:
            pass

    def handle(self, request):
        action = request.get("action")
        with self._lock:
            handler = getattr(self, f"_do_{action}", None)
            if handler is None:
                return {"error": f"Unknown action: {action}"}
            try:
                return handler(request)
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                return {"error": str(e)}

    # --- Coordinate helpers ---

    def _get_screen_size(self):
        """Return (width, height) of the VNC screen."""
        self.client.refreshScreen()
        return self.client.screen.size

    def _to_screen_coords(self, x, y):
        """Convert API image coordinates to screen coordinates."""
        scale = self._api_to_screen_scale
        return round(x * scale), round(y * scale)

    def _to_image_coords(self, x, y):
        """Convert screen coordinates to API image coordinates."""
        scale = self._api_to_screen_scale
        if scale == 0:
            return x, y
        return round(x / scale), round(y / scale)

    def _require_coords(self, req):
        x = req.get("x")
        y = req.get("y")
        if x is None or y is None:
            raise ValueError("x and y coordinates required")
        sx, sy = self._to_screen_coords(int(x), int(y))
        self._validate_coords(sx, sy)
        return sx, sy

    def _optional_coords(self, req):
        x = req.get("x")
        y = req.get("y")
        if x is not None and y is not None:
            sx, sy = self._to_screen_coords(int(x), int(y))
            self._validate_coords(sx, sy)
            return sx, sy
        return None, None

    def _validate_coords(self, x, y):
        """Raise if coordinates are outside screen bounds."""
        w, h = self._get_screen_size()
        if x < 0 or x >= w or y < 0 or y >= h:
            raise ValueError(f"Coordinates ({x}, {y}) are outside display bounds of {w}x{h}")

    # --- Actions ---

    def _do_key(self, req):
        text = req.get("text")
        if not text:
            return {"error": "text required for key"}
        self.client.keyPress(text)
        return {"ok": True}

    def _do_type(self, req):
        text = req.get("text")
        if not text:
            return {"error": "text required for type"}
        if len(text) >= PASTE_THRESHOLD:
            self.client.paste(text)
        else:
            for char in text:
                self.client.keyPress(char)
        return {"ok": True}

    def _do_mouse_move(self, req):
        x, y = self._require_coords(req)
        self.client.mouseMove(x, y)
        self.cursor_x, self.cursor_y = x, y
        return {"ok": True}

    def _do_left_click(self, req):
        x, y = self._optional_coords(req)
        if x is not None:
            self.client.mouseMove(x, y)
            self.cursor_x, self.cursor_y = x, y
        self.client.mousePress(1)
        return {"ok": True}

    def _do_right_click(self, req):
        x, y = self._optional_coords(req)
        if x is not None:
            self.client.mouseMove(x, y)
            self.cursor_x, self.cursor_y = x, y
        self.client.mousePress(3)
        return {"ok": True}

    def _do_middle_click(self, req):
        x, y = self._optional_coords(req)
        if x is not None:
            self.client.mouseMove(x, y)
            self.cursor_x, self.cursor_y = x, y
        self.client.mousePress(2)
        return {"ok": True}

    def _do_double_click(self, req):
        x, y = self._optional_coords(req)
        if x is not None:
            self.client.mouseMove(x, y)
            self.cursor_x, self.cursor_y = x, y
        self.client.mousePress(1)
        self.client.pause(0.1)
        self.client.mousePress(1)
        return {"ok": True}

    def _do_left_click_drag(self, req):
        x, y = self._require_coords(req)
        step = int(req.get("step", 10))
        self.client.mouseDrag(x, y, step=step)
        self.cursor_x, self.cursor_y = x, y
        return {"ok": True}

    def _do_scroll(self, req):
        text = req.get("text")
        if not text:
            return {"error": "text required for scroll. Use 'up', 'down', 'left', 'right', or 'down:500'"}

        # Parse direction and optional amount from text (e.g. "down" or "down:5")
        parts = text.split(":")
        direction = parts[0].lower()
        amount = int(parts[1]) if len(parts) > 1 else 3

        x, y = self._optional_coords(req)
        if x is not None:
            self.client.mouseMove(x, y)
            self.cursor_x, self.cursor_y = x, y

        # VNC scroll uses mouse buttons 4/5 (up/down) and 6/7 (left/right)
        button_map = {"up": 4, "down": 5, "left": 6, "right": 7}
        button = button_map.get(direction)
        if button is None:
            return {"error": f"Invalid scroll direction: '{direction}'. Use 'up', 'down', 'left', or 'right'"}
        for _ in range(amount):
            self.client.mousePress(button)
        return {"ok": True}

    def _do_get_screenshot(self, req):
        from PIL import Image

        output = req.get("output")

        # Wait for UI to settle before capturing
        time.sleep(SCREENSHOT_DELAY)

        # Capture screen to PIL Image
        self.client.refreshScreen()
        screen_w, screen_h = self.client.screen.size

        tmpfile = os.path.join(tempfile.gettempdir(), f"vnc-screenshot-{os.getpid()}.png")
        try:
            self.client.captureScreen(tmpfile)
            image = Image.open(tmpfile)
        finally:
            try:
                os.unlink(tmpfile)
            except OSError:
                pass

        # Calculate downscale factor and update coordinate mapping
        api_scale = _get_api_scale(screen_w, screen_h)
        self._api_to_screen_scale = 1.0 / api_scale

        if api_scale < 1.0:
            new_w = int(screen_w * api_scale)
            new_h = int(screen_h * api_scale)
            image = image.resize((new_w, new_h), Image.LANCZOS)

        # Draw crosshair at cursor position (in image coordinates)
        cursor_img_x, cursor_img_y = self._to_image_coords(self.cursor_x, self.cursor_y)
        img_w, img_h = image.size
        if 0 <= cursor_img_x < img_w and 0 <= cursor_img_y < img_h:
            _draw_crosshair(image, cursor_img_x, cursor_img_y)

        # Encode result
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()

        result_w, result_h = image.size

        if output:
            with open(output, "wb") as f:
                f.write(png_bytes)
            return {"ok": True, "path": output, "image_width": result_w, "image_height": result_h}
        else:
            data = base64.b64encode(png_bytes).decode("ascii")
            return {"ok": True, "data": data, "format": "png", "image_width": result_w, "image_height": result_h}

    def _do_get_cursor_position(self, req):
        # Return cursor position in API image coordinates
        img_x, img_y = self._to_image_coords(self.cursor_x, self.cursor_y)
        return {"ok": True, "x": img_x, "y": img_y}

    def _do_get_screen_size(self, req):
        self.client.refreshScreen()
        screen_w, screen_h = self.client.screen.size
        # Return the image dimensions the agent should use for coordinates
        api_scale = _get_api_scale(screen_w, screen_h)
        return {
            "ok": True,
            "width": int(screen_w * api_scale),
            "height": int(screen_h * api_scale),
        }

    def _do_status(self, req):
        return {
            "ok": True,
            "host": self.host,
            "session": self.session,
            "pid": os.getpid(),
            "cursor_x": self.cursor_x,
            "cursor_y": self.cursor_y,
        }


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                response = {"error": f"Invalid JSON: {e}"}
            else:
                response = self.server.vnc_controller.handle(request)
            self.wfile.write(json.dumps(response).encode() + b"\n")
            self.wfile.flush()


class VNCDaemonServer(socketserver.ThreadingUnixStreamServer):
    def __init__(self, socket_path, handler, vnc_controller):
        self.vnc_controller = vnc_controller
        super().__init__(socket_path, handler)


def cleanup(socket_path, pid_file):
    try:
        os.unlink(socket_path)
    except OSError:
        pass
    try:
        os.unlink(pid_file)
    except OSError:
        pass


def parse_arg(argv, flag):
    if flag in argv:
        idx = argv.index(flag)
        return argv[idx + 1]
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: vnc_daemon.py <host[:display]> [--password <pw>] [--username <user>] [--session <name>]", file=sys.stderr)
        sys.exit(1)

    host = sys.argv[1]
    password = parse_arg(sys.argv, "--password")
    username = parse_arg(sys.argv, "--username")
    session = parse_arg(sys.argv, "--session") or DEFAULT_SESSION

    socket_path = session_socket_path(session)
    pid_file = session_pid_file(session)

    # Clean up stale socket
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    # Connect to VNC
    print(f"[{session}] Connecting to VNC at {host}...", file=sys.stderr)
    try:
        controller = VNCController(host, password=password, username=username, session=session)
    except Exception as e:
        print(f"[{session}] Failed to connect to VNC: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[{session}] VNC connected.", file=sys.stderr)

    # Write PID file
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    # Set up signal handlers for clean shutdown
    def handle_signal(signum, frame):
        print(f"\n[{session}] Shutting down...", file=sys.stderr)
        controller.shutdown()
        cleanup(socket_path, pid_file)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Start socket server
    server = VNCDaemonServer(socket_path, RequestHandler, controller)
    print(f"[{session}] Listening on {socket_path}", file=sys.stderr)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        controller.shutdown()
        cleanup(socket_path, pid_file)


if __name__ == "__main__":
    main()
