#!/bin/zsh
set -e
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

# ==============================================================================
# 我要发财 全功能一键联合启动器 (Desktop GUI + Mobile Web + 公网访问)
# ==============================================================================
# 功能说明：
# - 默认行为：双击一键启动全部组件：
#   1. 手机端后台服务 (Flask Web / PWA on Port 8888)
#   2. 公网安全直连隧道 (ngrok 自动分配公网 HTTPS 网址，免装 App 秒开)
#   3. 桌面端图形界面 (PySide6 GUI)
# - 数据同步参数：
#   ./启动Money3.command --api        (强制完整券商API+Excel同步后启动)
#   ./启动Money3.command --excel      (强制仅本地Excel同步后启动)
#   ./启动Money3.command --no-sync    (强制跳过同步直接启动)
# - 服务模式参数：
#   ./启动Money3.command --only-mobile (仅启动手机后台与公网隧道，不打开桌面端GUI)
#   ./启动Money3.command --no-public   (仅启动桌面端与局域网手机端，不开启公网隧道)
#   ./启动Money3.command --no-mobile   (仅启动桌面端GUI，不启动手机端服务与公网)
#   ./启动Money3.command --stop        (一键停止手机后台服务与公网隧道)
# ==============================================================================

SYNC_API="${SYNC_API:-0}"
SYNC_EXCEL_ONLY="${SYNC_EXCEL_ONLY:-0}"
START_MOBILE=1
START_PUBLIC=1
START_DESKTOP=1
STOP_ALL=0

APP_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --api|--sync)
            SYNC_API=1
            SYNC_EXCEL_ONLY=0
            ;;
        --excel|--no-api)
            SYNC_API=0
            SYNC_EXCEL_ONLY=1
            ;;
        --no-sync)
            SYNC_API=0
            SYNC_EXCEL_ONLY=0
            ;;
        --only-mobile|--only-public|--server-only)
            START_MOBILE=1
            START_PUBLIC=1
            START_DESKTOP=0
            ;;
        --no-public)
            START_PUBLIC=0
            ;;
        --no-mobile)
            START_MOBILE=0
            START_PUBLIC=0
            ;;
        --stop-mobile|--stop|--stop-all)
            STOP_ALL=1
            ;;
        --help|-h)
            print "用法: ./启动Money3.command [选项]"
            print ""
            print "常规启动选项:"
            print "  (无参数)          一键启动桌面端 GUI、手机端服务与公网直连隧道 (默认推荐)"
            print "  --only-mobile    仅启动手机后台服务与公网隧道，不打开桌面端图形窗口"
            print "  --no-public      仅启动桌面端与局域网手机端，不建立公网隧道"
            print "  --no-mobile      仅启动桌面端 GUI，不启动手机端服务"
            print "  --stop           一键停止正在运行的手机端后台服务与公网隧道"
            print ""
            print "数据同步选项:"
            print "  --api, --sync    启动前强制调用券商API完整同步数据"
            print "  --excel          启动前强制仅同步本地交易流水.xlsx"
            print "  --no-sync        跳过任何同步，直接极速启动"
            exit 0
            ;;
        *)
            APP_ARGS+=("$arg")
            ;;
    esac
done

# ------------------------------------------------------------------------------
# 停止服务处理 (--stop / --stop-all)
# ------------------------------------------------------------------------------
if [[ "$STOP_ALL" == "1" ]]; then
    MOBILE_PID=$(lsof -ti tcp:8888 -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "$MOBILE_PID" ]]; then
        kill "$MOBILE_PID" 2>/dev/null || true
        print "✅ 手机端后台服务 (PID: $MOBILE_PID) 已成功停止。"
    else
        print "ℹ️ 手机端 Web 服务当前未在运行 (端口 8888 未被占用)。"
    fi

    if pgrep -f "ngrok http 8888" &>/dev/null; then
        pkill -f "ngrok http 8888" 2>/dev/null || true
        print "✅ 公网隧道 (ngrok) 已安全停止。"
    else
        print "ℹ️ 公网隧道当前未在运行。"
    fi
    exit 0
