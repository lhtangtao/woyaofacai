#!/bin/zsh
set -e
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

# ==============================================================================
# 数据同步开关配置 (Data Sync Switches)
# ------------------------------------------------------------------------------
# 选项 1：完整同步（调用长桥/嘉信 API -> 写入交易流水.xlsx -> 同步到数据库）
#   1: 开启
#   0: 关闭
SYNC_API="${SYNC_API:-0}"

# 选项 2：仅 Excel 同步（跳过 API，直接将交易流水.xlsx 中的 5 个 Sheet 同步到数据库）
#   1: 开启
#   0: 关闭
SYNC_EXCEL_ONLY="${SYNC_EXCEL_ONLY:-0}"

# 说明：
# - 若 SYNC_API=1：自动执行完整同步（API + Excel 5个Sheet入库）
# - 若 SYNC_API=0 且 SYNC_EXCEL_ONLY=1：仅将本地 Excel 5个Sheet 入库（无需网络，速度极快）
# - 若两者均为 0：不执行同步，直接启动 Money3（默认）
#
# 终端参数覆盖支持：
#   ./启动Money3.command --api        (强制完整同步后启动)
#   ./启动Money3.command --excel      (强制仅 Excel 同步后启动)
#   ./启动Money3.command --no-sync    (强制跳过同步直接启动)
# ==============================================================================

# ------------------------------------------------------------------------------
# 检查命令行参数中是否有临时覆盖开关
# ------------------------------------------------------------------------------
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
        *)
            APP_ARGS+=("$arg")
            ;;
    esac
done

# ------------------------------------------------------------------------------
# 执行前置数据同步
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
    # 寻找包含 openpyxl、longbridge、pandas 等依赖的 Python 环境
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
# 启动桌面应用
# ------------------------------------------------------------------------------
# 使用本地 .venv 启动本目录源码；不回退到未更新的旧打包程序
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
    exec "$APP_DIR/.venv/bin/python" -m portfolio "${APP_ARGS[@]}"
fi

print "[错误] Money3本地运行环境未准备好，请先安装本目录项目依赖。"
exit 1
