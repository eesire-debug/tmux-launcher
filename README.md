# 🔗 Tmux Launcher

> 图形化的远程 tmux 会话管理器 — 把 `ssh tmux ls` 变成可视化点击操作。

一个本地 GTK3 应用：左侧管理服务器，右侧自动探测该机器上所有 tmux 会话，
**双击即连**。支持跳板、密码认证、内嵌终端 tab、负荷看板、启动模板……

🌐 **语言 / Language**: [🇨🇳 中文](#-tmux-launcher) · [🇬🇧 English](./README.en.md)

![screenshot-placeholder](docs/screenshot.png)

---

### ✨ 功能一览

| 区域 | 能力 |
| --- | --- |
| 🖥 **服务器管理** | 增/删/改：显示名 · 地址 · 用户名 · 端口 · 密码 · 网关(一层跳板) |
| 🔍 **tmux 自动探测** | 后台 `ssh tmux ls` 拉取全部会话，**每 30 秒**自动刷新选中服务器 |
| 🖱 **一键连接** | 双击会话卡片 → 在 ptyxis/gnome-terminal 新标签页打开，支持标签页/新窗口切换 |
| 📑 **标签页模式** | 顶栏一键切换：开 = 复用现有终端窗口的 tab；关 = 每次新窗口 |
| ➕ **新建会话** | 图形化创建 tmux 会话，可指定**启动命令**，保存为**模板**复用 |
| 📊 **负荷看板** | CPU · 内存 · 磁盘 · 负载 · 上下行网速 · GPU · ping · 运行时长，**每 10 秒**刷新 |
| 💻 **内嵌终端** | 右侧 VTE 终端 tab，免 SSH 登录即可跑 `top`/`mytop`/`tail -f`，**实时刷新 + 可交互** |
| 📁 **SFTP** | 一键打开 Nautilus 或文件管理器到服务器根目录；或调用 `sftp://` URL |
| 📋 **复制命令** | 一键复制 attach / ssh 命令到剪贴板，贴到任何终端都能连 |
| 🪓 **杀会话** | 右键任意会话即可 `tmux kill-session`，可指定单个或全部 |
| 🚇 **跳板 / 网关** | 一层 `ssh -J` 嵌套，支持密码或密钥认证（问询时自动选 askpass） |
| 🔐 **askpass 免密** | 每台机器单独记密码，连接时自动调用，SSH Agent 不存在时也能免交互 |

---

### 📦 安装

**依赖**

```bash
# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 ptyxis
# Fedora
sudo dnf install python3-gobject gtk3 vte291 ptyxis
# Arch
sudo pacman -S python-gobject gtk3 vte3 ptyxis
```

> `ptyxis` 可替换为 `gnome-terminal` 或任何 `x-terminal-emulator`。
> 内嵌终端 tab 依赖 `gir1.2-vte-2.91`（VTE 缺失时自动降级，按钮隐藏）。

**运行**

```bash
git clone https://github.com/eesire-debug/tmux-launcher.git
cd tmux-launcher
chmod +x run.sh
./run.sh                    # 带日志启动（日志: /tmp/tmux-launcher.log）
```

或直接：

```bash
python3 tmux_launcher.py
```

**桌面快捷方式**

```bash
ln -sf "$PWD/io.tmux.launcher.desktop" ~/Desktop/io.tmux.launcher.desktop
cp io.tmux.launcher.desktop ~/.local/share/applications/
mkdir -p ~/.local/share/icons/hicolor/256x256/apps
cp tmux-launcher-256.png ~/.local/share/icons/hicolor/256x256/apps/tmux-launcher.png
```

之后在 GNOME Activities 搜 **Tmux 连接器** 即可。

---

### 🚀 使用

**第一次启动**

1. 点顶栏 **➕ 添加服务器**，填写：
   - **显示名称**：列表里看到的名字（任意字符串）
   - **服务器地址**：IP 或域名
   - **用户名**：留空用本机 `$USER`
   - **端口**：默认 22
   - **密码**：留空走 SSH 密钥；填了则走 askpass
   - **网关**：从下拉选另一台已添加的服务器，作为 `-J` 跳板
2. 左侧点选刚加的服务器 → 右侧**自动开始探测** `tmux ls`
3. 双击任意会话卡片 → 在终端里 attach 进去
4. 顶栏 **📑 标签页模式** 切换：开 = 复用现有终端窗口的 tab；关 = 每次新窗口

**常用操作**

| 想做什么 | 怎么操作 |
| --- | --- |
| 连接已有会话 | 双击右侧会话卡片 |
| 新建会话 | 右侧 **➕ 新建 tmux 会话**，填名字 + 启动命令，可保存为模板 |
| 跑个临时命令 | 顶栏 **📟 执行命令** 或右键服务器 → "执行命令" → 弹框输入 → 跑在右侧终端 tab |
| 打开 SFTP | 右键服务器 → "SFTP" |
| 复制 ssh 命令 | 右键会话 → "复制连接命令" |
| 杀掉会话 | 右键会话 → "删除会话" |
| 服务器不通 | 看左侧卡片右下角的 **✗ 不可达** 标记；点 ▶ 手动重试 |

**配置文件**

```text
~/.config/tmux-launcher/
├── sessions.json   # 服务器 / 模板配置（含密码字段，慎提交到公网仓库）
└── askpass.py      # 运行时自动生成的密码助手（按主机匹配）
```

`sessions.json` 示例：

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

> 🛡 `.gitignore` 已默认排除 `sessions.json` 和 `askpass.py` —
> 任何含密码的本地配置都不会被 git 追踪。

---

### 🧠 设计取舍

- **无服务端**：纯客户端，只用 `ssh` 调远端命令和 `tmux` 的内建特性（attach / new-session / kill-session）。
  不在远端装任何 agent。
- **探测与连接解耦**：探测用 `BatchMode=yes` + `-T` 超时静默跑，连接走终端 — 即使探测因认证失败挂掉，
  你照样可以右键服务器 → "新建会话" 或 "SFTP"。
- **askpass 精确匹配**：`SSH_ASKPASS=askpass.py`，根据 ssh 弹出的 `user@host's password:` 字符串精确匹配，
  多跳板时每跳用各自密码（v3 修复穿梭两跳密码不同导致目标跳拿错密码的 bug）。
- **内嵌终端 vs 外部终端**：内嵌 tab 用 VTE (`ssh -tt`)，适合看动态输出；正式工作仍推荐外部 tab
  （标签页模式开启时），可获得完整快捷键、配色、复制粘贴。
- **极简 i18n**：自带 `LANGS` 字典 + `t()` 函数，所有可见字符串按 key 索引。运行时切换语言只重绘静态文本，
  不重启应用。

---

### 🐞 故障排查

| 现象 | 处理 |
| --- | --- |
| 双击 `.desktop` 启动后没反应 | `cat /tmp/tmux-launcher.log` 看 stderr |
| 终端打不开 | 装 `ptyxis` 或 `gnome-terminal`；否则自动回退到 `x-terminal-emulator` |
| 探测显示"未安装 tmux" | 远端没装 tmux：`apt install tmux` / `yum install tmux` |
| 跳板连不上 | 确认跳板机器也已添加，且**跳板本身能 SSH 登录** |
| 密码错 | 重新编辑服务器，重填密码；askpass 不会回放老密码 |
| VTE 终端 tab 按钮不见 | 装 `gir1.2-vte-2.91`；缺失时仅该功能降级，其余正常 |
| 想改回默认语言 | UI 锁定英文，如需中文改 `tmux_launcher.py` 里的 `LANG = "en"` 为 `"zh"` 即可 |

---

### 🗺 版本历史

- **v8** — 右侧新增可切换终端 tab（VTE 内嵌，占满高度）；可执行任意命令并实时交互
- **v7** — tmux 连接标签页标题改为 `会话名@机器`
- **v6** — 移除硬编码默认服务器；标签页模式支持 gnome-terminal；新增 `.desktop` 文件
- **v5** — 修复跳板穿梭；会话列表双列；卡片加 GPU/网络/ping
- **v4** — 服务器列表与负荷看板合并为左侧卡片
- **v3** — 负荷看板（10s）+ SSH 一层跳板 + askpass v2 + 网关 UI
- **v2** — 双栏服务器管理 + tmux 探测 + 标签页 + 密码认证 + SFTP

---

### 📄 协议

MIT
