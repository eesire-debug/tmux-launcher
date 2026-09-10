# 🔗 Tmux Launcher

> A GUI launcher for remote tmux sessions — turn `ssh tmux ls` into clickable cards.

A local GTK3 app: manage servers on the left, auto-discover every tmux session
on the right, **double-click to attach**. Jump hosts, password auth, embedded
terminal tabs, load dashboard, startup templates — all included.

🌐 **Language / 语言**: [🇨🇳 中文](./README.md) · [🇬🇧 English](#-tmux-launcher)

![screenshot-placeholder](docs/screenshot.png)

---

### ✨ Features

| Area | Capability |
| --- | --- |
| 🖥 **Server management** | Add / edit / delete: display name, host, user, port, password, jump (one-hop gateway) |
| 🔍 **tmux auto-discovery** | Background `ssh tmux ls` for every session; **auto-refresh every 30 s** on the selected server |
| 🖱 **One-click connect** | Double-click a session card → opens in a ptyxis / gnome-terminal tab; tab-mode vs new-window toggle |
| 📑 **Tab mode** | Header-bar toggle: on = reuse current terminal window's tab; off = new window each time |
| ➕ **Create session** | GUI form for new tmux session with optional **startup command** and reusable **templates** |
| 📊 **Load dashboard** | CPU · Mem · Disk · Load · Net up/down · GPU · ping · uptime — **refreshes every 10 s** |
| 💻 **Embedded terminal** | Right-side VTE terminal tab — run `top` / `mytop` / `tail -f` without leaving the app, **live + interactive** |
| 📁 **SFTP** | Open Nautilus or your file manager at the server root, or hand off a `sftp://` URL |
| 📋 **Copy command** | One-click copy of attach / ssh command to clipboard; paste anywhere |
| 🪓 **Kill session** | Right-click any session → `tmux kill-session` (single or all) |
| 🚇 **Jump / Gateway** | One-hop `ssh -J` nesting, password or key auth (asks askpass when needed) |
| 🔐 **askpass** | Per-host password remembered; non-interactive logins even without an SSH agent |

---

### 📦 Install

**Dependencies**

```bash
# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 ptyxis
# Fedora
sudo dnf install python3-gobject gtk3 vte291 ptyxis
# Arch
sudo pacman -S python-gobject gtk3 vte3 ptyxis
```

> `ptyxis` can be replaced by `gnome-terminal` or any `x-terminal-emulator`.
> The embedded terminal tab needs `gir1.2-vte-2.91` (gracefully degrades when missing — the button is hidden).

**Run**

```bash
git clone https://github.com/eesire-debug/tmux-launcher.git
cd tmux-launcher
chmod +x run.sh
./run.sh                    # logged launch (log: /tmp/tmux-launcher.log)
```

Or simply:

```bash
python3 tmux_launcher.py
```

**Desktop entry**

```bash
ln -sf "$PWD/io.tmux.launcher.desktop" ~/Desktop/io.tmux.launcher.desktop
cp io.tmux.launcher.desktop ~/.local/share/applications/
mkdir -p ~/.local/share/icons/hicolor/256x256/apps
cp tmux-launcher-256.png ~/.local/share/icons/hicolor/256x256/apps/tmux-launcher.png
```

Search for **Tmux Launcher** in GNOME Activities afterwards.

---

### 🚀 Usage

**First launch**

1. Click **➕ Add Server** in the header bar:
   - **Display name**: the name shown in the list (any string)
   - **Host**: IP or domain
   - **User**: empty → use the local `$USER`
   - **Port**: default 22
   - **Password**: empty → use SSH key; filled → goes through askpass
   - **Gateway**: pick any other configured server as `-J` jump host
2. Click the server you just added → right side starts probing `tmux ls` automatically
3. Double-click any session card → attaches in your terminal
4. Toggle **📑 Tab Mode** in the header: on = reuse the current terminal window's tab; off = new window every time

**Common operations**

| Want to… | Do this |
| --- | --- |
| Connect to an existing session | Double-click the session card on the right |
| Create a new session | **➕ New tmux session** on the right; fill name + startup command; save as template |
| Run an ad-hoc command | **📟 Run command** in the header, or right-click server → "Run command" → dialog → runs in right-side terminal tab |
| Open SFTP | Right-click server → "SFTP" |
| Copy ssh command | Right-click session → "Copy connect command" |
| Kill a session | Right-click session → "Delete session" |
| Server unreachable | Look for the **✗ unreachable** badge; click ▶ to retry manually |

**Configuration file**

```text
~/.config/tmux-launcher/
├── sessions.json   # Servers & templates (contains password fields — never commit to a public repo)
└── askpass.py      # Auto-generated password helper, matched per host
```

`sessions.json` example:

```json
{
  "prefs": { "tab": true },
  "servers": [
    {
      "name": "prod-1",
      "host": "10.0.0.5",
      "user": "ops",
      "port": 22,
      "password": "",
      "jump": "",
      "templates": [
        { "name": "claude", "startup": "claude" },
        { "name": "logs",  "startup": "tail -f /var/log/syslog" }
      ]
    },
    {
      "name": "dmz-1",
      "host": "1.2.3.4",
      "user": "admin",
      "port": 2222,
      "password": "",
      "jump": "prod-1"
    }
  ]
}
```

> 🛡 `.gitignore` already excludes `sessions.json` and `askpass.py` —
> local config with passwords will never enter git history.

---

### 🧠 Design choices

- **Zero server-side**: pure client. Only uses `ssh` and tmux's built-in commands (attach / new-session / kill-session). No agent installed on remote hosts.
- **Probe and connect are decoupled**: probing uses `BatchMode=yes` + `-T` for silent, time-bounded scans; connecting goes through your terminal. Even if probing fails on auth, you can still right-click the server → "New session" or "SFTP".
- **askpass exact host match**: `SSH_ASKPASS=askpass.py` matches ssh's prompt (`user@host's password:`) exactly. Each hop in a multi-hop chain uses its own password (v3 fixed a bug where the target hop would get the wrong password when two hops used different ones).
- **Embedded vs external terminal**: the embedded tab uses VTE (`ssh -tt`) and is great for live output; for real work you'll still prefer the external tab (when Tab Mode is on) — full keybindings, theming, copy/paste.
- **Minimal i18n scaffolding**: a built-in `LANGS` dict + `t()` function with English-only strings. Change `LANG = "en"` at the top of `tmux_launcher.py` to `"zh"` to switch instantly without any restart.

---

### 🐞 Troubleshooting

| Symptom | Fix |
| --- | --- |
| `.desktop` double-click does nothing | `cat /tmp/tmux-launcher.log` to see stderr |
| Terminal won't open | Install `ptyxis` or `gnome-terminal`; otherwise it falls back to `x-terminal-emulator` |
| Probe says "tmux not installed" | Install tmux on the remote: `apt install tmux` / `yum install tmux` |
| Jump host fails to connect | Make sure the jump host itself is added and **reachable via SSH** |
| Wrong password | Re-edit the server and re-enter the password; askpass never replays stale passwords |
| Embedded terminal tab button is missing | Install `gir1.2-vte-2.91`; only that feature degrades, everything else still works |
| Want to switch languages | UI is locked to English; change `LANG = "en"` at the top of `tmux_launcher.py` to `"zh"` |

---

### 🗺 Version history

- **v8** — Right-side tabbed embedded terminal (VTE, full-height); run arbitrary commands live & interactively
- **v7** — tmux tab title changed to `session@host`
- **v6** — Removed hardcoded default server; tab mode for gnome-terminal; new `.desktop` file
- **v5** — Fixed jump-host chaining; two-column session list; GPU / Net / ping on cards
- **v4** — Merged server list and load dashboard into left-side cards
- **v3** — Load dashboard (10s) + SSH one-hop jump + askpass v2 + gateway UI
- **v2** — Two-pane server management + tmux probing + tab mode + password auth + SFTP

---

### 📄 License

MIT
