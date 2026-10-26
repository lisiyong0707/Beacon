# Beacon 探针

多节点 Linux 监控面板：**零依赖 Python 3 服务端** + **一行命令安装的 Bash 客户端** + **实时 Web 仪表盘**。

不需要数据库，不需要 pip，不需要 Docker。一台小机器就能跑，被监控的机器只需要 bash 和 curl。

## 功能

- **仪表盘**：每台服务器一张卡片，显示 CPU / 内存 / 硬盘进度条、网络上下行速率与累计流量、国旗、系统与硬件规格、运行时间
- **三网 TCP Ping**：电信 / 联通 / 移动延迟与丢包，绿（≤100ms）、黄（≤300ms）、红（超时或更高）三色徽章，附近 1 小时延迟趋势图
- **详情页**：点击卡片进入。CPU 型号、架构、虚拟化、内核、负载 1/5/15、进程数、Swap；1 / 6 / 12 / 24 小时的资源、网络、延迟、丢包图表；事件日志；可复制分享链接
- **告警**：CPU / 内存 / 硬盘 / 丢包 / 延迟阈值可调，连续 3 分钟越线才触发；节点离线立即触发；触发和恢复时推送 Webhook
- **管理后台**（`/admin`）：节点改名、删除并屏蔽、解除屏蔽、告警设置、生成客户端安装命令
- **其他**：国家筛选标签、深浅色主题（记住选择）、手机适配、公开接口不暴露任何服务器 IP

## 工作方式

```
被监控服务器 ──每分钟 cron 运行 agent.sh──▶ POST /api/report ──▶ 面板服务器 (server.py)
                                                                    │
                                              浏览器 ◀── index.html / admin.html
```

客户端每分钟采集一次数据并上报，服务端在内存中保留每台节点最近 24 小时（每分钟一个采样点），并定时保存到本地的 `beacon.json`。客户端脚本由面板自己提供下载，所以被监控的机器**不需要访问 GitHub**。

## 快速部署

### 1. 部署面板

在面板服务器上用 root 执行（需要 Python 3 和 git，脚本会尝试自动安装）：

```bash
curl -fsSL https://raw.githubusercontent.com/lisiyong0707/Beacon/main/install-server.sh | \
  BEACON_REPO=https://github.com/lisiyong0707/Beacon.git \
  BEACON_PUBLIC_URL=https://probe.你的域名.com \
  bash
```

脚本会克隆代码到 `/opt/beacon`、创建专用的 `beacon` 用户、随机生成管理密码和客户端 API 密钥（**请立刻记下**，保存在 `/etc/beacon-server.env`）、写好 systemd 服务并启动。

服务器连不上 GitHub 时，可以先把代码用 `scp` 传到 `/opt/beacon`，再按下面的“手动部署”操作。

没有域名、只想先试用，可以去掉 `BEACON_PUBLIC_URL`，直接访问 `http://面板IP:8080`。

### 2. 配置 HTTPS（强烈建议）

管理密码和 API 密钥通过请求头传输，对外开放前请用反向代理加 HTTPS。Nginx 示例：

```nginx
server {
    listen 80;
    server_name probe.你的域名.com;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

然后执行 `certbot --nginx -d probe.你的域名.com` 申请证书，防火墙只放行 80 和 443，不要对外开放 8080。

### 3. 添加被监控服务器

打开 `https://probe.你的域名.com/admin`，用管理密码登录，在“安装客户端”里复制命令，到每台被监控服务器上用 root 执行：

```bash
curl -fsSL 'https://probe.你的域名.com/install.sh?key=你的API密钥' | sh
```

它会下载客户端到 `/usr/local/bin/beacon-agent`，把地址和密钥写入 `/etc/beacon.env`，并添加一条每分钟运行一次的 cron 任务。约一分钟后，节点就会出现在仪表盘上。

客户端依赖：`bash`、`curl`、`cron`、`awk`、`df`。精简系统缺 cron 时：

```bash
apt install -y cron      # Debian / Ubuntu
dnf install -y cronie    # CentOS / Rocky
```

Alpine 默认没有 bash，需要先 `apk add bash coreutils curl`。

## 手动部署

不使用一键脚本时：

```bash
mkdir -p /opt/beacon && cd /opt/beacon      # 放入 server.py index.html admin.html agent.sh
BEACON_API_KEYS='key1,key2' BEACON_ADMIN_PASSWORD='强密码' python3 server.py
```

要常驻运行，请自行编写 systemd 服务，并通过 `EnvironmentFile` 注入环境变量。

## 服务端环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `BEACON_API_KEYS` | **必填** | 客户端上报密钥，多个用逗号分隔 |
| `BEACON_ADMIN_PASSWORD` | **必填** | 管理后台密码 |
| `PORT` | `8080` | 监听端口 |
| `BEACON_PUBLIC_URL` | 取请求的 Host | 生成安装脚本时使用的对外地址，反向代理时建议设置 |
| `BEACON_DATA_DIR` | 程序目录 | 数据文件 `beacon.json` 所在目录 |
| `BEACON_OFFLINE_SECONDS` | `90` | 超过该时间没有上报则判为离线 |
| `BEACON_HISTORY` | `1440` | 每个节点保留的采样点数（每分钟 1 个，1440 即 24 小时） |
| `BEACON_TRUST_PROXY` | 关闭 | 位于反向代理之后时设为 `true`，用 `X-Forwarded-For` 识别真实 IP（用于登录限流） |