fi

# ------------------------------------------------------------------------------
# 1. 启动并检测手机端后台服务 (Flask Web / PWA on Port 8888)
# ------------------------------------------------------------------------------
MOBILE_PID=""
IS_MOBILE_RUNNING=0
MOBILE_LOG="/tmp/money_mobile.log"
touch "$MOBILE_LOG"

if [[ "$START_MOBILE" == "1" ]]; then
    # Build only when source files are newer than the shipped entry point.
    # A normal relaunch must never keep serving stale Web code.
    if "$APP_DIR/.venv/bin/python" - "$APP_DIR" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
entry = root / 'src/portfolio/web/static_dist/index.html'
inputs = list((root / 'frontend/src').rglob('*')) + list((root / 'frontend').glob('*'))
newest = max((p.stat().st_mtime_ns for p in inputs if p.is_file()), default=0)
sys.exit(0 if not entry.exists() or newest > entry.stat().st_mtime_ns else 1)
PY
    then
        if ! command -v npm >/dev/null 2>&1; then
            print "❌ Web 源码已更新，需要 Node.js/npm 构建；本次停止启动，避免展示旧页面。"
            exit 1
        fi
        print "🔧 正在生成最新 Web 页面（不运行测试）…"
        npm --prefix "$APP_DIR/frontend" run build
    fi
    EXISTING_PID=$(lsof -ti tcp:8888 -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "$EXISTING_PID" ]]; then
        if ps -p "$EXISTING_PID" -o command= 2>/dev/null | grep -q "portfolio.web.app" &&
            lsof -a -p "$EXISTING_PID" -d cwd -Fn 2>/dev/null | grep -Fxq "n$APP_DIR"; then
            print "♻️ 正在重启本项目 Web 服务以载入最新代码…"
            kill "$EXISTING_PID"
            for i in {1..25}; do
                if ! kill -0 "$EXISTING_PID" 2>/dev/null; then
                    break
                fi
                sleep 0.2
            done
            if kill -0 "$EXISTING_PID" 2>/dev/null; then
                print "❌ 旧 Web 服务未能退出，请关闭后重新启动。"
                exit 1
            fi
        else
            print "❌ 8888 端口被其他进程占用；请释放端口后重新启动 Money3。"
            exit 1
        fi
    fi

    if [[ "$IS_MOBILE_RUNNING" == "0" ]]; then
        nohup "$APP_DIR/.venv/bin/python" -m portfolio.web.app >> "$MOBILE_LOG" 2>&1 &
        MOBILE_PID=$!
        for i in {1..25}; do
            if lsof -ti tcp:8888 -sTCP:LISTEN &>/dev/null; then
                IS_MOBILE_RUNNING=1
                MOBILE_PID=$(lsof -ti tcp:8888 -sTCP:LISTEN 2>/dev/null || echo "$MOBILE_PID")
                break
            fi
            sleep 0.2
        done
    fi
fi

# ------------------------------------------------------------------------------
# 2. 启动并检测公网直连隧道 (ngrok -> Port 8888)
# ------------------------------------------------------------------------------
NGROK_BIN="$APP_DIR/tools/ngrok"
NGROK_LOG="/tmp/money_ngrok.log"
PUBLIC_URL=""
IS_NGROK_RUNNING=0

