#!/bin/bash
# Beacon 面板一键部署 / 升级（root 运行，重复执行即升级）
# 用法: BEACON_REPO=https://github.com/你的用户名/beacon.git \
#       BEACON_PUBLIC_URL=https://probe.example.com bash install-server.sh
set -euo pipefail
REPO=${BEACON_REPO:?请设置 BEACON_REPO=https://github.com/<用户名>/<仓库>.git}
DIR=/opt/beacon; ENVF=/etc/beacon-server.env; PORT=${PORT:-8080}

need() { command -v "$1" >/dev/null 2>&1 || { apt-get install -y "$2" 2>/dev/null || dnf install -y "$2" 2>/dev/null || apk add "$2"; }; }
need git git; need python3 python3; need openssl openssl

if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone --depth 1 "$REPO" "$DIR"; fi
id beacon >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin beacon
mkdir -p /var/lib/beacon && chown beacon /var/lib/beacon

if [ ! -f "$ENVF" ]; then   # 首次部署才生成配置，升级不会覆盖密钥和密码
  key=$(openssl rand -hex 16); pw=${BEACON_ADMIN_PASSWORD:-$(openssl rand -hex 12)}
  umask 077
  { echo "BEACON_API_KEYS=$key"; echo "BEACON_ADMIN_PASSWORD=$pw"; echo "BEACON_DATA_DIR=/var/lib/beacon"; echo "PORT=$PORT"
    [ -n "${BEACON_PUBLIC_URL:-}" ] && { echo "BEACON_PUBLIC_URL=$BEACON_PUBLIC_URL"; echo "BEACON_TRUST_PROXY=true"; }
  } > "$ENVF"
  echo "管理密码: $pw"; echo "客户端 API 密钥: $key   (保存在 $ENVF)"
fi

cat > /etc/systemd/system/beacon.service <<UNIT
[Unit]
Description=Beacon probe server
After=network.target
[Service]
User=beacon
EnvironmentFile=$ENVF
ExecStart=/usr/bin/python3 $DIR/server.py
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable beacon >/dev/null 2>&1 && systemctl restart beacon
sleep 1; curl -fsS "http://127.0.0.1:$PORT/api/health" && echo " ← 面板已启动，监听 :$PORT（请用 Nginx 加 HTTPS 后再对外开放）"
