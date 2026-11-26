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
#   2. 公网安全直连隧道 (按可用配置自动择优: cpolar 国内节点 -> ngrok 固定域名 -> Cloudflare Tunnel)
#   3. 桌面端图形界面 (PySide6 GUI)
#   4. 崩溃自动重启守护 (Watchdog：桌面端异常崩溃自动重启，Web/隧道异常断开或假死自动拉起)
# - 数据同步参数：
#   ./启动Money3.command --api        (强制完整券商API+Excel同步后启动)
#   ./启动Money3.command --excel      (强制仅本地Excel同步后启动)
#   ./启动Money3.command --no-sync    (强制跳过同步直接启动)
# - 服务模式参数：
#   ./启动Money3.command --only-mobile     (仅启动手机后台与公网隧道，不打开桌面端GUI)
#   ./启动Money3.command --no-public       (仅启动桌面端与局域网手机端，不开启公网隧道)
#   ./启动Money3.command --no-mobile       (仅启动桌面端GUI，不启动手机端服务与公网)
#   ./启动Money3.command --tunnel=auto|ngrok|cpolar|cloudflared  (指定公网隧道引擎)
#   ./启动Money3.command --no-auto-restart (关闭崩溃自动重启守护，便于调试)
#   ./启动Money3.command --stop            (一键停止手机后台服务、公网隧道与守护进程)
# ==============================================================================

SYNC_API="${SYNC_API:-0}"
SYNC_EXCEL_ONLY="${SYNC_EXCEL_ONLY:-0}"
START_MOBILE=1
START_PUBLIC=1
START_DESKTOP=1
STOP_ALL=0
SKIP_NAV_SYNC=0
AUTO_RESTART="${AUTO_RESTART:-1}"
TUNNEL_MODE="${TUNNEL_MODE:-auto}"
CPOLAR_REGION="${CPOLAR_REGION:-cn_top}"
DERIVED_MODE=""

WATCHDOG_SCRIPT="$APP_DIR/scripts/watchdog.py"
WEB_WATCHDOG_PID_FILE="/tmp/money_web_watchdog.pid"
TUNNEL_WATCHDOG_PID_FILE="/tmp/money_tunnel_watchdog.pid"
DESKTOP_WATCHDOG_PID_FILE="/tmp/money_desktop_watchdog.pid"
DESKTOP_WATCHDOG_LOG="$APP_DIR/logs/crash_watchdog.log"
CRASH_DIR="$APP_DIR/logs/crashes"

APP_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --rebuild-derived=changed|--rebuild-derived=all)
            DERIVED_MODE="${arg#*=}"
            ;;
        --api|--sync)
            SYNC_API=1
            SYNC_EXCEL_ONLY=0
            ;;
        --excel|--no-api)
            SYNC_API=0
            SYNC_EXCEL_ONLY=1
            ;;
        --no-sync)
            SKIP_NAV_SYNC=1
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
        --tunnel=ngrok|--tunnel=cpolar|--tunnel=cloudflared|--tunnel=auto)
            TUNNEL_MODE="${arg#*=}"
            ;;
        --no-auto-restart)
            AUTO_RESTART=0
            ;;
        --stop-mobile|--stop|--stop-all)
            STOP_ALL=1
            ;;
        --help|-h)
            print "用法: ./启动Money3.command [选项]"
            print ""
            print "常规启动选项:"
            print "  (无参数)           一键启动桌面端 GUI、手机端服务与公网直连隧道 (默认推荐)"
            print "  --only-mobile      仅启动手机后台服务与公网隧道，不打开桌面端图形窗口"
            print "  --no-public        仅启动桌面端与局域网手机端，不建立公网隧道"
            print "  --no-mobile        仅启动桌面端 GUI，不启动手机端服务"
            print "  --tunnel=MODE      指定公网隧道引擎: auto | ngrok | cpolar | cloudflared"
            print "  --no-auto-restart  关闭崩溃自动重启守护机制（方便开发调试）"
            print "  --stop             一键停止手机端后台服务、公网隧道与自动重启守护"
            print ""
            print "数据同步选项:"
            print "  --api, --sync      启动前强制调用券商API完整同步数据"
            print "  --excel            启动前强制仅同步本地交易流水.xlsx"
            print "  --no-sync          跳过任何同步，直接极速启动"
            print "  --rebuild-derived=changed|all  启动服务前安全重建全部历史人员派生数据"
            exit 0
            ;;
        *)
            APP_ARGS+=("$arg")
            ;;
    esac