if [[ "$START_PUBLIC" == "1" && "$IS_MOBILE_RUNNING" == "1" && -x "$NGROK_BIN" ]]; then
    # 先检查是否已有运行中的 ngrok 隧道并提取 URL
    PUBLIC_URL=$("$APP_DIR/.venv/bin/python" -c "
import urllib.request, json
try:
    with urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels', timeout=0.8) as resp:
        data = json.loads(resp.read().decode())
        for t in data.get('tunnels', []):
            if t.get('proto') == 'https':
                print(t.get('public_url', ''))
                break
        else:
            if data.get('tunnels'):
                print(data['tunnels'][0].get('public_url', ''))
except Exception:
    pass
" 2>/dev/null || true)

    if [[ -n "$PUBLIC_URL" ]]; then
        IS_NGROK_RUNNING=1
    else
        # 若未运行且已配置 Token，启动后台守护进程
        if "$NGROK_BIN" config check &>/dev/null; then
            nohup "$NGROK_BIN" http 8888 --log=stdout > "$NGROK_LOG" 2>&1 &
            for i in {1..30}; do
                sleep 0.25
                PUBLIC_URL=$("$APP_DIR/.venv/bin/python" -c "
import urllib.request, json
try:
    with urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels', timeout=0.8) as resp:
        data = json.loads(resp.read().decode())
        for t in data.get('tunnels', []):
            if t.get('proto') == 'https':
                print(t.get('public_url', ''))
                break
        else:
            if data.get('tunnels'):
                print(data['tunnels'][0].get('public_url', ''))
except Exception:
    pass
" 2>/dev/null || true)
                if [[ -n "$PUBLIC_URL" ]]; then
                    IS_NGROK_RUNNING=1
                    break
                fi
            done
        fi
    fi

    # 自动将公网地址存入系统剪贴板
    if [[ -n "$PUBLIC_URL" ]]; then
        echo "$PUBLIC_URL" | pbcopy 2>/dev/null || true
    fi
fi

# ------------------------------------------------------------------------------
# 3. 提取本地网络 IP 并展示联合启动仪表盘
# ------------------------------------------------------------------------------
WIFI_IP=$(ifconfig | grep -E '^\s*inet ' | awk '{print $2}' | grep -v '^127\.' | grep -v '^198\.18\.' | grep -v '^100\.' | head -n 1)
[[ -z "$WIFI_IP" ]] && WIFI_IP="127.0.0.1"

TAILSCALE_IP=$(ifconfig | grep -E '^\s*inet ' | awk '{print $2}' | grep '^100\.' | head -n 1)
if [[ -z "$TAILSCALE_IP" ]] && which tailscale &>/dev/null; then
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null | head -n 1)
fi

print "\n=================================================================="
print "  🚀 我要发财 全功能一键联合启动器"
print "=================================================================="
if [[ "$IS_MOBILE_RUNNING" == "1" ]]; then
    print "  ✅ 1. 手机端 Web 核心服务已就绪 (端口: 8888, PID: $MOBILE_PID)"
else
    print "  ⚠️ 1. 手机端 Web 服务未启动"
fi

if [[ -n "$PUBLIC_URL" ]]; then
    print "  ✅ 2. 公网直连安全隧道已建立 (已自动复制公网网址到剪贴板！)"
else
    print "  ℹ️ 2. 公网隧道未运行 (如需开启请确认已配置 tools/ngrok)"
fi

if [[ "$START_DESKTOP" == "1" ]]; then
    print "  ✅ 3. 正在启动桌面端图形界面 (PySide6)..."
else
    print "  ℹ️ 3. 手机端独立模式 (已跳过桌面端窗口)"
fi

print ""
print "  📱【手机访问地址 (iPhone / iPad / 安卓)】"
print "     • 家中同一 WiFi:   👉 http://${WIFI_IP}:8888"
if [[ -n "$PUBLIC_URL" ]]; then
    print "     • 户外公网/5G直连: 👉 $PUBLIC_URL  (已复制剪贴板)"
fi
if [[ -n "$TAILSCALE_IP" ]]; then
    print "     • Tailscale 专用网: 👉 http://${TAILSCALE_IP}:8888"
fi

print ""
print "  👥【用户登录账号】"
print "     • 用户名: 拉科     密码: lake123     (绑定拉科个人账本)"
print "     • 用户名: 巴小     密码: baxiao321   (绑定巴小个人账本)"
print "     (注: 手机浏览器直接打开网址，登录后即可查看专属持仓与盈亏)"
print "=================================================================="

# ------------------------------------------------------------------------------
# 4. 执行前置数据同步 (可选)
# ------------------------------------------------------------------------------
DO_SYNC=1
SYNC_FLAGS=()

if [[ "$SYNC_API" == "1" ]]; then
    DO_SYNC=1
    print "\n=================================================="
    print " 🔄 正在执行完整数据同步 (长桥/嘉信 API -> 流水.xlsx -> 数据库)..."
    print "=================================================="
elif [[ "$SYNC_EXCEL_ONLY" == "1" ]]; then
    DO_SYNC=1
    SYNC_FLAGS+=("--no-api")
    print "\n=================================================="
    print " 🔄 正在执行本地数据同步 (交易流水.xlsx 5个Sheet -> 数据库)..."
    print "=================================================="
fi

if [[ "$DO_SYNC" == "1" ]]; then
    SYNC_PYTHON=""
    if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
        SYNC_PYTHON="$APP_DIR/.venv/bin/python"
    fi

    if [[ -z "$SYNC_PYTHON" ]]; then
        print "[错误] 未找到可执行数据同步的 Python 环境（需要 .venv）。"
        exit 1
    else
        if "$SYNC_PYTHON" "$APP_DIR/scripts/sync_data.py" "${SYNC_FLAGS[@]}"; then
            print "[OK] 数据同步成功！正在启动应用...\n"
        else
            print "\n[警告] 数据同步未完全成功（详见上方日志）。"
            read -r "?是否仍然继续启动 Money3？(y/N): " choice
            if [[ "$choice" != "y" && "$choice" != "Y" ]]; then
                print "启动已取消。"
                exit 1
            fi
            print "继续启动应用...\n"
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 5. 启动桌面端应用 (PySide6) 或保持手机独立后台服务
# ------------------------------------------------------------------------------
if [[ "$START_DESKTOP" == "1" ]]; then
    DESKTOP_BIN="$APP_DIR/.venv/bin/我要发财"
    if [[ ! -x "$DESKTOP_BIN" ]]; then
        cp -L "$APP_DIR/.venv/bin/python3.12" "$DESKTOP_BIN" 2>/dev/null || cp -L "$APP_DIR/.venv/bin/python" "$DESKTOP_BIN" 2>/dev/null || DESKTOP_BIN="$APP_DIR/.venv/bin/python"
    fi
    if [[ -x "$DESKTOP_BIN" ]]; then
        print "  🖥️ 桌面端主窗口已启动..."
        print -- "------------------------------------------------------------------\n"
        "$DESKTOP_BIN" -m portfolio "${APP_ARGS[@]}"
        DESKTOP_EXIT=$?

        print "\n=================================================================="
        print "  ℹ️ 桌面端已安全退出 (状态码: $DESKTOP_EXIT)"
        if [[ "$START_MOBILE" == "1" && "$IS_MOBILE_RUNNING" == "1" ]]; then
            print "  📱 手机端后台服务 (Port 8888) 与 🌐 公网隧道 仍在持续运行中"
            print "  👉 手机端随时可通过以下地址访问："
            print "     • WiFi 局域网: http://${WIFI_IP}:8888"
            if [[ -n "$PUBLIC_URL" ]]; then
                print "     • 公网直连:    $PUBLIC_URL"
            fi
            print "  💡 若需完全停止所有后台服务，可执行：./启动Money3.command --stop"
        fi
        print "=================================================================="
        exit "$DESKTOP_EXIT"
    fi

    print "[错误] Money3本地运行环境未准备好，请先安装本目录项目依赖。"
    exit 1
else
    print "\n  📱 手机端独立模式已就绪 (桌面端已跳过)。"
    print "  • Web日志: $MOBILE_LOG"
    [[ -n "$PUBLIC_URL" ]] && print "  • 隧道日志: $NGROK_LOG"
    print "  • 若需停止服务，可按 Ctrl + C 或执行 ./启动Money3.command --stop"
    print "=================================================================="
    trap "kill $MOBILE_PID 2>/dev/null || true; pkill -f 'ngrok http 8888' 2>/dev/null || true; exit 0" INT TERM
    tail -f "$MOBILE_LOG"
fi
