#!/usr/bin/env python3
"""VNC Computer-Use CLI - control a remote desktop via VNC.

Use a mouse and keyboard to interact with a computer, and take screenshots.

* This is an interface to a desktop GUI. You do not have access to a terminal
  or applications menu. You must click on desktop icons to start applications.
* Always prefer using keyboard shortcuts rather than clicking, where possible.
* Some applications may take time to start or process actions, so you may need
  to wait and take successive screenshots to see the results of your actions.
  E.g. if you click on Firefox and a window doesn't open, try taking another
  screenshot.
* Whenever you intend to move the cursor to click on an element like an icon,
  you should consult a screenshot to determine the coordinates of the element
  before moving the cursor.
* If you tried clicking on a program or link but it failed to load, even after
  waiting, try adjusting your cursor position so that the tip of the cursor
  visually falls on the element that you want to click.
* Make sure to click any buttons, links, icons, etc with the cursor tip in the
  center of the element. Don't click boxes on their edges unless asked.
"""

import argparse
import base64
import json
import os
import signal
import socket
import subprocess
import sys
import time

DEFAULT_SESSION = "default"
SOCKET_TIMEOUT = 60  # seconds

ACTION_DESCRIPTIONS = """\
Available actions:
  key              Press a key or key-combination on the keyboard.
                   Examples: "enter", "tab", "ctrl-c", "alt-f4", "shift-a"
  type             Type a string of text on the keyboard. Short text is typed
                   character by character; long text uses clipboard paste.
  mouse_move       Move the cursor to a specified (x, y) coordinate.
  left_click       Click the left mouse button. Optionally move to (x, y) first.
  left_click_drag  Click and drag the cursor to a specified (x, y) coordinate.
  right_click      Click the right mouse button. Optionally move to (x, y) first.
  middle_click     Click the middle mouse button. Optionally move to (x, y) first.
  double_click     Double-click the left mouse button. Optionally move to (x, y) first.
  scroll           Scroll the screen in a direction. Requires a coordinate.
                   Text param specifies direction and optional pixel amount:
                   "up", "down", "left", "right", or "down:500" for 500 pixels.
  get_screenshot   Take a screenshot of the screen.
  get_cursor_position  Get the current (x, y) coordinate of the cursor.
  get_screen_size  Get the screen dimensions (width x height).
  status           Show daemon connection status.
"""


def session_socket_path(session):
    return f"/tmp/vnc-cli-{session}.sock"


def session_pid_file(session):
    return f"/tmp/vnc-cli-{session}.pid"


def session_log_file(session):
    return f"/tmp/vnc-cli-{session}.log"


def send_command(session, request):
    """Send a JSON command to the daemon and return the response."""
    socket_path = session_socket_path(session)
    if not os.path.exists(socket_path):
        result = {"error": f"VNC session '{session}' not running. Run 'vnc connect <host> --session {session}' first."}
        print(json.dumps(result))
        sys.exit(1)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(SOCKET_TIMEOUT)
    try:
        sock.connect(socket_path)
        sock.sendall(json.dumps(request).encode() + b"\n")

        chunks = []
        while True:
            chunk = sock.recv(1 << 20)  # 1MB buffer
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        data = b"".join(chunks)
        return json.loads(data.strip())
    except socket.timeout:
        result = {"error": f"VNC session '{session}' timed out."}
        print(json.dumps(result))
        sys.exit(1)
    except ConnectionRefusedError:
        result = {"error": f"VNC session '{session}' not responding. Try 'vnc disconnect --session {session}' then reconnect."}
        print(json.dumps(result))
        sys.exit(1)
    finally:
        sock.close()