done

stop_pid_file() {
    local pid_file="$1"
    local expect_pattern="${2:-watchdog.py}"
    if [[ ! -f "$pid_file" ]]; then
        return 1
    fi
    local pid=""
    pid=$(<"$pid_file" 2>/dev/null || true)
    pid="${pid//[[:space:]]/}"
    if [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]]; then
        rm -f "$pid_file"
        return 1
    fi
    if kill -0 "$pid" 2>/dev/null; then
        local cmdline=""
        cmdline=$(ps -p "$pid" -o command= 2>/dev/null || true)
        if [[ -z "$expect_pattern" || "$cmdline" == *"$expect_pattern"* ]]; then
            kill "$pid" 2>/dev/null || true
            for _ in {1..15}; do
                if ! kill -0 "$pid" 2>/dev/null; then
                    break
                fi
                sleep 0.2
            done
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
            rm -f "$pid_file"
            return 0
        fi
    fi
    rm -f "$pid_file"
    return 1
}

# ------------------------------------------------------------------------------
# 停止服务处理 (--stop / --stop-all)
# ------------------------------------------------------------------------------
if [[ "$STOP_ALL" == "1" ]]; then
    stop_pid_file "$WEB_WATCHDOG_PID_FILE" "watchdog.py" && print "✅ Web 自动重启守护已停止。" || true
    stop_pid_file "$TUNNEL_WATCHDOG_PID_FILE" "watchdog.py" && print "✅ 公网隧道自动重启守护已停止。" || true
    stop_pid_file "$DESKTOP_WATCHDOG_PID_FILE" "watchdog.py" && print "✅ 桌面端自动重启守护已停止。" || true

    MOBILE_PID=$(lsof -ti tcp:8888 -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "$MOBILE_PID" ]]; then
        kill "$MOBILE_PID" 2>/dev/null || true
        print "✅ 手机端后台服务 (PID: $MOBILE_PID) 已成功停止。"
    else
        print "ℹ️ 手机端 Web 服务当前未在运行 (端口 8888 未被占用)。"
    fi

    STOPPED_ANY_TUNNEL=0
    if pgrep -f "^$APP_DIR/tools/ngrok http" &>/dev/null; then
        pkill -f "^$APP_DIR/tools/ngrok http" 2>/dev/null || true
        print "✅ 公网隧道 (ngrok) 已安全停止。"
        STOPPED_ANY_TUNNEL=1
    fi
    if pgrep -f "^$APP_DIR/tools/cpolar http" &>/dev/null; then
        pkill -f "^$APP_DIR/tools/cpolar http" 2>/dev/null || true
        print "✅ 公网隧道 (cpolar 国内高速节点) 已安全停止。"
        STOPPED_ANY_TUNNEL=1
    fi
    if pgrep -f "^$APP_DIR/tools/cloudflared tunnel --url http://127.0.0.1:8888" &>/dev/null; then
        pkill -f "^$APP_DIR/tools/cloudflared tunnel --url http://127.0.0.1:8888" 2>/dev/null || true
        print "✅ 公网隧道 (cloudflared) 已安全停止。"
        STOPPED_ANY_TUNNEL=1
    fi
    if [[ "$STOPPED_ANY_TUNNEL" == "0" ]]; then
        print "ℹ️ 公网隧道当前未在运行。"
    fi
    exit 0
fi

# Stop this project's prior Web process (and its watchdog) before synchronization or reconstruction.
if [[ "$START_MOBILE" == "1" ]]; then
    stop_pid_file "$WEB_WATCHDOG_PID_FILE" "watchdog.py" || true
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

fi

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

if [[ "$SKIP_NAV_SYNC" == "0" && -x "$APP_DIR/.venv/bin/python" ]]; then
    nohup "$APP_DIR/.venv/bin/python" -m portfolio.domestic check-recent-nav >/dev/null 2>&1 &!
fi

