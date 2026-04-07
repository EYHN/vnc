# vnc-computer-use

VNC Computer-Use CLI - control a remote desktop via VNC for AI computer-use agents.

## Installation

```bash
pip install vnc-computer-use
```

### Claude Code Skill

To add VNC desktop control as a skill for [Claude Code](https://claude.ai/claude-code):

```bash
npx skills add EYHN/vnc --skill vnc
```

This installs the `/vnc` skill, which teaches Claude how to connect to VNC servers, take screenshots, click, type, and interact with remote desktops autonomously.

## Usage

### Connect to a VNC server

```bash
vnc connect localhost::5900
vnc connect myhost:0 --password secret
vnc connect myhost:0 --username user --password pass  # macOS Screen Sharing
```

### Interact with the remote desktop

```bash
# Screenshots
vnc get_screenshot -o screen.png
vnc get_screenshot  # returns base64 JSON

# Keyboard
vnc key enter
vnc key ctrl-c
vnc type "hello world"

# Mouse
vnc left_click 100 200
vnc right_click 300 400
vnc double_click 100 200
vnc mouse_move 500 500
vnc left_click_drag 100 200
vnc scroll down 100 200

# Info
vnc get_cursor_position
vnc get_screen_size
vnc status
```

### Multi-session support

```bash
vnc connect host1::5900 --session work
vnc connect host2::5900 --session personal
vnc -s work get_screenshot -o work.png
vnc -s personal left_click 100 200
```

### Disconnect

```bash
vnc disconnect
vnc disconnect --session work
```

## How it works

`vnc` launches a background daemon process that maintains a persistent VNC connection via [vncdotool](https://github.com/sibson/vncdotool). The CLI communicates with the daemon over a Unix domain socket, making each action fast without reconnection overhead.
