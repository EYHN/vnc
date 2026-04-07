---
name: vnc
description: Control a remote desktop via VNC. Use when user asks to interact with a remote computer screen — click, type, screenshot, scroll, or connect/disconnect VNC sessions.
allowed-tools: Bash Read
---

# VNC Computer-Use Tool

You have access to a VNC computer-use CLI tool (`vnc`) that lets you control a remote desktop. Use it to interact with GUIs — click buttons, type text, take screenshots, scroll, and more.

## Installation

If the `vnc` command is not available, install it first:

```bash
pip install vnc-computer-use
```

## Quick Reference

### Connect / Disconnect

```bash
# Connect to a VNC server
vnc connect <host>::<port>
vnc connect <host>::<port> --password <pw>
vnc connect <host>::<port> --username <user> --password <pw>  # macOS Screen Sharing

# Disconnect
vnc disconnect
```

### Screenshots

```bash
# Save screenshot to file (preferred — you can then read the image)
vnc get_screenshot -o /tmp/screen.png

# Get screenshot as base64 JSON
vnc get_screenshot
```

Screenshot response includes `image_width` and `image_height`:
```json
{"ok": true, "path": "/tmp/screen.png", "image_width": 1568, "image_height": 882}
```

### Mouse Actions

```bash
vnc left_click <x> <y>
vnc right_click <x> <y>
vnc double_click <x> <y>
vnc middle_click <x> <y>
vnc mouse_move <x> <y>
vnc left_click_drag <x> <y>     # drag from current position to (x, y)
vnc scroll <direction> <x> <y>  # direction: up, down, left, right, or down:500
```

### Keyboard Actions

```bash
vnc key <key>          # e.g. enter, tab, ctrl-c, alt-f4, shift-a
vnc type "<text>"      # type a string (short: keypress, long: clipboard paste)
```

### Info

```bash
vnc get_screen_size
vnc get_cursor_position
vnc status
```

### Multi-Session

```bash
vnc connect host1::5900 --session work
vnc connect host2::5900 --session home
vnc -s work get_screenshot -o /tmp/work.png
vnc -s home left_click 100 200
vnc disconnect --session work
```

## Coordinate System

**IMPORTANT:** All coordinates are in **image space**, not raw screen space.

- Screenshots are automatically downscaled to fit Claude's vision limits (max 1568px long edge).
- The `image_width` and `image_height` in the screenshot response tell you the coordinate space.
- When you click/move using coordinates, the tool automatically maps them back to the real screen.
- `get_cursor_position` and `get_screen_size` also return values in image space.

This means: **use the coordinates as you see them in the screenshot image directly.** No manual scaling needed.

## Cursor Crosshair

Screenshots include a **red crosshair** marking the current cursor position. Use it to:

- Verify your last click landed where you intended.
- If the crosshair is far from your target, adjust coordinates proportionally to the distance — use large adjustments first, then refine.
- Consider the display dimensions when estimating positions (e.g. if something is 90% to the bottom, the y-coordinate should be ~90% of `image_height`).

## Usage Guidelines

- **Always take a screenshot first** before clicking — you need to see where elements are on screen.
- **Prefer keyboard shortcuts** over clicking when possible (faster and more reliable).
- **Click center of elements** — don't click edges of buttons or icons.
- **Wait and re-screenshot** if an action doesn't seem to take effect. GUI apps may need time to respond (a 1-second delay is built into screenshots).
- **All output is JSON** — parse it to check for `"ok": true` or `"error"` fields.
- **Check the crosshair** after clicking to verify accuracy. If it missed, adjust.
- Coordinates outside screen bounds will return an error.

## Workflow Example

A typical interaction looks like:

1. `vnc connect <host>::<port>` — establish connection
2. `vnc get_screenshot -o /tmp/screen.png` — see what's on screen (also gives you image dimensions)
3. Read the screenshot to identify UI elements and their coordinates
4. `vnc left_click <x> <y>` — click on the target element
5. `vnc get_screenshot -o /tmp/screen.png` — verify the result, check crosshair position
6. If crosshair missed the target, adjust coordinates and retry
7. Repeat steps 3-6 as needed
8. `vnc disconnect` — when done