## 客户端环境变量

写在被监控机器的 `/etc/beacon.env` 里，下一分钟生效：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `T_CT` | `202.96.209.133:53` | 电信 Ping 目标，格式 `host:port` |
| `T_CU` | `210.22.97.1:53` | 联通 Ping 目标 |
| `T_CM` | `211.136.17.107:53` | 移动 Ping 目标 |

例如追加一行 `export T_CT=1.2.3.4:443`。目标需要能通过 TCP 连通，默认值是各家运营商 DNS 的 53 端口，若某个地址不通，请换成你自己的目标。

## 告警

在 `/admin` 的“告警设置”中调整：

| 指标 | 默认阈值 | 触发条件 |
|---|---|---|
| CPU / 内存 / 硬盘 | 90% | 连续 3 分钟高于阈值 |
| 丢包率 | 30% | 连续 3 分钟高于阈值 |
| TCP 延迟 | 300 ms | 连续 3 分钟高于阈值 |
| Ping 超时 | — | 连续 3 分钟全部超时 |
| 节点离线 | — | 超过 `BEACON_OFFLINE_SECONDS` 没有上报 |

填写 Webhook 地址后，告警**触发**和**恢复**时会各发一次 POST，请求体为：

```json
{"text": "🔴 节点名: CPU 过高", "content": "🔴 节点名: CPU 过高"}
```

`text` 和 `content` 内容相同，可直接对接 Slack 和 Discord 风格的接口；Telegram 等格式不同的接口需要加一层转发。事件日志是从已保存的历史数据中按需计算的，不占额外存储。

## 接口

| 接口 | 认证 | 说明 |
|---|---|---|
| `GET /api/health` | 无 | 健康检查 |
| `GET /api/nodes` | 无 | 节点列表（含近 1 小时采样），不含 IP |
| `GET /api/nodes/<id>?range=秒数` | 无 | 节点详情、事件、阈值，`range` 范围 300–86400 |
| `POST /api/report` | `X-API-Key` | 客户端上报 |
| `GET /install.sh?key=...` | 密钥 | 生成客户端安装脚本 |
| `GET /api/admin/info` | `X-Admin-Password` | 后台数据 |
| `POST /api/admin/settings` | `X-Admin-Password` | 修改阈值和 Webhook |
| `POST /api/nodes/<id>` | `X-Admin-Password` | 节点改名，body 为 `{"name": "..."}` |
| `DELETE /api/nodes/<id>` | `X-Admin-Password` | 删除并屏蔽节点 |
| `POST /api/admin/unblock` | `X-Admin-Password` | 解除屏蔽，body 为 `{"id": "..."}` |

## 安全说明

- 必须设置管理密码和至少一个 API 密钥，否则服务端拒绝启动
- 管理接口同一 IP 5 分钟内失败 5 次会被限流
- 所有上报字段都经过白名单过滤和范围限制，请求体上限 16 KB，节点数上限 200
- 密钥和密码使用常数时间比较；页面带有 CSP、`X-Frame-Options` 等安全响应头
- 安装脚本只会为已配置的 API 密钥生成，并校验 Host，不会生成任意内容
- 数据文件 `beacon.json` 为明文，请保护好服务器本身；`.gitignore` 已排除它，不要把它提交到仓库

## 升级

在面板服务器上重新运行部署脚本即可，会拉取最新代码并重启，**不会覆盖已有的密码、密钥和历史数据**：

```bash
BEACON_REPO=https://github.com/lisiyong0707/Beacon.git bash /opt/beacon/install-server.sh
```

如果更新了 `agent.sh`，各节点重新执行一遍安装命令即可升级客户端，不会重复添加 cron。

## 常见问题

**客户端手动测试**

```bash
. /etc/beacon.env && /usr/local/bin/beacon-agent; echo "退出码: $?"
```

| 退出码 | 原因 |
|---|---|
| 0 | 正常 |
| 22 | 服务端拒绝：密钥错误（401）或节点已被屏蔽（403） |
| 7 / 28 | 连不上面板或超时，检查域名、防火墙、证书 |

**仪表盘一直没有节点**：先看客户端退出码；再确认 cron 在运行（`systemctl status cron`）。

**国旗显示成地球图标**：客户端需要能访问 `ipinfo.io` 来识别国家，不通只影响国旗显示。

**克隆的虚拟机只显示一个节点**：节点 ID 来自 `/etc/machine-id`，克隆机会重复。在克隆机上执行 `rm /etc/machine-id && systemd-machine-id-setup`，再重新运行安装命令。

**卸载客户端**：

```bash
crontab -l | grep -v beacon-agent | crontab -
rm -f /usr/local/bin/beacon-agent /etc/beacon.env
```

然后在 `/admin` 删除该节点。注意删除会同时屏蔽它，之后它再上报会被拒绝。

## 文件说明

| 文件 | 作用 |
|---|---|
| `server.py` | 服务端：接收上报、存储、告警、接口 |
| `agent.sh` | 客户端：采集并上报 |
| `index.html` | 仪表盘与详情页 |
| `admin.html` | 管理后台 |
| `install-server.sh` | 面板一键部署 / 升级脚本 |