# Synchronize/check/rebuild before either client starts serving data.
DERIVED_ARGS=()
if [[ -n "$DERIVED_MODE" ]]; then
    DERIVED_ARGS+=("--mode" "$DERIVED_MODE")
    "$APP_DIR/.venv/bin/python" -m portfolio.derived rebuild "${DERIVED_ARGS[@]}"
else
    print "ℹ️ 两端将在后台自动检查并初始化历史缓存。"
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

    if [[ "$IS_MOBILE_RUNNING" == "0" ]]; then
        if [[ "$AUTO_RESTART" == "1" && -f "$WATCHDOG_SCRIPT" ]]; then
            nohup "$APP_DIR/.venv/bin/python" "$WATCHDOG_SCRIPT" \
                --name web --restart-on-zero --redirect-child-output \
                --pid-file "$WEB_WATCHDOG_PID_FILE" \
                --log-file "$MOBILE_LOG" \
                --crash-dir "$CRASH_DIR" \
                --context-log "$APP_DIR/logs/crash.log" \
                --context-log "$APP_DIR/logs/crash_fault.log" \
                --context-log "$MOBILE_LOG" \
                --context-log "$APP_DIR/logs/web.log" \
                -- "$APP_DIR/.venv/bin/python" -m portfolio.web.app >/dev/null 2>&1 &
        else
            nohup "$APP_DIR/.venv/bin/python" -m portfolio.web.app >> "$MOBILE_LOG" 2>&1 &
        fi
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
# 2. 启动并检测公网直连隧道 (cpolar -> ngrok -> cloudflared)
# ------------------------------------------------------------------------------
CPOLAR_BIN="$APP_DIR/tools/cpolar"
CPOLAR_LOG="/tmp/money_cpolar.log"
CPOLAR_CFG="$HOME/.cpolar/cpolar.yml"
NGROK_BIN="$APP_DIR/tools/ngrok"
NGROK_LOG="/tmp/money_ngrok.log"
CLOUDFLARED_BIN="$APP_DIR/tools/cloudflared"
CLOUDFLARED_LOG="/tmp/money_cloudflared.log"
PUBLIC_URL=""
IS_TUNNEL_RUNNING=0
TUNNEL_PROVIDER=""
ACTIVE_TUNNEL_LOG="$CLOUDFLARED_LOG"

if [[ -n "${CPOLAR_AUTHTOKEN:-}" && -x "$CPOLAR_BIN" ]]; then
    "$CPOLAR_BIN" authtoken "$CPOLAR_AUTHTOKEN" >/dev/null 2>&1 || true
fi

HAS_CPOLAR_AUTH=0
if [[ -x "$CPOLAR_BIN" && -f "$CPOLAR_CFG" ]] && grep -Eq '^[[:space:]]*authtoken:[[:space:]]*[^[:space:]#]+' "$CPOLAR_CFG" 2>/dev/null; then
    HAS_CPOLAR_AUTH=1
fi

HAS_NGROK_AUTH=0
if [[ -x "$NGROK_BIN" ]] && "$NGROK_BIN" config check >/dev/null 2>&1; then
    HAS_NGROK_AUTH=1
fi

extract_local_4040_tunnel_url() {
    "$APP_DIR/.venv/bin/python" - <<'PY' 2>/dev/null || true
import json
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open("http://127.0.0.1:4040/api/tunnels", timeout=1.5) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    urls = [t.get("public_url", "") for t in data.get("tunnels", []) if isinstance(t, dict)]
    for u in urls:
        if u.startswith("https://"):
            print(u)
            raise SystemExit(0)
    for u in urls:
        if u.startswith("http://"):
            print(u)
            raise SystemExit(0)
except Exception:
    pass
PY
}

extract_cpolar_public_url() {
    local url=""
    url=$(extract_local_4040_tunnel_url)
    if [[ -n "$url" ]]; then
        print -r -- "$url"
        return 0
    fi
    if [[ -f "$CPOLAR_LOG" ]]; then
        grep -Eo 'https://[a-zA-Z0-9.-]+\.(cpolar\.(top|cn|io|com)|vip\.cpolar\.cn)' "$CPOLAR_LOG" 2>/dev/null | tail -n 1 || true
    fi
}

