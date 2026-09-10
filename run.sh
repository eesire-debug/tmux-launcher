#!/usr/bin/env bash
# Tmux Launcher 启动脚本 —— 带日志 / 进程检查 / 重复启动保护
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG=/tmp/tmux-launcher.log
PIDFILE=/tmp/tmux-launcher.pid

# 防止重复启动
if [[ -f $PIDFILE ]] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "⚠️  已在运行 (pid=$(cat $PIDFILE))，日志: $LOG"
    echo "   要重启请先 kill: kill \$(cat $PIDFILE)"
    exit 1
fi

# 启动
nohup python3 -u "$HERE/tmux_launcher.py" >"$LOG" 2>&1 &
PID=$!
echo $PID > "$PIDFILE"

# 等 2 秒看是否存活
sleep 2
if kill -0 "$PID" 2>/dev/null; then
    echo "✅ 已启动 pid=$PID，日志: $LOG"
    echo "   停止: kill $PID"
else
    echo "❌ 启动失败，日志内容："
    echo "────────────────────────────────"
    cat "$LOG"
    echo "────────────────────────────────"
    rm -f "$PIDFILE"
    exit 1
fi
