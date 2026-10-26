#!/bin/zsh
set -e
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

# ==============================================================================
# Money Portfolio 双端联合启动器 (Desktop GUI + Mobile Web/PWA)
# ==============================================================================
# 功能说明：
# - 默认行为：执行单个脚本，同时启动桌面端图形界面 (PySide6) 与手机端全量服务 (Port 8888)
# - 数据同步参数：
#   ./启动Money3.command --api        (强制完整券商API+Excel同步后启动)
#   ./启动Money3.command --excel      (强制仅本地Excel同步后启动)
#   ./启动Money3.command --no-sync    (强制跳过同步直接启动)
# - 服务模式参数：
#   ./启动Money3.command --only-mobile (仅启动手机端后台服务，不打开桌面端GUI)
#   ./启动Money3.command --no-mobile   (仅启动桌面端GUI，不启动手机端服务)
#   ./启动Money3.command --stop-mobile (停止正在运行的手机端后台服务)
# ==============================================================================

SYNC_API="${SYNC_API:-0}"
SYNC_EXCEL_ONLY="${SYNC_EXCEL_ONLY:-0}"
START_MOBILE=1
START_DESKTOP=1
STOP_MOBILE=0

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
        --only-mobile)
            START_MOBILE=1
            START_DESKTOP=0
            ;;
        --no-mobile)
            START_MOBILE=0
            ;;
        --stop-mobile|--stop)
            STOP_MOBILE=1
            ;;
        --help|-h)
            print "用法: ./启动Money3.command [选项]"
            print ""
            print "常规启动选项:"
            print "  (无参数)          同时启动桌面端 GUI 与手机端全量服务 (默认推荐)"
            print "  --only-mobile    仅启动手机端后台服务 (iPhone/iPad/远程)，不打开桌面端窗口"
            print "  --no-mobile      仅启动桌面端 GUI，不启动手机端服务"
            print "  --stop-mobile    停止正在运行的手机端后台服务"
            print "  --stop           同 --stop-mobile"
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
# 停止手机端服务处理
# ------------------------------------------------------------------------------
if [[ "$STOP_MOBILE" == "1" ]]; then
    MOBILE_PID=$(lsof -ti tcp:8888 -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "$MOBILE_PID" ]]; then
        kill "$MOBILE_PID" 2>/dev/null || true
        print "✅ 手机端后台服务 (PID: $MOBILE_PID) 已成功停止。"
    else
        print "ℹ️ 手机端服务当前未在运行 (端口 8888 未被占用)。"
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
    # 检查端口 8888 是否已有服务运行
    EXISTING_PID=$(lsof -ti tcp:8888 -sTCP:LISTEN 2>/dev/null || true)
    if [[ -n "$EXISTING_PID" ]]; then
        if ps -p "$EXISTING_PID" -o command= 2>/dev/null | grep -q "portfolio.web.app"; then
            MOBILE_PID="$EXISTING_PID"
            IS_MOBILE_RUNNING=1
        fi
    fi

    # 若未运行，启动后台守护进程
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

    # 获取 WiFi 局域网 IP 与 Tailscale 远程 IP
    WIFI_IP=$(ifconfig | grep -E '^\s*inet ' | awk '{print $2}' | grep -v '^127\.' | grep -v '^198\.18\.' | grep -v '^100\.' | head -n 1)
    [[ -z "$WIFI_IP" ]] && WIFI_IP="127.0.0.1"

    TAILSCALE_IP=$(ifconfig | grep -E '^\s*inet ' | awk '{print $2}' | grep '^100\.' | head -n 1)
    if [[ -z "$TAILSCALE_IP" ]] && which tailscale &>/dev/null; then
        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null | head -n 1)
    fi

    print "\n=================================================================="
    print "  🚀 Money Portfolio 双端联合启动器 (桌面端 + 手机端)"
    print "=================================================================="
    if [[ "$IS_MOBILE_RUNNING" == "1" ]]; then
        print "  ✅ 手机端后台服务已就绪！ (PID: $MOBILE_PID, 端口: 8888)"
    else
        print "  ⚠️ 手机端服务正在启动中 (端口: 8888)..."
    fi
    print ""
    print "  📱【1. 家中同一 WiFi 局域网访问 (iPhone / iPad)】"
    print "      打开 iPhone Safari 浏览器输入："
    print "      👉 http://${WIFI_IP}:8888"
    print ""
    if [[ -n "$TAILSCALE_IP" ]]; then
        print "  🌐【2. 户外公网 5G 远程访问 (Tailscale 已连接)】"
        print "      打开 iPhone Safari 浏览器输入："
        print "      👉 http://${TAILSCALE_IP}:8888"
        print ""
    else
        print "  🌐【2. 户外公网 5G 远程访问 (Tailscale)】"
        print "      若需在户外通过 5G 随时访问家中 Mac："
        print "      • 手机与 Mac 均下载 Tailscale (App Store 免费) 并登录同账号"
        print "      • 连接后即可通过专属 Tailscale 网址随时随地直连访问！"
        print ""
    fi
    print "  📲【3. 一键添加为 iPhone 原生全屏桌面 App】"
    print "      在 Safari 打开上述网址 -> 点击底部「分享」按钮 ->「添加到主屏幕」"
    print "=================================================================="
fi

# ------------------------------------------------------------------------------
# 2. 执行前置数据同步 (可选)
# ------------------------------------------------------------------------------
DO_SYNC=0
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
# 3. 启动桌面端应用 (PySide6) 或保持手机独立模式
# ------------------------------------------------------------------------------
if [[ "$START_DESKTOP" == "1" ]]; then
    if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
        print "  🖥️ 正在启动桌面端图形界面 (PySide6)..."
        print "------------------------------------------------------------------\n"
        "$APP_DIR/.venv/bin/python" -m portfolio "${APP_ARGS[@]}"
        DESKTOP_EXIT=$?

        print "\n=================================================================="
        print "  ℹ️ 桌面端已安全退出 (退出状态码: $DESKTOP_EXIT)"
        if [[ "$START_MOBILE" == "1" && "$IS_MOBILE_RUNNING" == "1" ]]; then
            print "  📱 手机端后台服务 (PID: $MOBILE_PID) 仍在持续运行中"
            print "  👉 手机随时可通过以下地址访问："
            print "     http://${WIFI_IP}:8888"
            if [[ -n "$TAILSCALE_IP" ]]; then
                print "     http://${TAILSCALE_IP}:8888"
            fi
            print "  💡 若需停止手机后台服务，可执行：./启动手机服务.command --stop"
        fi
        print "=================================================================="
        exit "$DESKTOP_EXIT"
    fi

    print "[错误] Money3本地运行环境未准备好，请先安装本目录项目依赖。"
    exit 1
else
    print "\n  📱 手机端专属模式已激活 (桌面端已跳过启动)。"
    print "  服务运行日志：$MOBILE_LOG"
    print "  若需停止服务，可按 Ctrl + C 或运行 ./启动手机服务.command --stop"
    print "=================================================================="
    trap "kill $MOBILE_PID 2>/dev/null || true; exit 0" INT TERM
    tail -f "$MOBILE_LOG"
fi