extract_ngrok_public_url() {
    local url=""
    url=$(extract_local_4040_tunnel_url)
    if [[ -n "$url" ]]; then
        print -r -- "$url"
        return 0
    fi
    if [[ -f "$NGROK_LOG" ]]; then
        grep -Eo 'https://[a-zA-Z0-9.-]+\.ngrok(-free)?\.(dev|app|io)' "$NGROK_LOG" 2>/dev/null | tail -n 1 || true
    fi
}

if [[ "$START_PUBLIC" == "1" && "$IS_MOBILE_RUNNING" == "1" ]]; then
    TUNNEL_WATCHDOG_ALIVE=1
    if [[ "$AUTO_RESTART" == "1" && -f "$WATCHDOG_SCRIPT" ]]; then
        TUNNEL_WATCHDOG_ALIVE=0
        if [[ -f "$TUNNEL_WATCHDOG_PID_FILE" ]]; then
            local_tunnel_wd_pid=$(<"$TUNNEL_WATCHDOG_PID_FILE" 2>/dev/null || true)
            if [[ -n "$local_tunnel_wd_pid" ]] && kill -0 "$local_tunnel_wd_pid" 2>/dev/null; then
                TUNNEL_WATCHDOG_ALIVE=1
            fi
        fi
    fi

    SELECTED_ENGINE=""
    case "$TUNNEL_MODE" in
        cpolar)
            if [[ "$HAS_CPOLAR_AUTH" == "1" ]]; then
                SELECTED_ENGINE="cpolar"
            else
                print "⚠️ 未检测到 cpolar Authtoken（~/.cpolar/cpolar.yml），本次回退选择其他可用隧道。"
            fi
            ;;
        ngrok)
            if [[ "$HAS_NGROK_AUTH" == "1" ]]; then
                SELECTED_ENGINE="ngrok"
            else
                print "⚠️ ngrok 配置检查未通过，本次回退选择 Cloudflare Tunnel。"
            fi
            ;;
        cloudflared)
            SELECTED_ENGINE="cloudflared"
            ;;
    esac

    if [[ -z "$SELECTED_ENGINE" ]]; then
        if [[ "$HAS_CPOLAR_AUTH" == "1" ]]; then
            SELECTED_ENGINE="cpolar"
        elif [[ "$HAS_NGROK_AUTH" == "1" ]]; then
            SELECTED_ENGINE="ngrok"
        elif [[ -x "$CLOUDFLARED_BIN" ]]; then
            SELECTED_ENGINE="cloudflared"
        fi
    fi

    if [[ "$SELECTED_ENGINE" == "cpolar" ]]; then
        TUNNEL_PROVIDER="cpolar"
        ACTIVE_TUNNEL_LOG="$CPOLAR_LOG"
        if pgrep -f "^$NGROK_BIN http" >/dev/null 2>&1 || pgrep -f "^$CLOUDFLARED_BIN tunnel --url http://127.0.0.1:8888" >/dev/null 2>&1; then
            stop_pid_file "$TUNNEL_WATCHDOG_PID_FILE" "watchdog.py" || true
            pkill -f "^$NGROK_BIN http" 2>/dev/null || true
            pkill -f "^$CLOUDFLARED_BIN tunnel --url http://127.0.0.1:8888" 2>/dev/null || true
            TUNNEL_WATCHDOG_ALIVE=0
        fi

        if [[ "$TUNNEL_WATCHDOG_ALIVE" == "1" ]] \
            && pgrep -f "^$CPOLAR_BIN http" >/dev/null 2>&1 \
            && curl -sf --noproxy '*' --max-time 2 http://127.0.0.1:4040/api/tunnels >/dev/null 2>&1; then
            PUBLIC_URL=$(extract_cpolar_public_url)
        fi

        if [[ -n "$PUBLIC_URL" ]]; then
            IS_TUNNEL_RUNNING=1
        else
            stop_pid_file "$TUNNEL_WATCHDOG_PID_FILE" "watchdog.py" || true
            pkill -f "^$CPOLAR_BIN http" 2>/dev/null || true
            : > "$CPOLAR_LOG"
            # 清理代理变量让 cpolar 直连国内高速节点 (cn_top)
            if [[ "$AUTO_RESTART" == "1" && -f "$WATCHDOG_SCRIPT" ]]; then
                nohup env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
                    "$APP_DIR/.venv/bin/python" "$WATCHDOG_SCRIPT" \
                    --name tunnel --restart-on-zero --redirect-child-output \
                    --pid-file "$TUNNEL_WATCHDOG_PID_FILE" \
                    --log-file "$CPOLAR_LOG" \
                    --crash-dir "$CRASH_DIR" \
                    --context-log "$CPOLAR_LOG" \
                    --health-url "http://127.0.0.1:4040/api/tunnels" \
                    --health-interval 30 --health-grace 20 --health-failures 3 \
                    -- "$CPOLAR_BIN" http -region="$CPOLAR_REGION" -log=stdout -log-level=INFO 8888 >/dev/null 2>&1 &
            else
                nohup env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
                    "$CPOLAR_BIN" http -region="$CPOLAR_REGION" -log=stdout -log-level=INFO 8888 > "$CPOLAR_LOG" 2>&1 &
            fi
            for i in {1..40}; do
                sleep 0.3
                PUBLIC_URL=$(extract_cpolar_public_url)
                if [[ -n "$PUBLIC_URL" ]]; then
                    IS_TUNNEL_RUNNING=1
                    break
                fi
            done
        fi
    elif [[ "$SELECTED_ENGINE" == "ngrok" ]]; then
        TUNNEL_PROVIDER="ngrok"
        ACTIVE_TUNNEL_LOG="$NGROK_LOG"
        if pgrep -f "^$CPOLAR_BIN http" >/dev/null 2>&1 || pgrep -f "^$CLOUDFLARED_BIN tunnel --url http://127.0.0.1:8888" >/dev/null 2>&1; then
            stop_pid_file "$TUNNEL_WATCHDOG_PID_FILE" "watchdog.py" || true
            pkill -f "^$CPOLAR_BIN http" 2>/dev/null || true
            pkill -f "^$CLOUDFLARED_BIN tunnel --url http://127.0.0.1:8888" 2>/dev/null || true
            TUNNEL_WATCHDOG_ALIVE=0
        fi

        if [[ "$TUNNEL_WATCHDOG_ALIVE" == "1" ]] \
            && pgrep -f "^$NGROK_BIN http" >/dev/null 2>&1 \
            && curl -sf --noproxy '*' --max-time 2 http://127.0.0.1:4040/api/tunnels >/dev/null 2>&1; then
            PUBLIC_URL=$(extract_ngrok_public_url)
        fi

        if [[ -n "$PUBLIC_URL" ]]; then
            IS_TUNNEL_RUNNING=1
        else
            stop_pid_file "$TUNNEL_WATCHDOG_PID_FILE" "watchdog.py" || true
            pkill -f "^$NGROK_BIN http" 2>/dev/null || true
            : > "$NGROK_LOG"
            # 清除代理环境变量防 ERR_NGROK_9009；显式转发到 http://127.0.0.1:8888 避免 macOS IPv6 ::1 握手异常触发 ERR_NGROK_3004
            if [[ "$AUTO_RESTART" == "1" && -f "$WATCHDOG_SCRIPT" ]]; then
                nohup env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
                    "$APP_DIR/.venv/bin/python" "$WATCHDOG_SCRIPT" \
                    --name tunnel --restart-on-zero --redirect-child-output \
                    --pid-file "$TUNNEL_WATCHDOG_PID_FILE" \
                    --log-file "$NGROK_LOG" \
                    --crash-dir "$CRASH_DIR" \
                    --context-log "$NGROK_LOG" \
                    --health-url "http://127.0.0.1:4040/api/tunnels" \
                    --health-interval 30 --health-grace 20 --health-failures 3 \
                    -- "$NGROK_BIN" http http://127.0.0.1:8888 --log=stdout --log-level=info >/dev/null 2>&1 &
            else
                nohup env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
                    "$NGROK_BIN" http http://127.0.0.1:8888 --log=stdout --log-level=info > "$NGROK_LOG" 2>&1 &
            fi
            for i in {1..40}; do
                sleep 0.3
                PUBLIC_URL=$(extract_ngrok_public_url)
                if [[ -n "$PUBLIC_URL" ]]; then
                    IS_TUNNEL_RUNNING=1
                    break
                fi
            done
        fi
    elif [[ -x "$CLOUDFLARED_BIN" ]]; then
        TUNNEL_PROVIDER="cloudflared"
        ACTIVE_TUNNEL_LOG="$CLOUDFLARED_LOG"
        if pgrep -f "^$NGROK_BIN http" >/dev/null 2>&1 || pgrep -f "^$CPOLAR_BIN http" >/dev/null 2>&1; then
            stop_pid_file "$TUNNEL_WATCHDOG_PID_FILE" "watchdog.py" || true
            pkill -f "^$NGROK_BIN http" 2>/dev/null || true
            pkill -f "^$CPOLAR_BIN http" 2>/dev/null || true
            TUNNEL_WATCHDOG_ALIVE=0
        fi
        # 先检查是否已有健康运行中的 cloudflared 隧道（受 watchdog 托管且 /ready 为 200）并提取 URL
        if [[ "$TUNNEL_WATCHDOG_ALIVE" == "1" ]] \
            && pgrep -f "^$CLOUDFLARED_BIN tunnel --url http://127.0.0.1:8888" >/dev/null 2>&1 \
            && [[ -f "$CLOUDFLARED_LOG" ]] \
            && curl -sf --noproxy '*' --max-time 2 http://127.0.0.1:20241/ready >/dev/null 2>&1; then
            PUBLIC_URL=$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" | tail -n 1 || true)
        fi

        if [[ -n "$PUBLIC_URL" ]]; then
            IS_TUNNEL_RUNNING=1
        else
            # 若未运行、无 URL 或隧道假死不健康，清理旧守护与旧进程并启动新的 cloudflared Quick Tunnel（自动协商协议 + IPv4 + 端到端双向保活探活）
            stop_pid_file "$TUNNEL_WATCHDOG_PID_FILE" "watchdog.py" || true
            pkill -f "^$CLOUDFLARED_BIN tunnel --url http://127.0.0.1:8888" 2>/dev/null || true
            : > "$CLOUDFLARED_LOG"
            if [[ "$AUTO_RESTART" == "1" && -f "$WATCHDOG_SCRIPT" ]]; then
                nohup "$APP_DIR/.venv/bin/python" "$WATCHDOG_SCRIPT" \
                    --name tunnel --restart-on-zero --redirect-child-output \
                    --pid-file "$TUNNEL_WATCHDOG_PID_FILE" \
                    --log-file "$CLOUDFLARED_LOG" \
                    --crash-dir "$CRASH_DIR" \
                    --context-log "$CLOUDFLARED_LOG" \
                    --health-url "http://127.0.0.1:20241/ready" \
                    --health-interval 25 --health-grace 35 --health-failures 3 \
                    --keepalive-url-regex 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' \
                    --keepalive-path '/api/auth/me' \
                    -- "$CLOUDFLARED_BIN" tunnel --url http://127.0.0.1:8888 --edge-ip-version 4 --metrics 127.0.0.1:20241 --no-autoupdate >/dev/null 2>&1 &
            else
                nohup "$CLOUDFLARED_BIN" tunnel --url http://127.0.0.1:8888 --edge-ip-version 4 --metrics 127.0.0.1:20241 --no-autoupdate > "$CLOUDFLARED_LOG" 2>&1 &
            fi
            for i in {1..40}; do
                sleep 0.3
                PUBLIC_URL=$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$CLOUDFLARED_LOG" 2>/dev/null | tail -n 1 || true)
                if [[ -n "$PUBLIC_URL" ]]; then
                    IS_TUNNEL_RUNNING=1
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
    if [[ "$TUNNEL_PROVIDER" == "cpolar" ]]; then
        print "  ✅ 2. 公网直连高速隧道已建立 (cpolar 国内节点 ${CPOLAR_REGION} · 已复制网址！)"
    elif [[ "$TUNNEL_PROVIDER" == "ngrok" ]]; then
        print "  ✅ 2. 公网直连安全隧道已建立 (ngrok 固定域名 · 已自动复制网址到剪贴板！)"
    else
        print "  ✅ 2. 公网直连安全隧道已建立 (Cloudflare Tunnel · 已自动复制网址到剪贴板！)"
    fi
