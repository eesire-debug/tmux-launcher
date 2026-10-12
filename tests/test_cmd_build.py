#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令构建 / 解析 / i18n 的离线回归测试：不发网络请求、不碰用户配置。

    python3 tests/test_cmd_build.py        # 全绿退出码 0，任一失败退出码 1

覆盖 v8.4.2 修的三个 bug（新建会话漏传 cfg、kill 阻塞主线程、Wayland 菜单告警），
以及跳板 + askpass 命令行拼装、探测输出解析、中英文词条对齐。
"""
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tmux_launcher as tl        # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        FAILS.append(name)
        print("  FAIL %s%s" % (name, ("  <- " + detail) if detail else ""))


def eq(name, got, want):
    check(name, got == want, "got %r, want %r" % (got, want))


def has(name, haystack, *needles):
    missing = [n for n in needles if n not in haystack]
    check(name, not missing, "missing %s" % missing)


def not_has(name, haystack, *banned):
    present = [b for b in banned if b in haystack]
    check(name, not present, "unexpectedly present: %s" % present)


# ---------------- 测试用配置 ----------------

GW = {"name": "gw", "host": "10.0.0.1", "user": "ops", "port": 22, "password": "PwGw",
      "jump": "", "templates": []}
DMZ = {"name": "dmz", "host": "1.2.3.4", "user": "admin", "port": 2222, "password": "PwDmz",
       "jump": "gw", "templates": []}
KEY = {"name": "key", "host": "9.9.9.9", "user": "ligq", "port": 22, "password": "",
       "jump": "", "templates": []}
CFG = {"prefs": {"tab": True}, "servers": [GW, DMZ, KEY]}


def section(title):
    print("\n%s" % title)


# ---------------- 1. 跳板 + askpass 命令行 ----------------

section("1. 跳板 / askpass 命令行拼装")

s = tl.attach_shell_string(DMZ, "work", cfg=CFG)
has("attach: 带 -J 跳板(含端口)", s, "-J", "ops@10.0.0.1")
has("attach: askpass 环境变量", s, "SSH_ASKPASS=", "SSH_ASKPASS_REQUIRE=force", "TL_CONFIG=")
has("attach: 两跳目标都进 TL_TARGETS", s, "TL_TARGETS=")
check("attach: TL_TARGETS 含跳板与目标", "admin@1.2.3.4" in s and "ops@10.0.0.1" in s)
has("attach: 密码认证策略", s, "PreferredAuthentications=password", "NumberOfPasswordPrompts=1")
has("attach: 端口", s, "-p 2222")
has("attach: tmux 新建并 attach", s, "tmux new-session -A -s work")
not_has("attach: 密码不出现在命令行", s, "PwDmz", "PwGw")

s2 = tl.attach_shell_string(DMZ, "work", startup="claude", cfg=CFG)
has("attach(带启动命令): has-session 判断 + 新建", s2, "tmux has-session -t", "tmux new-session -s", "exec bash")
not_has("attach(带启动命令): 密码不外泄", s2, "PwDmz")

s3 = tl.attach_shell_string(KEY, "work", cfg=CFG)
not_has("直连密钥服务器: 无跳板", s3, "-J")
not_has("直连密钥服务器: 无 askpass 前缀", s3, "SSH_ASKPASS", "TL_TARGETS")

argv, env = tl.term_ssh_argv(DMZ, "top", cfg=CFG)
check("VTE: argv 是列表且首元素 ssh", isinstance(argv, list) and argv[0] == "ssh")
has("VTE: -J 跳板", " ".join(argv), "-J")
check("VTE: env 带 askpass", bool(env) and env.get("SSH_ASKPASS_REQUIRE") == "force")
check("VTE: env 不含密码本身", all("PwDmz" != v for v in (env or {}).values()))
check("VTE: bash -lic 包装", argv[-1].startswith("bash -lic ") and "top" in argv[-1])
argv_ns, _ = tl.term_ssh_argv(DMZ, "top", cfg=CFG, shell=False)
eq("VTE: shell=False 不包装", argv_ns[-1], "top")

calls = []
_real_run = subprocess.run


def fake_run(a, *args, **kw):
    calls.append((a, kw.get("env")))
    return subprocess.CompletedProcess(a, 0, "", "")


subprocess.run = fake_run
try:
    tl.run_ssh_quiet(DMZ, "tmux ls", CFG)     # 跳板 + 密码
    tl.run_ssh_quiet(KEY, "tmux ls", CFG)     # 直连 + 密钥
finally:
    subprocess.run = _real_run

check("探测: 捕获到两次调用", len(calls) == 2)
pw_argv, pw_env = calls[0]
key_argv, key_env = calls[1]
has("探测: 跳板服务器带 -J", " ".join(pw_argv), "-J", "ops@10.0.0.1")
has("探测: 密码服务器 env 带 askpass", "%s" % sorted(pw_env or {}), "SSH_ASKPASS", "TL_TARGETS")
check("探测: 密码服务器 env 不含密码明文", all("PwDmz" != v for v in (pw_env or {}).values()))
has("探测: 密钥服务器走 BatchMode 不弹窗", " ".join(key_argv), "BatchMode=yes")
check("探测: 密钥服务器不需要 askpass", key_env is None or "SSH_ASKPASS" not in key_env)
has("探测: 都有 ConnectTimeout 兜底", " ".join(key_argv), "ConnectTimeout=6")
has("探测: 目标地址与远端命令", " ".join(key_argv), "ligq@9.9.9.9", "tmux ls")
eq("sftp url: 非默认端口", tl.sftp_url(DMZ), "sftp://admin@1.2.3.4:2222/")
eq("sftp url: 默认端口省略", tl.sftp_url(GW), "sftp://ops@10.0.0.1/")
eq("jump_display: 显示网关名", tl.jump_display(DMZ, CFG), "via gw")
eq("jump_display: 直连为空", tl.jump_display(KEY, CFG), "")
eq("jump: 自己不能当自己网关", tl._jump_srv({"name": "gw", "jump": "gw"}, CFG), None)
eq("jump: 兼容脏数据 '↪ gw' 前缀", (tl._jump_srv({"name": "dmz", "jump": "↪ gw"}, CFG) or {}).get("name"), "gw")

# ---------------- 2. 三个 bug 的回归护栏 ----------------

section("2. v8.4.2 修复回归护栏")

src_new = inspect.getsource(tl.MainWindow.on_new_session)
has("bug1: 新建会话传 cfg（否则跳板+密码失效）", src_new, "attach_shell_string(srv, name, startup, cfg=self.cfg)")
src_conn = inspect.getsource(tl.MainWindow._connect)
has("bug1: 双击连接同样传 cfg", src_conn, "cfg=self.cfg")

src_kill = inspect.getsource(tl.MainWindow.on_kill_session)
has("bug2: kill 走后台线程", src_kill, "threading.Thread(target=run, daemon=True).start()")
has("bug2: 结果回主线程", src_kill, "GLib.idle_add(self._after_kill")
check("bug2: kill 主路径不再同步调用 run_ssh_quiet",
      src_kill.index("def run()") < src_kill.index("run_ssh_quiet"))
has("bug2: 重扫只在结果回调里做", inspect.getsource(tl.MainWindow._after_kill), "self._scan(srv)")

src_pm = inspect.getsource(tl.MainWindow._popup_menu)
has("bug3: 菜单用 popup_at_pointer", src_pm, "menu.popup_at_pointer(event)")
has("bug3: 旧 GTK 回退 popup", src_pm, "menu.popup(None, None, None, None")
has("bug3: 服务器右键用 _popup_menu", inspect.getsource(tl.MainWindow.on_srv_button_press), "self._popup_menu(self.srv_menu, event)")
has("bug3: 会话右键用 _popup_menu", inspect.getsource(tl.MainWindow._on_sess_card_press), "self._popup_menu(self.sess_menu, event)")
src_bm = inspect.getsource(tl.MainWindow.build_menus)
eq("bug3: 两个菜单都 attach 到容器", src_bm.count("attach_to_widget("), 2)

# ---------------- 3. 探测输出解析 ----------------

section("3. 探测输出解析")

st, sessions, msg = tl.parse_tmux_ls(subprocess.CompletedProcess([], 0,
    "claude|3|1700000000\nlogs|1|1700000900\nRC=0\n", ""))
eq("tmux ls: status", st, "ok")
eq("tmux ls: 条数", len(sessions), 2)
eq("tmux ls: 按创建时间倒序", [x["name"] for x in sessions], ["logs", "claude"])
eq("tmux ls: 窗口数", sessions[1]["windows"], "3")
eq("tmux ls: 时间戳", sessions[1]["created"], 1700000000)
eq("tmux ls: 无会话", tl.parse_tmux_ls(subprocess.CompletedProcess([], 0, "RC=1\n", ""))[0], "empty")
eq("tmux ls: 未装 tmux", tl.parse_tmux_ls(subprocess.CompletedProcess([], 0, "NOTMUX\n", ""))[0], "notmux")
eq("tmux ls: 未装 tmux(旧 stderr 路径)",
   tl.parse_tmux_ls(subprocess.CompletedProcess([], 0, "", "bash: tmux: command not found"))[0], "notmux")
has("tmux ls: 探测命令先判定 tmux 是否存在", inspect.getsource(tl.scan_server), "command -v tmux", "NOTMUX")
eq("tmux ls: ssh 失败", tl.parse_tmux_ls(subprocess.CompletedProcess([], 255, "", "Permission denied"))[0], "down")

load_out = ("L|2.50|1.50|1.00\nM|32000|16000|15000\nD|80000|200000\nU|1500\nC|8\n"
            "N|2048|512\nG|45|4096|24576|NVIDIA GeForce RTX 4090\n")
info = tl.parse_load_output(load_out)
eq("load: ok", info.get("ok"), True)
eq("load: load1", info.get("load1"), 2.5)
eq("load: 内存 MB", info.get("mem_avail"), 15000)
eq("load: 核数", info.get("cores"), 8)
eq("load: 百分比 = load1/核", round(info.get("load_pct"), 1), 31.2)
eq("load: 网络 rx KB/s", info.get("net_rx_kbps"), 2048)
eq("load: GPU 数量", len(info.get("gpus") or []), 1)
eq("load: GPU 利用率", info["gpus"][0]["util"], 45)
eq("load: 状态标签", tl.load_status(info)[0], "load-ok")
eq("load: 空输出不算 ok", tl.parse_load_output("").get("ok"), False)
eq("load: 垃圾行忽略", tl.parse_load_output("bye\r\n???\nL|1|1|1").get("load1"), 1.0)
eq("load: lspci 兜底", tl.parse_load_output("L|1|1|1\nG|lspci|CardA;CardB;\n")["gpus"][0]["lspci"], True)
eq("load: lspci 兜底取到卡名",
   tl.parse_load_output("G|lspci|CardA;CardB;\n")["gpus"][0]["names"], ["CardA", "CardB"])
eq("fmt_up: 分钟", tl.fmt_up(5), tl.t("up_min", 5))
eq("fmt_up: 天/小时", tl.fmt_up(25 * 60), tl.t("up_dh", 1, 1))
eq("fmt_kbps: K/s", tl.fmt_kbps(500), "500K/s")
eq("fmt_kbps: M/s", tl.fmt_kbps(2048), "2.0M/s")
eq("fmt_kbps: G/s", tl.fmt_kbps(3 * 1024 * 1024), "3.0G/s")
eq("net_text: 缺失", tl.net_text({}), "NET —")
has("net_text: 上下行", tl.net_text({"net_rx_kbps": 100, "net_tx_kbps": 200}), "↓", "↑")
has("ping_markup: 颜色分级", tl.ping_markup(100), "#2ecc71")
has("ping_markup: 高延迟红色", tl.ping_markup(2000), "#e74c3c")

# ---------------- 4. askpass 脚本（临时目录，不碰用户配置） ----------------

section("4. askpass 按 host 取密码")

with tempfile.TemporaryDirectory() as td:
    cfg_path = os.path.join(td, "sessions.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"prefs": {}, "servers": [GW, DMZ]}, f)
    old_dir, old_askpass = tl.CONFIG_DIR, tl.ASKPASS
    tl.CONFIG_DIR, tl.ASKPASS = td, os.path.join(td, "askpass.py")
    try:
        path = tl.ensure_askpass()
        env2 = dict(os.environ, TL_CONFIG=cfg_path,
                    TL_TARGETS="ops@10.0.0.1 admin@1.2.3.4")
        r = subprocess.run([sys.executable, path, "admin@1.2.3.4's password:"],
                           capture_output=True, text=True, env=env2)
        eq("askpass: 目标跳取到自己的密码", r.stdout, "PwDmz")
        r = subprocess.run([sys.executable, path, "ops@10.0.0.1's password:"],
                           capture_output=True, text=True, env=env2)
        eq("askpass: 跳板取到跳板密码", r.stdout, "PwGw")
        r = subprocess.run([sys.executable, path, "nobody@8.8.8.8's password:"],
                           capture_output=True, text=True, env=env2)
        eq("askpass: 未知主机不回吐密码", (r.returncode, r.stdout), (1, ""))
        r = subprocess.run([sys.executable, path, "Password:"],
                           capture_output=True, text=True, env=env2)
        eq("askpass: 提示无主机时回退 TL_TARGETS", r.stdout, "PwGw")
        r = subprocess.run([sys.executable, path, "10.0.0.1's password:"],
                           capture_output=True, text=True,
                           env=dict(env2, TL_TARGETS=""))
        eq("askpass: 裸主机名也能匹配", r.stdout, "PwGw")
        r = subprocess.run([sys.executable, path, "admin@1.2.3.4's password:"],
                           capture_output=True, text=True, env=dict(env2, TL_TARGETS=""))
        eq("askpass: 无 TL_TARGETS 时仍按提示取", r.stdout, "PwDmz")
        eq("askpass: 权限收紧到 0700", oct(os.stat(path).st_mode & 0o777), "0o700")
        eq("askpass: 版本号写入文件头", tl.ASKPASS_VERSION in open(path).read(200), True)
        eq("askpass: v4 起不再无条件回退 TL_TARGETS",
           "cands += os.environ" in open(path).read(), False)
    finally:
        tl.CONFIG_DIR, tl.ASKPASS = old_dir, old_askpass

# ---------------- 5. i18n 词条对齐 ----------------

section("5. i18n 词条对齐")

src = open(os.path.join(os.path.dirname(tl.__file__), "tmux_launcher.py"), encoding="utf-8").read()
used = set(re.findall(r'\bt\(\s*"([a-z0-9_]+)"', src))
missing_en = sorted(k for k in used if k not in tl.LANGS["en"])
missing_zh = sorted(k for k in used if k not in tl.LANGS["zh"])
check("en: 所有用到的 key 都有词条", not missing_en, "missing %s" % missing_en)
check("zh: 所有用到的 key 都有词条", not missing_zh, "missing %s" % missing_zh)
eq("zh / en key 集合一致", sorted(set(tl.LANGS["zh"]) ^ set(tl.LANGS["en"])), [])
dead = sorted(set(tl.LANGS["en"]) - used)
check("en: 无未使用词条", not dead, "unused %s" % dead)
eq("t(): 缺失 key 原样返回", tl.t("no_such_key_xyz"), "no_such_key_xyz")
eq("t(): 参数格式化", tl.t("scan_count", "srv", 3), tl.LANGS["en"]["scan_count"] % ("srv", 3))
eq("t(): 参数数量不匹配不抛异常", tl.t("scan_count", "only-one"), tl.LANGS["en"]["scan_count"])
check("新增词条: killing 存在", "killing" in tl.LANGS["en"] and "killing" in tl.LANGS["zh"])
check("新增词条: dlg_cmd_preset 存在", "dlg_cmd_preset" in tl.LANGS["en"])

# ---------------- 6. 快捷命令 ----------------

section("6. top/btop/mytop 快捷命令")

eq("CMD_PRESETS 内容", tl.CMD_PRESETS, ("top", "btop", "mytop"))
eq("CMD_PRESETS 无重复", len(set(tl.CMD_PRESETS)), len(tl.CMD_PRESETS))
src_term = inspect.getsource(tl.MainWindow.on_new_term)
has("执行命令对话框渲染快捷按钮", src_term, "for cmd in CMD_PRESETS")
has("快捷按钮写入命令框", src_term, "e_cmd.set_text(c)")
check("快捷按钮不改变对话框行布局(复选框移到第 3 行)", 'grid.attach(cb_login, 1, 3, 2, 1)' in src_term)

# ---------------- 结果 ----------------

print("\n" + "=" * 60)
if FAILS:
    print("✗ %d 项失败:" % len(FAILS))
    for f in FAILS:
        print("   - %s" % f)
    sys.exit(1)
print("✓ 全部通过")
