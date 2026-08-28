#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tmux 连接器 — 图形化服务器管理 + 自动探测远程 tmux 会话，点击即连。

- 左侧: 服务器列表，可在界面上增删改 (名称/地址/用户名/端口)
- 右侧: 选中服务器后自动探测 (ssh tmux ls) 全部 tmux 实例，双击即 attach
- 服务器无 tmux 实例时，可一键新建会话（可带启动命令，支持存为模板复用）
- 每 30 秒自动重新探测选中的服务器
- 配置: ~/.config/tmux-launcher/sessions.json
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import warnings

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "tmux-launcher")
CONFIG_FILE = os.path.join(CONFIG_DIR, "sessions.json")
DEFAULT_HOST = "103.95.70.250"
AUTO_RESCAN_MS = 30000
LOAD_REFRESH_MS = 10_000   # 负荷刷新周期

CSS = b"""
.dot-ok     { color: #2ecc71; }
.dot-empty  { color: #95a5a6; }
.dot-down   { color: #e74c3c; }
.dot-notmux { color: #f39c12; }
.srv-card   { padding: 8px 10px; border: 1px solid #e3e6e8; border-radius: 8px;
              background: #fbfcfd; }
.srv-card-down { padding: 8px 10px; border: 1px solid #e3e6e8; border-radius: 8px;
              background: #f6f6f6; opacity: 0.75; }
list row:selected box.srv-card { background: #eef5ff; border-color: #5b9bd5; }
list row:selected box.srv-card-down { background: #eef5ff; border-color: #5b9bd5; opacity: 0.9; }
.load-name  { font-weight: bold; }
.load-mut   { color: #7f8c8d; }
.sess-card  { padding: 8px 10px; border: 1px solid #e3e6e8; border-radius: 8px;
              background: #fbfcfd; }
flowbox { background: transparent; }
flowboxchild { padding: 3px; }
flowboxchild:selected box.sess-card { background: #eef5ff; border-color: #5b9bd5; }
list.srvlist { padding: 0; }
list.srvlist row { padding: 0; min-height: 0; }   /* full-width cards, no row padding */
.bar-track  { background: #ecf0f1; border-radius: 4px; min-height: 8px; }
.bar-ok     { background: #2ecc71; border-radius: 4px; min-height: 8px; }
.bar-warn   { background: #f39c12; border-radius: 4px; min-height: 8px; }
.bar-high   { background: #e67e22; border-radius: 4px; min-height: 8px; }
.bar-crit   { background: #e74c3c; border-radius: 4px; min-height: 8px; }
"""

# ---------------- 配置 ----------------

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "servers" in data:
                if "prefs" not in data:
                    data["prefs"] = {"tab": True}
                    save_config(data)
                return data
            if isinstance(data, dict) and "sessions" in data:   # v1 → v2 迁移
                servers = {}
                for s in data["sessions"]:
                    key = (s.get("host") or "", s.get("user") or "", s.get("port") or 22)
                    if key not in servers:
                        servers[key] = {"name": s.get("host") or DEFAULT_HOST,
                                        "host": s.get("host") or DEFAULT_HOST,
                                        "user": s.get("user") or "",
                                        "port": int(s.get("port") or 22),
                                        "templates": []}
                    if s.get("startup", "").strip():
                        servers[key]["templates"].append({"name": s["name"], "startup": s["startup"]})
                cfg = {"prefs": {"tab": True}, "servers": list(servers.values())}
                save_config(cfg)
                return cfg
        except Exception:
            pass
    cfg = {"prefs": {"tab": True},
           "servers": [{"name": DEFAULT_HOST, "host": DEFAULT_HOST,
                        "user": "", "port": 22, "templates": []}]}
    save_config(cfg)
    return cfg


ASKPASS = os.path.join(CONFIG_DIR, "askpass.py")


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)          # 含明文密码，目录收紧
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(CONFIG_FILE, 0o600)
    ensure_askpass()


ASKPASS_VERSION = "v2"