def cmd_connect(args):
    session = args.session
    pid_file = session_pid_file(session)
    socket_path = session_socket_path(session)
    log_file = session_log_file(session)

    if os.path.exists(pid_file):
        with open(pid_file) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
            print(json.dumps({"error": f"VNC session '{session}' already running (PID {pid}). Use 'vnc disconnect --session {session}' first."}))
            sys.exit(1)
        except ProcessLookupError:
            os.unlink(pid_file)
            if os.path.exists(socket_path):
                os.unlink(socket_path)

    daemon_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daemon.py")
    cmd = [sys.executable, daemon_script, args.host, "--session", session]
    if args.password:
        cmd += ["--password", args.password]
    if args.username:
        cmd += ["--username", args.username]

    with open(log_file, "w") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)

    for _ in range(30):
        if os.path.exists(socket_path):
            print(json.dumps({"ok": True, "session": session, "host": args.host, "pid": proc.pid}))
            return
        ret = proc.poll()
        if ret is not None:
            err = ""
            try:
                with open(log_file) as f:
                    err = f.read()
            except Exception:
                pass
            print(json.dumps({"error": f"Daemon exited with code {ret}.", "log": err}))
            sys.exit(1)
        time.sleep(0.5)

    proc.terminate()
    print(json.dumps({"error": f"Timeout waiting for daemon to start. Check {log_file}"}))
    sys.exit(1)


def cmd_disconnect(args):
    session = args.session
    pid_file = session_pid_file(session)
    socket_path = session_socket_path(session)

    if not os.path.exists(pid_file):
        print(json.dumps({"ok": True, "message": f"VNC session '{session}' was not running."}))
        return

    with open(pid_file) as f:
        pid = int(f.read().strip())

    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.2)
            except ProcessLookupError:
                break
    except ProcessLookupError:
        pass

    for path in [pid_file, socket_path]:
        try:
            os.unlink(path)
        except OSError:
            pass

    print(json.dumps({"ok": True, "session": session, "message": "disconnected"}))


def cmd_action(args):
    """Unified action handler -- mirrors the computer-use tool interface."""
    action = args.action
    req = {"action": action}

    # Parse coordinate
    if args.coordinate is not None:
        if len(args.coordinate) != 2:
            print(json.dumps({"error": "coordinate requires exactly 2 values: x y"}))
            sys.exit(1)
        req["x"] = args.coordinate[0]
        req["y"] = args.coordinate[1]

    # Parse text
    if args.text is not None:
        req["text"] = args.text

    # Parse output (screenshot)
    if hasattr(args, "output") and args.output:
        req["output"] = os.path.abspath(args.output)

    # Validate required params per action
    needs_coord = {"mouse_move", "left_click_drag", "scroll"}
    needs_text = {"key", "type", "scroll"}
    optional_coord = {"left_click", "right_click", "middle_click", "double_click"}

    if action in needs_coord and "x" not in req:
        print(json.dumps({"error": f"coordinate required for {action}"}))
        sys.exit(1)
    if action in needs_text and "text" not in req:
        print(json.dumps({"error": f"text required for {action}"}))
        sys.exit(1)
    if action not in needs_coord and action not in optional_coord and "x" in req:
        # Ignore stray coordinates for actions that don't use them
        del req["x"]
        del req["y"]

    resp = send_command(args.session, req)

    # Handle screenshot output
    if action == "get_screenshot" and resp.get("ok"):
        img_w = resp.get("image_width")
        img_h = resp.get("image_height")
        if "path" in resp:
            print(json.dumps({"ok": True, "path": resp["path"], "image_width": img_w, "image_height": img_h}))
        elif "data" in resp:
            if args.output:
                png_data = base64.b64decode(resp["data"])
                output = os.path.abspath(args.output)
                with open(output, "wb") as f:
                    f.write(png_data)
                print(json.dumps({"ok": True, "path": output, "image_width": img_w, "image_height": img_h}))
            else:
                print(json.dumps({"ok": True, "format": "png", "data": resp["data"], "image_width": img_w, "image_height": img_h}))
        return

    print(json.dumps(resp))


