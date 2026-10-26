# Beacon 探针

零依赖 Python 3 服务端 + Bash 客户端 + 实时仪表盘。

- 仪表盘 `/`：CPU/内存/硬盘进度条、网络速率、三网 TCP Ping、国家筛选、深浅色、告警徽章
- 详情页 `/#/node/<id>`：点击卡片进入。硬件与系统信息、负载、Swap，1/6/12/24 小时图表（资源、网络、延迟、丢包），事件日志，可复制分享链接
- 告警：CPU/内存/硬盘/丢包/延迟阈值，**连续 3 分钟**越线才触发；节点离线（超过 90 秒无上报）立即触发；触发与恢复时推送 Webhook
- 管理后台 `/admin`：改节点名、删除并屏蔽/解除屏蔽、告警阈值与 Webhook、生成安装命令

## 启动服务端
    BEACON_API_KEYS='key1,key2' BEACON_ADMIN_PASSWORD='strong-pass' python3 server.py
可选：`PORT` `BEACON_PUBLIC_URL` `BEACON_DATA_DIR` `BEACON_OFFLINE_SECONDS`(90) `BEACON_HISTORY`(1440) `BEACON_TRUST_PROXY`(置于反代后设为 true)

## 安装客户端（被监控机器，root）
在 `/admin` 复制安装命令，或：

    curl -fsSL 'https://probe.example.com/install.sh?key=key1' | sh
三网 Ping 目标用 agent 环境变量改：`T_CT` `T_CU` `T_CM`（host:port）。
重新运行安装命令即可升级旧客户端；旧客户端不会报告的字段（负载、Swap 等）详情页显示为 `-`。

## 说明
- 管理接口用 `X-Admin-Password` 请求头认证，同一 IP 5 分钟内失败 5 次会被限流。
- Webhook 请求体为 `{"text": "...", "content": "..."}`；Telegram 等需要特殊格式的接口请用转发服务。
- 事件日志从已存历史里按需计算，不额外占存储。
- 公开接口不返回任何 IP。