def ensure_askpass():
    """生成 SSH_ASKPASS 辅助脚本：从 ssh 密码提示文本提取 user@host，
    按 TL_TARGETS(多目标空格分隔，支持 -J 跳板) 匹配配置里的密码。
    密码不进命令行/环境；文件带版本标记，升级时自动重写。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    content = '''#!/usr/bin/env python3
# %s
import json, os, re, sys
cfg_path = os.environ.get("TL_CONFIG", os.path.expanduser("~/.config/tmux-launcher/sessions.json"))
try:
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    sys.exit(1)
prompt = sys.argv[1] if len(sys.argv) > 1 else ""
targets = os.environ.get("TL_TARGETS", "").split()
m = re.search(r"([\\w.-]+@)?([\\w.-]+)(?::\\d+)?", prompt)
if m:
    targets.append((m.group(1) or "") + m.group(2))
    if m.group(2):
        targets.append(m.group(2))
def match(t):
    for s in cfg.get("servers", []):
        full = ("%%s@%%s" %% (s["user"], s["host"])) if s.get("user") else s["host"]
        if t in (full, s["host"]) and s.get("password"):
            return s["password"]
    return None
for t in targets:
    if not t:
        continue
    pw = match(t)
    if pw:
        sys.stdout.write(pw)
        sys.exit(0)
sys.exit(1)
''' % ASKPASS_VERSION
    if os.path.exists(ASKPASS):
        with open(ASKPASS, encoding="utf-8") as f:
            if ASKPASS_VERSION in f.read(200):
                return ASKPASS
    with open(ASKPASS, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(ASKPASS, 0o700)
    return ASKPASS


def target_of(srv):
    user = srv.get("user") or os.environ.get("USER", "")
    return "%s@%s" % (user, srv["host"]) if user else srv["host"]


def port_of(srv):
    try:
        return int(srv.get("port") or 22)
    except (TypeError, ValueError):
        return 22


def _jump_srv(srv, cfg):
    """返回该服务器配置的网关服务器 dict（不存在/空返回 None）。
    兼容旧版对话框误把显示前缀 '↪ ' 存进 jump 的脏数据。"""
    name = (srv.get("jump") or "").strip()
    if name.startswith("↪"):
        name = name[1:].strip()
    if not name or name == (srv.get("name") or "").strip():
        return None
    for s in cfg.get("servers", []):
        if s["name"] == name:
            return s
    return None


def _jump_args(srv, cfg):
    """ssh -J 参数（一层嵌套穿梭）：[\"-J\", \"user@host\"]，无网关返回 []。"""
    j = _jump_srv(srv, cfg)
    if not j:
        return []
    port = port_of(j)
    target = target_of(j)
    return ["-J", ("%s:%d" % (target, port)) if port != 22 else target]


def jump_display(srv, cfg):
    """界面用的网关描述串，无网关返回空串。"""
    j = _jump_srv(srv, cfg)
    if not j:
        return ""
    return "via %s" % j["name"]



# ---------------- 命令构建 ----------------

def remote_cmd(session, startup):
    """服务器上执行的命令：有会话就 attach，没有就新建（含启动脚本则执行）。"""
    q = shlex.quote
    if startup and startup.strip():
        script = startup.strip() + "\nexec bash"   # 脚本跑完后留在交互 shell
        inner = q("bash -lc " + q(script))
        return ("tmux has-session -t %s 2>/dev/null && exec tmux attach -t %s "
                "|| exec tmux new-session -s %s %s") % (q(session), q(session), q(session), inner)
    return "tmux new-session -A -s %s" % q(session)


def _ssh_opts(srv):
    """ssh 选项串；有密码时走 askpass 密码认证（禁 pubkey 避免双认证干扰）。"""
    opts = ["-o ServerAliveInterval=30", "-o ServerAliveCountMax=3",
            "-o StrictHostKeyChecking=accept-new"]
    if srv.get("password"):
        opts += ["-o PreferredAuthentications=password", "-o PubkeyAuthentication=no",
                 "-o NumberOfPasswordPrompts=1"]
    return " ".join(opts)


def _env_prefix(srv, cfg=None):
    """有密码时的环境变量前缀（放命令前）；无密码返回空串走密钥。
    带网关时 TL_TARGETS 含两跳主机，askpass 按 ssh 提示里的 host 匹配。"""
    if not srv.get("password") and not (cfg and _jump_srv(srv, cfg)):
        return ""
    targets = target_of(srv)
    j = _jump_srv(srv, cfg) if cfg else None
    if j:
        targets = "%s %s" % (target_of(j), targets)
    return ("TL_TARGETS=%s TL_CONFIG=%s SSH_ASKPASS=%s SSH_ASKPASS_REQUIRE=force "
            % (shlex.quote(targets), shlex.quote(CONFIG_FILE), shlex.quote(ASKPASS)))


def attach_shell_string(srv, session, startup="", cfg=None):
    """本地终端整段执行的 shell：ssh → tmux (attach 或新建)，断线后窗口保留。"""
    remote = remote_cmd(session, startup)
    cmd = ("%sssh -t %s -p %d %s %s %s"
           % (_env_prefix(srv, cfg), " ".join(_jump_args(srv, cfg)), port_of(srv),
              _ssh_opts(srv), shlex.quote(target_of(srv)), shlex.quote(remote)))
    return cmd + '; echo; read -p "已断开，回车关闭窗口..."; exit'


def ssh_login_shell_string(srv, cfg=None):
    cmd = ("%sssh -t %s -p %d %s %s"
           % (_env_prefix(srv, cfg), " ".join(_jump_args(srv, cfg)), port_of(srv),
              _ssh_opts(srv), shlex.quote(target_of(srv))))
    return cmd + '; echo; read -p "已断开，回车关闭窗口..."; exit'


def copy_attach_cmd(srv, session, cfg=None):
    remote = remote_cmd(session, "")
    return ("%sssh -t %s -p %d %s %s %s"
            % (_env_prefix(srv, cfg), " ".join(_jump_args(srv, cfg)), port_of(srv),
               _ssh_opts(srv), shlex.quote(target_of(srv)), shlex.quote(remote)))


def sftp_url(srv):
    """文件管理器用的 sftp:// URL（不支持跳板，直连场景用）。"""
    user = srv.get("user") or os.environ.get("USER", "")
    u = "%s@" % urllib.parse.quote(user) if user else ""
    port = port_of(srv)
    return "sftp://%s%s:%d/" % (u, srv["host"], port) if port != 22 else "sftp://%s%s/" % (u, srv["host"])


def sftp_cli_args(srv, cfg=None):
    """sftp 命令行主体（含 -J 跳板、端口、askpass 环境变量前缀）。"""
    opts = _ssh_opts(srv)
    if port_of(srv) != 22:
        opts += " -o Port=%d" % port_of(srv)
    return ("%ssftp %s %s %s"
            % (_env_prefix(srv, cfg), " ".join(_jump_args(srv, cfg)),
               opts, shlex.quote(target_of(srv))))


def sftp_shell_string(srv, cfg=None):
    """ptyxis 标签页里运行的 sftp 终端命令（复用 askpass 自动密码，支持 -J 跳板）。"""
    return (sftp_cli_args(srv, cfg)
            + '; echo; read -p "已断开，回车关闭窗口..."; exit')


def ptyxis_argv(shell_string, title, tab=True):
    """构造 ptyxis 启动参数：tab=True 用 --tab(进现有窗口)，否则 --new-window。"""
    argv = ["ptyxis"]
    argv.append("--tab" if tab else "--new-window")
    argv += ["-T", title, "--", "bash", "-c", shell_string]
    return argv


def launch_terminal(shell_string, title, tab=True):
    """用 ptyxis 执行 shell_string；tab=True 在现有窗口开新标签页（无窗口则新建）。
    找不到终端返回 None。"""
    term = shutil.which("ptyxis") or shutil.which("x-terminal-emulator")
    if not term:
        return None
    if os.path.basename(term) == "ptyxis":
        argv = ptyxis_argv(shell_string, title, tab)
    else:
        argv = [term, "-e", "sh", "-c", shell_string]
    subprocess.Popen(argv)
    return argv


def run_ssh_quiet(srv, remote, cfg=None):
    """非交互执行一条远端命令（探测/终止/负荷等）。有密码时用 askpass 免密，否则 BatchMode 走密钥。
    cfg 提供时支持 -J 一层跳板。"""
    argv = ["ssh"]
    jump = _jump_args(srv, cfg)
    if jump:
        argv += jump
    argv += ["-p", str(port_of(srv)),
             "-o", "ConnectTimeout=6", "-o", "ServerAliveInterval=15",
             "-o", "StrictHostKeyChecking=accept-new"]
    env = None
    j = _jump_srv(srv, cfg) if cfg else None
    # 最后一跳的认证策略由目标机自己决定：有密码走 askpass(禁 pubkey)，
    # 无密码走密钥 BatchMode(绝不弹提示)。-o 选项不会泄漏到 -J 网关跳
    # （OpenSSH 10.2 实测：隐式 proxy 只继承 -l/-v）。
    if srv.get("password") or (j and j.get("password")):
        targets = target_of(srv)
        if j:
            targets = "%s %s" % (target_of(j), targets)
        env = dict(os.environ, TL_TARGETS=targets, TL_CONFIG=CONFIG_FILE,
                   SSH_ASKPASS=ASKPASS, SSH_ASKPASS_REQUIRE="force")
    if srv.get("password"):
        argv += ["-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
                 "-o", "NumberOfPasswordPrompts=1"]
    else:
        argv += ["-o", "BatchMode=yes"]
    argv += [target_of(srv), remote]
    return subprocess.run(argv, capture_output=True, text=True, timeout=15, env=env)


# ---------------- 负荷探测 ----------------

# 一次性取 load/内存/磁盘/在线时长/核数/网络/显卡。
# 输出格式（每行一个标签，行尾必须带 \n）:
#   L|1|5|15  M|TOTAL|USED|AVAIL  D|USED|TOTAL  U|MIN  C|N
#   N|RX_KBPS|TX_KBPS            （非 lo/veth/docker 接口 1 秒采样和）
#   G|UTIL|MEMU_MIB|MEMT_MIB|NAME （NVIDIA，每卡一行）/ G|lspci|NAME;NAME…（无驱动时兜底）
_LOAD_CMD = (
    "awk '{printf \"L|%s|%s|%s\\n\",$1,$2,$3}' /proc/loadavg 2>/dev/null; "
    "free -m 2>/dev/null | awk '/^Mem:/{printf \"M|%d|%d|%d\\n\",$2,$3,$7}'; "
    "df -m / 2>/dev/null | awk 'NR==2{printf \"D|%d|%d\\n\",$3,$2}'; "
    "awk '{print \"U|\" int($1/60)}' /proc/uptime 2>/dev/null; "
    "nproc 2>/dev/null | awk '{print \"C|\" $1}'; "
    "s1=$(awk 'NR>2{sub(/:/,\"\",$1); if($1!=\"lo\" && $1 !~ /^(veth|docker|br-|virbr)/){r+=$2;t+=$9}}END{print r+0, t+0}' /proc/net/dev 2>/dev/null); "
    "sleep 1; "
    "s2=$(awk 'NR>2{sub(/:/,\"\",$1); if($1!=\"lo\" && $1 !~ /^(veth|docker|br-|virbr)/){r+=$2;t+=$9}}END{print r+0, t+0}' /proc/net/dev 2>/dev/null); "
    "awk -v a=\"$s1\" -v b=\"$s2\" 'BEGIN{split(a,x,\" \"); split(b,y,\" \"); if(y[1]+0>=x[1]+0 && y[2]+0>=x[2]+0) printf \"N|%d|%d\\n\",(y[1]-x[1])/1024,(y[2]-x[2])/1024}'; "
    "if command -v nvidia-smi >/dev/null 2>&1; then "
    "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,name --format=csv,noheader,nounits 2>/dev/null | head -16 | "
    "while IFS=',' read -r u mu mt n; do "
    "u=$(printf '%s' \"$u\" | tr -d ' ,'); mu=$(printf '%s' \"$mu\" | tr -d ' ,'); mt=$(printf '%s' \"$mt\" | tr -d ' ,'); "
    "n=$(printf '%s' \"$n\" | sed 's/^ *//'); "
    "[ -n \"$u\" ] && printf 'G|%s|%s|%s|%s\\n' \"$u\" \"$mu\" \"$mt\" \"$n\"; done; "
    "else "
    "g=$(lspci 2>/dev/null | grep -iE 'vga|3d controller|display controller' | head -4 | sed -E 's/^[0-9a-fA-F]{2}:[0-9a-fA-F]{2}.[0-9] +//' | cut -d'[' -f1 | sed 's/ *$//' | tr '\\n' ';'); "
    "[ -n \"$g\" ] && printf 'G|lspci|%s\\n' \"$g\"; fi; true")


def parse_load_output(text):
    """解析 _LOAD_CMD 输出 → dict(load1, load5, load15, mem_*, disk_*, up_min, cores,
    net_rx_kbps, net_tx_kbps, gpus=[...], load_pct, ok)。"""
    out = {}
    gpus = []
    for line in text.splitlines():
        p = line.strip().split("|")
        if len(p) < 2 or p[0] not in ("L", "M", "D", "U", "C", "N", "G"):
            continue
        try:
            if p[0] == "L":
                out.update(load1=float(p[1]), load5=float(p[2]), load15=float(p[3]))
            elif p[0] == "M":
                out.update(mem_total=int(p[1]), mem_used=int(p[2]), mem_avail=int(p[3]))
            elif p[0] == "D":
                out.update(disk_used=int(p[1]), disk_total=int(p[2]))
            elif p[0] == "U":
                out.update(up_min=int(p[1]))
            elif p[0] == "C":
                out.update(cores=int(p[1]))
            elif p[0] == "N":
                out.update(net_rx_kbps=int(float(p[1])), net_tx_kbps=int(float(p[2])))
            elif p[0] == "G":
                g = {}
                if p[1] == "lspci":
                    g["lspci"] = True
                    g["names"] = [x for x in p[2].split(";") if x]
                else:
                    g["util"] = int(float(p[1]))
                    g["mem_used"] = int(float(p[2]))
                    g["mem_total"] = int(float(p[3]))
                    g["name"] = p[4].strip() if len(p) > 4 else ""
                gpus.append(g)
        except (ValueError, IndexError):
            continue
    if gpus:
        out["gpus"] = gpus
    if out.get("load1") is not None and out.get("cores"):
        out["load_pct"] = out["load1"] / out["cores"] * 100.0
    out["ok"] = any(k in out for k in ("load1", "mem_total"))
    return out


def load_status(info):
    """按 load/核 比给出 (颜色class, 文字)。"""
    pct = info.get("load_pct")
    if pct is None:
        return "load-ok", "负荷正常"
    if pct < 60:
        return "load-ok", "负荷轻"
    if pct < 100:
        return "load-warn", "负荷中"
    if pct < 200:
        return "load-high", "负荷高"
    return "load-crit", "负荷过载"


def fmt_up(mins):
    if mins is None:
        return "?"
    if mins < 60:
        return "%d 分钟" % mins
    if mins < 60 * 24:
        return "%d 小时 %d 分" % (mins // 60, mins % 60)
    return "%d 天 %d 小时" % (mins // 1440, (mins % 1440) // 60)


def fmt_kbps(v):
    """KB/s → 人话 (K/s, M/s, G/s)。"""
    if v is None:
        return "?"
    v = float(v)
    if v >= 1024 * 1024:
        return "%.1fG/s" % (v / 1024.0 / 1024.0)
    if v >= 1024:
        return "%.1fM/s" % (v / 1024.0)
    return "%dK/s" % int(v)


def gpu_text(info):
    """GPU 信息 → 一行文本（NVIDIA 每卡: 名称 利用率 显存; 无驱动时 lspci 名称）。"""
    gs = info.get("gpus") or []
    if not gs:
        return "GPU —"
    parts = []
    for g in gs:
        if g.get("lspci"):
            parts.append("; ".join(g["names"]))
        else:
            nm = g.get("name") or "GPU"
            util = ("%d%%" % g["util"]) if g.get("util") is not None else "?"
            mem = ("%d/%dG" % (g.get("mem_used", 0) // 1024, g["mem_total"] // 1024)) if g.get("mem_total") else "?"
            parts.append("%s %s %s" % (nm, util, mem))
    return "GPU " + " · ".join(parts)


def net_text(info):
    if info.get("net_rx_kbps") is None:
        return "NET —"
    return "NET ↓%s ↑%s" % (fmt_kbps(info["net_rx_kbps"]), fmt_kbps(info.get("net_tx_kbps", 0)))


def ping_markup(ms):
    """⚡ 延迟：绿<500ms 橙<1500ms 红≥1500ms。"""
    if ms is None:
        return '<span size="small" fgcolor="#95a5a6">⚡ —</span>'
    color = "#2ecc71" if ms < 500 else ("#e67e22" if ms < 1500 else "#e74c3c")
    return '<span size="small" fgcolor="%s">⚡%dms</span>' % (color, ms)


def probe_load(srv, cfg=None):
    """ssh 探测服务器负荷；返回 (ok, info_dict)。info 含 rtt_ms(ssh 往返, 连通性指标)。
    失败时 info 含 msg。"""
    # 1) ping: 一次轻量 ssh 往返，测整条链路(含跳板)延迟
    rtt = None
    try:
        t0 = time.monotonic()
        pr = run_ssh_quiet(srv, "true", cfg)
        if pr.returncode == 0:
            rtt = int((time.monotonic() - t0) * 1000)
    except Exception:
        rtt = None
    # 2) 完整探测（含 1 秒网络采样窗口，整体约 1.x 秒）
    try:
        r = run_ssh_quiet(srv, _LOAD_CMD, cfg)
    except subprocess.TimeoutExpired:
        return False, {"msg": "SSH 超时", "rtt_ms": rtt}
    if r.returncode != 0:
        return False, {"msg": "SSH 失败", "rtt_ms": rtt}
    info = parse_load_output(r.stdout)
    info["rtt_ms"] = rtt
    if not info.get("ok"):
        return False, {"msg": "输出异常", "raw": r.stdout[:200], "rtt_ms": rtt}
    return True, info


# ---------------- 探测 ----------------

def parse_tmux_ls(r):
    """解析 ssh 探测结果 → (status, sessions, msg)。sessions: [{name, windows, created}]"""
    if r.returncode != 0:
        return "down", [], "SSH 失败(检查地址/用户名/密钥)"
    out = r.stdout.splitlines()
    sessions = []
    for line in out:
        if line.startswith("RC="):
            break
        parts = line.split("|")
        if len(parts) >= 3:
            try:
                ts = int(parts[2])
            except ValueError:
                ts = 0
            sessions.append({"name": parts[0], "windows": parts[1], "created": ts})
    if "RC=0" in r.stdout:
        sessions.sort(key=lambda x: -x["created"])
        return "ok", sessions, ""
    if "not found" in r.stderr.lower():
        return "notmux", [], "服务器上未安装 tmux"
    return "empty", [], "tmux 已装，但没有任何会话"


def scan_server(srv, seq, done, cfg=None):
    """后台探测服务器上的 tmux 实例: tmux ls。结果回调 GLib.idle_add(done, result)。
    cfg 提供时支持 -J 跳板。"""
    def run():
        remote = ("tmux ls -F '#{session_name}|#{session_windows}|#{session_created}' "
                  "2>/dev/null; echo RC=$?")
        try:
            r = run_ssh_quiet(srv, remote, cfg)
        except Exception:
            GLib.idle_add(done, {"srv": srv["name"], "seq": seq,
                                 "status": "down", "sessions": [], "msg": "服务器不可达"})
            return
        st, sessions, msg = parse_tmux_ls(r)
        GLib.idle_add(done, {"srv": srv["name"], "seq": seq, "status": st,
                             "sessions": sessions, "msg": msg})
    threading.Thread(target=run, daemon=True).start()


def rel_time(ts):
    if not ts:
        return "?"
    d = time.time() - ts
    if d < 60:
        return "刚刚"
    if d < 3600:
        return "%d分钟前" % int(d // 60)
    if d < 86400:
        return "%d小时前" % int(d // 3600)
    return "%d天前" % int(d // 86400)


# ---------------- GUI ----------------

class MainWindow(Gtk.Window):
    SRV_DOT = {"ok": "dot-ok", "empty": "dot-empty", "down": "dot-down", "notmux": "dot-notmux"}
    SRV_TEXT = {"ok": "● 可连接，tmux 运行中", "empty": "○ 可连接，无 tmux 会话",
                "down": "✗ 不可达", "notmux": "⚠ 未安装 tmux"}

    def __init__(self):
        super().__init__(title="🔗 Tmux 连接器")
        self.set_default_size(900, 560)
        # WM_CLASS 必须与 .desktop 的 StartupWMClass 一致，GNOME dock 才显示正确图标
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.set_wmclass("tmux-launcher", "tmux-launcher")
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmux-launcher.png")
        if os.path.exists(icon):
            try:
                self.set_icon_from_file(icon)
            except Exception:
                pass
        self.cfg = load_config()
        self.scan_seq = 0
        self.sel_srv = None            # 选中的服务器名
        self.srv_status = {}           # srv name -> status
        self.srv_rows = {}             # box -> srv name
        self.srv_widgets = {}          # srv name -> (dot, card, val, bar, info, gpu, net, ping, target)
        self.sess_widgets = {}         # box -> session dict
        self.load_running = {}         # srv name -> 是否在探测
        self.load_skip = {}            # srv name -> 上轮失败，本轮跳过（防不可达服务器闪烁）

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        hb = Gtk.HeaderBar(show_close_button=True)
        hb.set_title("🔗 Tmux 连接器")
        hb.set_subtitle("%d 台服务器 · 自动探测 tmux" % len(self.cfg["servers"]))
        self.set_titlebar(hb)

        b_add = Gtk.Button(label="➕ 添加服务器")
        b_add.connect("clicked", self.on_add_server)
        hb.pack_start(b_add)

        self.tab_btn = Gtk.ToggleButton(label="📑 标签页模式")
        self.tab_btn.set_active(bool(self.cfg.get("prefs", {}).get("tab", True)))
        self.tab_btn.set_tooltip_text("连接时在现有 ptyxis 窗口开新标签页；关闭则每次开新窗口")
        self.tab_btn.connect("toggled", self.on_tab_toggled)
        hb.pack_end(self.tab_btn)

        # ---- 左: 服务器卡片（列表+负荷看板合并，点选即显示 tmux 清单）----
        left_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        left_label = Gtk.Label(label="🖥 服务器 · 负荷", xalign=0)
        left_label.get_style_context().add_class("dim-label")
        left_bar.pack_start(left_label, True, True, 0)
        self.b_load_all = Gtk.Button(label="🔄")
        self.b_load_all.set_relief(Gtk.ReliefStyle.NONE)
        self.b_load_all.set_tooltip_text("手动刷新全部服务器负荷")
        self.b_load_all.connect("clicked", lambda *a: self.refresh_loads(force=True))
        left_bar.pack_end(self.b_load_all, False, False, 0)
        self.srv_list = Gtk.ListBox()
        self.srv_list.get_style_context().add_class("srvlist")
        self.srv_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.srv_list.connect("row-selected", self.on_srv_selected)
        self.srv_list.connect("button-press-event", self.on_srv_button_press)
        srv_sw = Gtk.ScrolledWindow()
        srv_sw.add(self.srv_list)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left.pack_start(left_bar, False, False, 0)
        left.pack_start(srv_sw, True, True, 0)
        left.set_size_request(300, -1)

        # ---- 右: 探测到的会话 ----
        self.right_info = Gtk.Label(label="请在左侧选择服务器", xalign=0)
        self.right_info.set_margin_top(6)
        self.right_info.set_margin_bottom(2)
        right_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.b_refresh = Gtk.Button(label="🔄 重新探测")
        self.b_refresh.connect("clicked", self.on_rescan)
        self.b_refresh.set_sensitive(False)
        self.b_new = Gtk.Button(label="➕ 新建 tmux 会话")
        self.b_new.connect("clicked", self.on_new_session)
        self.b_new.set_sensitive(False)
        right_bar.pack_start(self.right_info, True, True, 0)
        right_bar.pack_end(self.b_new, False, False, 0)
        right_bar.pack_end(self.b_refresh, False, False, 0)

        self.sess_flow = Gtk.FlowBox()
        self.sess_flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sess_flow.set_max_children_per_line(2)    # 双列
        self.sess_flow.set_min_children_per_line(2)
        self.sess_flow.set_homogeneous(True)
        self.sess_flow.set_valign(Gtk.Align.START)
        self.sess_flow.connect("child-activated", self.on_sess_activate)
        sess_sw = Gtk.ScrolledWindow()
        sess_sw.add(self.sess_flow)
        self.right_hint = Gtk.Label(label="", xalign=0, wrap=True)
        self.right_hint.set_margin_top(4)
        self.right_hint.set_margin_bottom(4)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right.pack_start(right_bar, False, False, 0)
        right.pack_start(sess_sw, True, True, 0)
        right.pack_start(self.right_hint, False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.pack1(left, False, False)
        paned.pack2(right, True, True)

        self.status = Gtk.Label(label="就绪 · 状态探测走 ssh 密钥登录", xalign=0)
        self.status.set_margin_top(4)
        self.status.set_margin_bottom(4)
        self.status.set_margin_start(8)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.pack_start(paned, True, True, 0)
        vbox.pack_start(self.status, False, False, 0)
        self.add(vbox)

        self.build_menus()
        self.reload_servers()
        GLib.timeout_add(AUTO_RESCAN_MS, self._auto_rescan)
        GLib.timeout_add(LOAD_REFRESH_MS, self._auto_load)
        GLib.idle_add(self._initial_select)
        self.refresh_loads(first=True)

    # ---------- 左列: 服务器 ----------

    def reload_servers(self):
        self.srv_rows = {}
        self.srv_widgets = {}
        self.srv_status = {}
        self.load_running = {}
        self.load_skip = {}
        for child in self.srv_list.get_children():
            self.srv_list.remove(child)
        for srv in self.cfg["servers"]:
            self.srv_list.add(self._make_srv_row(srv))
        self.srv_list.show_all()

    # ---------- 负荷刷新 ----------

    def _auto_load(self):
        if self.get_visible():
            self.refresh_loads()
        return True

    def refresh_loads(self, first=False, force=False):
        for srv in self.cfg["servers"]:
            name = srv["name"]
            if self.load_running.get(name):
                continue   # 上一轮还没回来，跳过
            if self.load_skip.get(name) and not force:
                self.load_skip[name] = False
                continue   # 刚失败过，下一轮再试（不可达服务器约 20s 重试一次，避免卡片闪烁）
            self.load_running[name] = True
            self._start_load_probe(srv)

    def _start_load_probe(self, srv):
        w = self.srv_widgets.get(srv["name"])
        if w:
            val_l, bar, info_l = w[2], w[3], w[4]
            val_l.set_text("…")
            info_l.set_markup('<span size="small">📊 探测中…</span>')

        def run():
            try:
                ok, info = probe_load(srv, self.cfg)
            except Exception:
                info = {"ok": False}
            GLib.idle_add(self._fill_load_card, srv["name"], info)

        threading.Thread(target=run, daemon=True).start()

    def _fill_load_card(self, name, info):
        self.load_running[name] = False
        w = self.srv_widgets.get(name)
        srv = self.srv_by_name(name)
        if not (w and srv):
            return
        dot, card, val_l, bar, info_l, gpu_l, net_l, ping_l, target_l = w
        for cls in ("srv-card", "srv-card-down"):
            card.get_style_context().remove_class(cls)
        if not info.get("ok"):
            self.load_skip[name] = True
            card.get_style_context().add_class("srv-card-down")
            bar.set_fraction(0.0)
            for cls in ("bar-ok", "bar-warn", "bar-high", "bar-crit"):
                bar.get_style_context().remove_class(cls)
            val_l.set_text("⚠ 离线")
            info_l.set_markup('<span size="small">📊 不可达</span>')
            gpu_l.set_text("")
            net_l.set_text("")
            ping_l.set_markup(ping_markup(info.get("rtt_ms")))
            return
        self.load_skip[name] = False
        card.get_style_context().add_class("srv-card")
        cls, label = load_status(info)
        pct = info.get("load_pct")
        bar.set_fraction(min(1.0, (pct or 0) / 200.0))
        bar.get_style_context().remove_class("bar-ok")
        bar.get_style_context().remove_class("bar-warn")
        bar.get_style_context().remove_class("bar-high")
        bar.get_style_context().remove_class("bar-crit")
        bar.get_style_context().add_class("bar-" + cls.split("-")[1])
        cores = info.get("cores")
        val_l.set_markup('<span size="large">%s</span>'
                         % GLib.markup_escape_text("%.1f" % (pct or 0)
                                                   + (" (/%d核)" % cores if cores else "")))
        mem = ("%d%% 内存" % (info["mem_used"] * 100.0 / info["mem_total"])) if info.get("mem_total") else ""
        disk = ("%d%% 磁盘" % (info["disk_used"] * 100.0 / info["disk_total"])) if info.get("disk_total") else ""
        parts = [label, "load %s" % info.get("load1", "?"), mem, disk, "运行 %s" % fmt_up(info.get("up_min"))]
        text = " · ".join(p for p in parts if p)
        info_l.set_markup('<span size="small">%s</span>' % GLib.markup_escape_text(text))
        gpu_l.set_markup('<span size="small">%s</span>' % GLib.markup_escape_text(gpu_text(info)))
        net_l.set_markup('<span size="small">%s</span>' % GLib.markup_escape_text(net_text(info)))
        ping_l.set_markup(ping_markup(info.get("rtt_ms")))
        card.set_tooltip_text("%s:%d\n%s\n%s\n%s" % (target_of(srv), port_of(srv),
                                                     text, gpu_text(info), net_text(info)))

    def _make_srv_row(self, srv):
        """服务器卡片（撑满栏宽）：状态点 + 名称 + 网关标记 + ping + load% + 进度条
        + load/内存/磁盘/uptime + GPU + 网络 + 目标地址。
        点选卡片 → on_srv_selected → 右侧显示该服务器的 tmux 会话清单。"""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        card.set_margin_bottom(6)    # 卡片间留白（水平方向无 margin → 撑满栏宽）
        card.get_style_context().add_class("srv-card-down")   # 首次探测成功后转正常态

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        dot = Gtk.Label(label="●")
        dot.get_style_context().add_class("dot-empty")
        dot.set_tooltip_text("尚未探测")
        name_l = Gtk.Label(label="", xalign=0)
        name_l.get_style_context().add_class("load-name")
        name_l.set_markup("<b>%s</b>" % GLib.markup_escape_text(srv["name"]))
        top.pack_start(dot, False, False, 0)
        top.pack_start(name_l, True, True, 0)
        val_l = Gtk.Label(label="—", xalign=1)
        val_l.get_style_context().add_class("load-mut")
        top.pack_end(val_l, False, False, 0)
        ping_l = Gtk.Label(label="", xalign=1)
        ping_l.set_tooltip_text("ssh 往返延迟(含跳板链路)")
        top.pack_end(ping_l, False, False, 0)
        via = jump_display(srv, self.cfg)
        if via:
            vl = Gtk.Label(label="↪", xalign=0)
            vl.get_style_context().add_class("load-mut")
            vl.set_tooltip_text("经网关: %s" % via)
            top.pack_end(vl, False, False, 0)

        bar = Gtk.ProgressBar()
        bar.set_show_text(False)
        bar.set_fraction(0.0)

        info_l = Gtk.Label(label="", xalign=0)
        info_l.get_style_context().add_class("load-mut")
        info_l.set_markup('<span size="small">📊 探测中…</span>')
        gpu_l = Gtk.Label(label="", xalign=0)
        gpu_l.get_style_context().add_class("load-mut")
        net_l = Gtk.Label(label="", xalign=0)
        net_l.get_style_context().add_class("load-mut")
        target_l = Gtk.Label(label="", xalign=0)
        target_l.get_style_context().add_class("load-mut")
        target_l.set_markup('<span size="small" fgcolor="#95a5a6">%s</span>'
                            % GLib.markup_escape_text("%s:%d" % (target_of(srv), port_of(srv))))

        card.pack_start(top, False, False, 0)
        card.pack_start(bar, False, False, 0)
        card.pack_start(info_l, False, False, 0)
        card.pack_start(gpu_l, False, False, 0)
        card.pack_start(net_l, False, False, 0)
        card.pack_start(target_l, False, False, 0)
        self.srv_rows[card] = srv["name"]
        self.srv_widgets[srv["name"]] = (dot, card, val_l, bar, info_l, gpu_l, net_l, ping_l, target_l)
        return card

    def srv_by_name(self, name):
        for s in self.cfg["servers"]:
            if s["name"] == name:
                return s
        return None

    def on_srv_selected(self, listbox, row):
        if row is None:
            return
        name = self.srv_rows.get(row.get_child())
        self.sel_srv = name
        srv = self.srv_by_name(name)
        if not srv:
            return
        self.right_info.set_text("🖥 %s · %s:%d" % (name, target_of(srv), port_of(srv)))
        self.b_refresh.set_sensitive(True)
        self.b_new.set_sensitive(True)
        self._scan(srv)

    def on_srv_button_press(self, widget, event):
        if event.button == 3:
            row = self.srv_list.get_row_at_y(int(event.y))
            if row is not None:
                self.srv_list.select_row(row)
                self.srv_menu.popup(None, None, None, None, event.button, event.time)
        return False

    # ---------- 探测 ----------

    def _scan(self, srv):
        self.scan_seq += 1
        seq = self.scan_seq
        self.right_hint.set_text("🔄 正在探测 %s 上的 tmux 实例..." % srv["name"])
        scan_server(srv, seq, self.on_scan_result, self.cfg)

    def on_rescan(self, *a):
        if self.sel_srv:
            self._scan(self.srv_by_name(self.sel_srv))

    def _auto_rescan(self):
        if self.sel_srv and self.get_visible():
            self._scan(self.srv_by_name(self.sel_srv))
        return True

    def on_scan_result(self, result):
        if result["seq"] != self.scan_seq:
            return   # 过期结果，忽略
        srv_name = result["srv"]
        st = result["status"]
        self.srv_status[srv_name] = st
        w = self.srv_widgets.get(srv_name)
        if w:
            dot = w[0]
            for cls in self.SRV_DOT.values():
                dot.get_style_context().remove_class(cls)
            dot.get_style_context().add_class(self.SRV_DOT[st])
            dot.set_tooltip_text(self.SRV_TEXT[st])
        if srv_name != self.sel_srv:
            return
        self._fill_sessions(srv_name, st, result["sessions"], result["msg"])

    def _fill_sessions(self, srv_name, st, sessions, msg):
        for child in self.sess_flow.get_children():
            self.sess_flow.remove(child)
        self.sess_widgets = {}
        if st == "ok":
            for sess in sessions:
                self.sess_flow.add(self._make_sess_row(sess))
            self.sess_flow.show_all()
            self.right_hint.set_text("双击会话即连接 · 右键更多操作 · 每 30 秒自动刷新")
            self.status.set_text("🔍 %s: 探测到 %d 个 tmux 会话" % (srv_name, len(sessions)))
        else:
            self.sess_flow.show_all()
            self.right_hint.set_text("ℹ️ %s — %s。点「➕ 新建 tmux 会话」创建。" % (msg, srv_name))
            self.status.set_text("🔍 %s: %s" % (srv_name, msg))

    def _make_sess_row(self, sess):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.get_style_context().add_class("sess-card")
        dot = Gtk.Label(label="●")
        dot.get_style_context().add_class("dot-ok")
        dot.set_tooltip_text("运行中")
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        tl = Gtk.Label(label="", xalign=0)
        tl.set_markup("<b>%s</b>" % GLib.markup_escape_text(sess["name"]))
        sl = Gtk.Label(label="", xalign=0)
        sl.set_markup('<span size="small" fgcolor="#7f8c8d">%s</span>'
                      % GLib.markup_escape_text("%s 个窗口 · 创建于 %s"
                                                % (sess["windows"], rel_time(sess["created"]))))
        v.pack_start(tl, False, False, 0)
        v.pack_start(sl, False, False, 0)
        box.pack_start(dot, False, False, 0)
        box.pack_start(v, True, True, 0)
        box.connect("button-press-event", self._on_sess_card_press)
        self.sess_widgets[box] = sess
        return box

    def on_sess_activate(self, flowbox, child):
        """child-activated: 双击或回车连接。child 是 FlowBoxChild，其子节点才是卡片 box。"""
        sess = self.sess_widgets.get(child.get_child())
        if sess:
            self._connect(sess)

    def _on_sess_card_press(self, box, event):
        """卡片自身的右键 → 选中该卡并弹菜单。FlowBox 没有 get_child_at，
        所以把事件绑在卡片上；box 的父节点就是包裹它的 FlowBoxChild。"""
        if event.button == 3:
            parent = box.get_parent()
            if parent is not None:
                self.sess_flow.select_child(parent)
            self.sess_menu.popup(None, None, None, None, event.button, event.time)
            return True
        return False

    # ---------- 动作 ----------

    def _tab_mode(self):
        return bool(self.cfg.get("prefs", {}).get("tab", True))

    def on_tab_toggled(self, btn):
        self.cfg.setdefault("prefs", {})["tab"] = btn.get_active()
        save_config(self.cfg)
        self.status.set_text("📑 标签页模式: %s" % ("开" if btn.get_active() else "关（新窗口）"))

    def _connect(self, sess):
        srv = self.srv_by_name(self.sel_srv)
        if not srv:
            return
        argv = launch_terminal(attach_shell_string(srv, sess["name"], cfg=self.cfg),
                               "tmux: %s" % sess["name"], tab=self._tab_mode())
        if argv is None:
            self.status.set_text("⚠ 未找到终端模拟器，请手动执行: " + copy_attach_cmd(srv, sess["name"], self.cfg))
        else:
            self.status.set_text("🔌 正在打开终端连接 %s → tmux %s ..." % (srv["name"], sess["name"]))

    def on_connect_ssh(self, *a):
        srv = self.srv_by_name(self.sel_srv)
        if srv:
            launch_terminal(ssh_login_shell_string(srv, self.cfg), "ssh: %s" % srv["name"],
                            tab=self._tab_mode())
            self.status.set_text("🔌 正在打开 SSH 终端 %s ..." % srv["name"])

    def on_sftp_terminal(self, *a):
        srv = self.srv_by_name(self.sel_srv)
        if srv:
            launch_terminal(sftp_shell_string(srv, self.cfg), "sftp: %s" % srv["name"],
                            tab=self._tab_mode())
            self.status.set_text("📂 正在打开 SFTP 终端 %s ..." % srv["name"])

    def on_sftp_fm(self, *a):
        srv = self.srv_by_name(self.sel_srv)
        if not srv:
            return
        url = sftp_url(srv)
        launcher = shutil.which("gio") or shutil.which("nautilus") or shutil.which("nemo") \
            or shutil.which("thunar") or shutil.which("dolphin")
        if not launcher:
            self.status.set_text("⚠ 未找到文件管理器/gio，请手动打开 %s" % url)
            return
        if os.path.basename(launcher) == "gio":
            subprocess.Popen([launcher, "open", url])
        else:
            subprocess.Popen([launcher, url])
        self.status.set_text("📁 已请求打开 %s" % url)

    def on_copy_sess_cmd(self, *a):
        sess = self._selected_sess()
        srv = self.srv_by_name(self.sel_srv)
        if sess and srv:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(copy_attach_cmd(srv, sess["name"], self.cfg), -1)
            self.status.set_text("📋 已复制: " + copy_attach_cmd(srv, sess["name"], self.cfg))

    def on_copy_ssh_cmd(self, *a):
        srv = self.srv_by_name(self.sel_srv)
        if srv:
            cmd = ssh_login_shell_string(srv, self.cfg).split("; echo")[0]
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(cmd, -1)
            self.status.set_text("📋 已复制 ssh 登录命令")

    def on_kill_session(self, *a):
        sess = self._selected_sess()
        srv = self.srv_by_name(self.sel_srv)
        if not (sess and srv):
            return
        md = Gtk.MessageDialog(transient_for=self, modal=True,
                               message_type=Gtk.MessageType.QUESTION,
                               buttons=Gtk.ButtonsType.YES_NO,
                               text="终止 tmux 会话「%s」?" % sess["name"],
                               secondary_text="执行 tmux kill-session -t %s，会话内所有进程都会被结束" % sess["name"])
        resp = md.run()
        md.destroy()
        if resp != Gtk.ResponseType.YES:
            return
        try:
            r = run_ssh_quiet(srv, "tmux kill-session -t %s" % shlex.quote(sess["name"]), self.cfg)
            if r.returncode == 0:
                self.status.set_text("💀 已终止会话 %s" % sess["name"])
            else:
                self.status.set_text("⚠ 终止失败: %s" % r.stderr.strip()[:80])
        except Exception as e:
            self.status.set_text("⚠ 终止失败: %s" % e)
        self._scan(srv)

    def _selected_sess(self):
        sel = self.sess_flow.get_selected_children()
        child = sel[0] if sel else None
        if child is None:
            self.status.set_text("⚠ 请先在右侧选中一个 tmux 会话")
            return None
        return self.sess_widgets.get(child.get_child())

    # ---------- 服务器增删改 ----------

    def on_add_server(self, *a):
        d = self.server_dialog()
        if not d:
            return
        if self.srv_by_name(d["name"]):
            self.status.set_text("⚠ 服务器名称「%s」已存在" % d["name"])
            return
        d["templates"] = []
        self.cfg["servers"].append(d)
        save_config(self.cfg)
        self.reload_servers()
        self._select_srv(d["name"])
        self.status.set_text("➕ 已添加服务器「%s」" % d["name"])

    def on_edit_server(self, *a):
        srv = self.srv_by_name(self.sel_srv) if self.sel_srv else None
        if not srv:
            self.status.set_text("⚠ 请先选中一个服务器")
            return
        d = self.server_dialog(srv)
        if not d:
            return
        if d["name"] != srv["name"] and self.srv_by_name(d["name"]):
            self.status.set_text("⚠ 名称「%s」已存在" % d["name"])
            return
        idx = self.cfg["servers"].index(srv)
        d["templates"] = srv.get("templates", [])
        self.cfg["servers"][idx] = d
        save_config(self.cfg)
        self.reload_servers()
        self._select_srv(d["name"])
        self.status.set_text("✏️ 已保存服务器「%s」" % d["name"])

    def on_delete_server(self, *a):
        srv = self.srv_by_name(self.sel_srv) if self.sel_srv else None
        if not srv:
            self.status.set_text("⚠ 请先选中一个服务器")
            return
        md = Gtk.MessageDialog(transient_for=self, modal=True,
                               message_type=Gtk.MessageType.QUESTION,
                               buttons=Gtk.ButtonsType.YES_NO,
                               text="删除服务器「%s」?" % srv["name"],
                               secondary_text="只删本地配置，不影响服务器本身")
        resp = md.run()
        md.destroy()
        if resp != Gtk.ResponseType.YES:
            return
        self.cfg["servers"] = [s for s in self.cfg["servers"] if s["name"] != srv["name"]]
        save_config(self.cfg)
        self.sel_srv = None
        self.reload_servers()
        self._initial_select()
        self.status.set_text("🗑 已删除服务器「%s」" % srv["name"])

    def server_dialog(self, srv=None):
        d = Gtk.Dialog(title="编辑服务器" if srv else "添加服务器",
                       transient_for=self, modal=True)
        d.add_buttons("取消", Gtk.ResponseType.CANCEL, "保存", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=6, margin=14)
        d.get_content_area().add(grid)

        def row(y, label, text):
            lb = Gtk.Label(label=label, xalign=0)
            grid.attach(lb, 0, y, 1, 1)
            e = Gtk.Entry(text=text)
            grid.attach(e, 1, y, 1, 1)
            return e

        e_name = row(0, "显示名称", srv["name"] if srv else "")
        e_host = row(1, "服务器地址", srv["host"] if srv else DEFAULT_HOST)
        e_user = row(2, "用户名(空=本机用户)", srv.get("user", "") if srv else "")
        e_port = row(3, "端口", str(port_of(srv)) if srv else "22")

        lb4 = Gtk.Label(label="密码(可选)", xalign=0)
        grid.attach(lb4, 0, 4, 1, 1)
        e_pass = Gtk.Entry()
        e_pass.set_visibility(False)
        e_pass.set_placeholder_text("留空 = 使用 SSH 密钥")
        e_pass.set_text(srv.get("password", "") if srv else "")
        grid.attach(e_pass, 1, 4, 1, 1)
        cb_show = Gtk.CheckButton(label="显示")
        cb_show.connect("toggled", lambda b: e_pass.set_visibility(b.get_active()))
        grid.attach(cb_show, 2, 4, 1, 1)

        # 网关(跳板): 一层 SSH 嵌套穿梭。列表来自当前其他服务器
        lb5 = Gtk.Label(label="网关(可选, 跳板)", xalign=0)
        grid.attach(lb5, 0, 5, 1, 1)
        e_jump = Gtk.ComboBoxText()
        e_jump.append("", "（无 · 直连）")
        my_name = srv["name"] if srv else None
        jump_names = []   # combo 下标(≥1) → 服务器名；保存按下标取，绝不反解析显示文本
        for s in self.cfg["servers"]:
            if s["name"] == my_name:
                continue
            jump_names.append(s["name"])
            e_jump.append(s["name"], "↪ %s (%s)" % (s["name"], s["host"]))
        cur = (srv or {}).get("jump", "").lstrip("↪").strip()
        for i, sname in enumerate(jump_names, start=1):
            if sname == cur:
                e_jump.set_active(i)
                break
        grid.attach(e_jump, 1, 5, 1, 1)

        d.show_all()
        resp = d.run()
        name = e_name.get_text().strip()
        host = e_host.get_text().strip() or DEFAULT_HOST
        user = e_user.get_text().strip()
        password = e_pass.get_text()
        active = e_jump.get_active()
        jump = jump_names[active - 1] if active > 0 else ""
        d.destroy()
        if resp != Gtk.ResponseType.OK or not name:
            return None
        try:
            port = int(e_port.get_text().strip() or "22")
        except ValueError:
            port = 22
        if password and not user:
            user = os.environ.get("USER", "")   # 密码认证必须知道用户名
        if jump == name:
            jump = ""   # 不能把自己当网关
        return {"name": name, "host": host, "user": user, "port": port,
                "password": password, "jump": jump}

    # ---------- 新建 tmux 会话 ----------

    def on_new_session(self, *a):
        srv = self.srv_by_name(self.sel_srv) if self.sel_srv else None
        if not srv:
            self.status.set_text("⚠ 请先选中一个服务器")
            return
        d = Gtk.Dialog(title="新建 tmux 会话 — %s" % srv["name"],
                       transient_for=self, modal=True)
        d.add_buttons("取消", Gtk.ResponseType.CANCEL, "创建并连接", Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=8, row_spacing=6, margin=14)
        d.get_content_area().add(grid)

        lb = Gtk.Label(label="会话名", xalign=0)
        grid.attach(lb, 0, 0, 1, 1)
        e_name = Gtk.Entry()
        grid.attach(e_name, 1, 0, 1, 1)

        lb2 = Gtk.Label(label="启动命令(可选)", xalign=0)
        grid.attach(lb2, 0, 1, 1, 1)
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        buf = tv.get_buffer()
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(80)
        sw.add(tv)
        grid.attach(sw, 1, 1, 1, 1)

        lb3 = Gtk.Label(label="模板", xalign=0)
        grid.attach(lb3, 0, 2, 1, 1)
        combo = Gtk.ComboBoxText()
        combo.append("", "（不使用模板）")
        for t in srv.get("templates", []):
            combo.append(t["name"], "%s: %s" % (t["name"], t["startup"].splitlines()[0][:40]))
        combo.set_active(0)
        grid.attach(combo, 1, 2, 1, 1)

        cb_save = Gtk.CheckButton(label="同时存为模板，下次直接选")
        cb_save.set_margin_top(4)
        grid.attach(cb_save, 1, 3, 1, 1)

        d.show_all()
        resp = d.run()
        name = e_name.get_text().strip()
        startup = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        tpl_name = combo.get_active_text() or ""
        d.destroy()
        if resp != Gtk.ResponseType.OK or not name:
            return
        if not startup.strip() and tpl_name and tpl_name != "（不使用模板）":
            for t in srv.get("templates", []):
                if tpl_name.startswith(t["name"] + ":"):
                    startup = t["startup"]
                    break
        if cb_save.get_active() and startup.strip():
            tpls = srv.setdefault("templates", [])
            tpls = [t for t in tpls if t["name"] != name]
            tpls.append({"name": name, "startup": startup})
            srv["templates"] = tpls
            save_config(self.cfg)
        argv = launch_terminal(attach_shell_string(srv, name, startup), "tmux: %s" % name,
                               tab=self._tab_mode())
        if argv is None:
            self.status.set_text("⚠ 未找到终端模拟器")
        else:
            self.status.set_text("🔌 正在创建并连接 tmux 会话「%s」..." % name)
        self._scan(srv)

    # ---------- 杂项 ----------

    def build_menus(self):
        self.srv_menu = Gtk.Menu()
        for label, cb in (("🔌 打开 SSH", self.on_connect_ssh),
                          ("📂 SFTP 终端", self.on_sftp_terminal),
                          ("📁 SFTP 文件管理器", self.on_sftp_fm),
                          ("✏️ 编辑", self.on_edit_server),
                          ("📋 复制 ssh 命令", self.on_copy_ssh_cmd),
                          ("🗑 删除", self.on_delete_server)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", cb)
            self.srv_menu.append(item)
        self.srv_menu.show_all()

        self.sess_menu = Gtk.Menu()
        for label, cb in (("🔌 连接", self._connect_selected), ("📋 复制命令", self.on_copy_sess_cmd),
                          ("💀 终止会话", self.on_kill_session)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", cb)
            self.sess_menu.append(item)
        self.sess_menu.show_all()

    def _connect_selected(self, *a):
        sess = self._selected_sess()
        if sess:
            self._connect(sess)

    def _select_srv(self, name):
        for box, n in self.srv_rows.items():
            if n == name:
                row = self.srv_list.get_row_at_index(list(self.srv_rows.keys()).index(box))
                self.srv_list.select_row(row)
                return

    def _initial_select(self):
        if self.cfg["servers"] and self.sel_srv is None:
            self.srv_list.select_row(self.srv_list.get_row_at_index(0))


class TmuxApp(Gtk.Application):
    """Gtk.Application 使 Wayland 下窗口 app_id = application_id，
    GNOME 按此关联 desktop 文件 (io.tmux.launcher.desktop)，dock 才显示正确图标。"""

    def __init__(self):
        super().__init__(application_id="io.tmux.launcher")

    def do_activate(self):
        win = MainWindow()
        win.set_application(self)
        win.connect("destroy", lambda *a: self.quit())
        win.show_all()
        self.win = win


def main():
    TmuxApp().run(sys.argv)


if __name__ == "__main__":
    main()