def _rewrite_argv():
    """Rewrite sys.argv to map positional shorthand to --text / --coordinate flags."""
    argv = sys.argv[1:]
    if len(argv) < 1:
        return

    # Find session flag and skip it
    clean = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--session", "-s") and i + 1 < len(argv):
            clean.append(argv[i])
            clean.append(argv[i + 1])
            i += 2
        else:
            clean.append(argv[i])
            i += 1

    # Separate global flags from subcommand
    global_flags = []
    rest = []
    i = 0
    while i < len(clean):
        if clean[i] in ("--session", "-s"):
            global_flags += [clean[i], clean[i + 1]]
            i += 2
        elif clean[i].startswith("-") and not rest:
            global_flags.append(clean[i])
            i += 1
        else:
            rest = clean[i:]
            break

    if not rest:
        return

    action = rest[0]
    action_args = rest[1:]

    text_actions = {"key", "type"}
    coord_actions = {"mouse_move", "left_click_drag"}
    optional_coord_actions = {"left_click", "right_click", "middle_click", "double_click"}
    scroll_action = {"scroll"}

    # Already has flags -- don't rewrite
    if any(a.startswith("-") for a in action_args if a not in ("-o", "--output")):
        return

    new_args = global_flags + [action]

    if action in text_actions and action_args:
        # vnc key enter  /  vnc type "hello"
        # Find -o flag if present
        text_parts = []
        j = 0
        while j < len(action_args):
            if action_args[j] in ("-o", "--output") and j + 1 < len(action_args):
                new_args += [action_args[j], action_args[j + 1]]
                j += 2
            else:
                text_parts.append(action_args[j])
                j += 1
        if text_parts:
            new_args += ["--text", " ".join(text_parts)]

    elif action in scroll_action and action_args:
        # vnc scroll down:5 100 200
        parts = []
        j = 0
        while j < len(action_args):
            if action_args[j] in ("-o", "--output") and j + 1 < len(action_args):
                new_args += [action_args[j], action_args[j + 1]]
                j += 2
            else:
                parts.append(action_args[j])
                j += 1
        if parts:
            new_args += ["--text", parts[0]]
        if len(parts) >= 3:
            new_args += ["--coordinate", parts[1], parts[2]]

    elif action in coord_actions and action_args:
        # vnc mouse_move 100 200
        parts = []
        j = 0
        while j < len(action_args):
            if action_args[j] in ("-o", "--output") and j + 1 < len(action_args):
                new_args += [action_args[j], action_args[j + 1]]
                j += 2
            else:
                parts.append(action_args[j])
                j += 1
        if len(parts) >= 2:
            new_args += ["--coordinate", parts[0], parts[1]]

    elif action in optional_coord_actions and action_args:
        # vnc left_click 100 200
        parts = []
        j = 0
        while j < len(action_args):
            if action_args[j] in ("-o", "--output") and j + 1 < len(action_args):
                new_args += [action_args[j], action_args[j + 1]]
                j += 2
            else:
                parts.append(action_args[j])
                j += 1
        if len(parts) >= 2:
            new_args += ["--coordinate", parts[0], parts[1]]

    elif action == "get_screenshot":
        new_args += action_args

    else:
        new_args += action_args

    sys.argv = [sys.argv[0]] + new_args


def main():
    _rewrite_argv()

    parser = argparse.ArgumentParser(
        prog="vnc",
        description=__doc__,
        epilog=ACTION_DESCRIPTIONS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--session", "-s", default=DEFAULT_SESSION,
                        help=f"Session name for multi-connection support (default: {DEFAULT_SESSION})")
    sub = parser.add_subparsers(dest="command")

    # connect
    p = sub.add_parser("connect", help="Connect to VNC server and start daemon")
    p.add_argument("host", help="VNC host (e.g. localhost::5900 or localhost:0)")
    p.add_argument("--password", "-p", help="VNC password")
    p.add_argument("--username", "-u", help="VNC/ARD username (required for macOS Screen Sharing)")

    # disconnect
    sub.add_parser("disconnect", help="Stop VNC daemon")

    # All computer-use actions share the same interface
    actions = [
        "key", "type", "mouse_move",
        "left_click", "left_click_drag", "right_click",
        "middle_click", "double_click", "scroll",
        "get_screenshot", "get_cursor_position",
        "get_screen_size", "status",
    ]

    for action in actions:
        p = sub.add_parser(action)
        p.add_argument("--coordinate", "-c", type=int, nargs=2, metavar=("X", "Y"),
                        help="(x, y) pixel coordinate on the screen")
        p.add_argument("--text", "-t", help="Text to type, key name, or scroll direction")
        if action == "get_screenshot":
            p.add_argument("--output", "-o", help="Output file path (default: base64 JSON to stdout)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "connect":
        cmd_connect(args)
    elif args.command == "disconnect":
        cmd_disconnect(args)
    else:
        args.action = args.command
        cmd_action(args)


if __name__ == "__main__":
    main()