else
    print "  ℹ️ 2. 公网隧道未运行 (如需开启请确认已配置 tools/ngrok、tools/cpolar 或 tools/cloudflared)"
fi

if [[ "$START_DESKTOP" == "1" ]]; then
    print "  ✅ 3. 正在启动桌面端图形界面 (PySide6)..."
else
    print "  ℹ️ 3. 手机端独立模式 (已跳过桌面端窗口)"
fi
if [[ "$AUTO_RESTART" == "1" && -f "$WATCHDOG_SCRIPT" ]]; then
    print "  🛡️ 4. 崩溃自动重启守护已开启 (桌面端/Web/隧道，可用 --no-auto-restart 关闭)"
else
    print "  ℹ️ 4. 崩溃自动重启守护未开启"
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
print "  👥【各人员手机/Web 登录账号】(账号即名字，初始密码为拼音+123)"
print "     • 用户名: 个人     密码: geren123     (绑定个人专属账本)"
print "     • 用户名: 华       密码: hua123       (绑定华专属账本)"
print "     • 用户名: 巴小     密码: baxiao123    (绑定巴小专属账本)"
print "     • 用户名: 张薇     密码: zhangwei123  (绑定张薇专属账本)"
print "     • 用户名: 拉科     密码: lake123      (绑定拉科专属账本)"
print "     • 用户名: 摸总     密码: mozong123    (绑定摸总专属账本)"
print "     • 用户名: 百万     密码: baiwan123    (绑定百万专属账本)"
print "     • 用户名: 钱       密码: qian123      (绑定钱专属账本)"
print "     • 用户名: 陈行     密码: chenhang123  (绑定陈行专属账本)"
print "     (注: 手机浏览器直接打开上方网址，登录后即可查看对应人员专属持仓与盈亏)"
print "=================================================================="

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
        mkdir -p "$APP_DIR/logs" "$CRASH_DIR"
        set +e
        if [[ "$AUTO_RESTART" == "1" && -f "$WATCHDOG_SCRIPT" ]]; then
            "$APP_DIR/.venv/bin/python" "$WATCHDOG_SCRIPT" \
                --name desktop --notify \
                --pid-file "$DESKTOP_WATCHDOG_PID_FILE" \
                --log-file "$DESKTOP_WATCHDOG_LOG" \
                --crash-dir "$CRASH_DIR" \
                --context-log "$APP_DIR/logs/crash.log" \
                --context-log "$APP_DIR/logs/crash_fault.log" \
                --context-log "$APP_DIR/logs/money3.log" \
                -- "$DESKTOP_BIN" -m portfolio "${APP_ARGS[@]}"
        else
            "$DESKTOP_BIN" -m portfolio "${APP_ARGS[@]}"
        fi
        DESKTOP_EXIT=$?
        set -e

        print "\n=================================================================="
        if [[ "$DESKTOP_EXIT" == "0" ]]; then
            print "  ℹ️ 桌面端已安全退出 (状态码: $DESKTOP_EXIT)"
        else
            print "  ⚠️ 桌面端异常退出或触发连续崩溃熔断 (状态码: $DESKTOP_EXIT)"
            print "  • 守护日志: $DESKTOP_WATCHDOG_LOG"
            print "  • 异常日志: $APP_DIR/logs/crash.log"
            print "  • 原生堆栈: $APP_DIR/logs/crash_fault.log"
            print "  • 现场快照: $CRASH_DIR"
        fi
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
    [[ -n "$PUBLIC_URL" ]] && print "  • 隧道日志: $ACTIVE_TUNNEL_LOG"
    print "  • 若需停止服务，可按 Ctrl + C 或执行 ./启动Money3.command --stop"
    print "=================================================================="
    trap 'stop_pid_file "$WEB_WATCHDOG_PID_FILE" "watchdog.py" || true; stop_pid_file "$TUNNEL_WATCHDOG_PID_FILE" "watchdog.py" || true; kill "$MOBILE_PID" 2>/dev/null || true; pkill -f "^$NGROK_BIN http" 2>/dev/null || true; pkill -f "^$CPOLAR_BIN http" 2>/dev/null || true; pkill -f "^$CLOUDFLARED_BIN tunnel --url http://127.0.0.1:8888" 2>/dev/null || true; exit 0' INT TERM
    tail -f "$MOBILE_LOG"
fi
