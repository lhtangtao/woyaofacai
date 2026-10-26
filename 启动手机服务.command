#!/bin/zsh
set -e
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

# 统一复用启动脚本，进入手机专属独立运行模式（不启动桌面端GUI）
exec "$APP_DIR/启动Money3.command" --only-mobile "$@"
