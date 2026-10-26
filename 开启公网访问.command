#!/bin/zsh
set -e
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

# 统一复用启动脚本：公网访问已集成至启动器，进入独立服务模式 (手机端服务 + 公网隧道)
exec "$APP_DIR/启动Money3.command" --only-mobile "$@"
