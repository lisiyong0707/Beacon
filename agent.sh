#!/bin/bash
# Beacon agent. Run every minute from cron. Needs: bash, curl, awk, df.
: "${BEACON_URL:?}" "${BEACON_KEY:?}"
T_CT=${T_CT:-202.96.209.133:53}; T_CU=${T_CU:-210.22.97.1:53}; T_CM=${T_CM:-211.136.17.107:53}

tping() { # host:port -> "avg_ms loss_pct" (3 probes)
  local h=${1%:*} p=${1#*:} ok=0 sum=0 s e i
  [[ $h =~ ^[A-Za-z0-9.-]+$ && $p =~ ^[0-9]+$ ]] || { echo "-1 100"; return; }
  for i in 1 2 3; do
    s=$(date +%s%N)
    if timeout 2 bash -c 'exec 3<>/dev/tcp/$0/$1' "$h" "$p" 2>/dev/null; then
      e=$(date +%s%N); sum=$((sum + (e - s) / 1000000)); ok=$((ok + 1))
    fi
  done
  [ $ok -gt 0 ] && echo "$((sum / ok)) $(((3 - ok) * 100 / 3))" || echo "-1 100"
}
netsum() { awk -F'[: ]+' 'NR>2 && $2!="lo" {rx+=$3; tx+=$11} END{print rx+0, tx+0}' /proc/net/dev; }
esc() { tr -d '"\\\n\r' <<<"$1"; }

read -r _ u n s i w q sq st _ </proc/stat; t1=$((u+n+s+i+w+q+sq+st)); id1=$((i+w))
read -r rx1 tx1 < <(netsum); sleep 1
read -r _ u n s i w q sq st _ </proc/stat; t2=$((u+n+s+i+w+q+sq+st)); id2=$((i+w))
read -r rx2 tx2 < <(netsum)
dt=$((t2-t1)); cpu=$(awk -v d=$dt -v i=$((id2-id1)) 'BEGIN{printf "%.1f", (d>0) ? (d-i)*100/d : 0}')

mt=$(awk '/^MemTotal/{print $2*1024}' /proc/meminfo); ma=$(awk '/^MemAvailable/{print $2*1024}' /proc/meminfo)
mem=$(awk -v t=$mt -v a=$ma 'BEGIN{printf "%.1f", (t>0) ? (t-a)*100/t : 0}')
read -r dtot dpct < <(df -P -B1 / | awk 'NR==2{gsub("%","",$5); print $2, $5}')
read -r l1 l5 l15 pr _ </proc/loadavg; procs=${pr#*/}; up=$(cut -d. -f1 /proc/uptime)
osn=$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux}")
cpum=$(awk -F: '/model name/{gsub(/^ +/,"",$2); print $2; exit}' /proc/cpuinfo)
cores=$(nproc 2>/dev/null || echo 1); arch=$(uname -m)
virt=$(systemd-detect-virt 2>/dev/null); virt=${virt:-unknown}
swap=$(awk '/^SwapTotal/{t=$2}/^SwapFree/{f=$2}END{printf "%.1f",(t>0)?(t-f)*100/t:0}' /proc/meminfo)
nid=$({ cat /etc/machine-id 2>/dev/null || hostname; } | sha256sum | cut -c1-32)

cc=/tmp/.beacon_cc
if [ ! -s $cc ] || [ -n "$(find $cc -mmin +1440 2>/dev/null)" ]; then
  c=$(curl -fsS --max-time 3 https://ipinfo.io/country 2>/dev/null | tr -d '[:space:]')
  [[ $c =~ ^[A-Za-z]{2}$ ]] && echo "$c" >$cc
fi
country=$(cat $cc 2>/dev/null)

read -r a1 b1 < <(tping "$T_CT"); read -r a2 b2 < <(tping "$T_CU"); read -r a3 b3 < <(tping "$T_CM")

json=$(printf '{"id":"%s","name":"%s","os":"%s","kernel":"%s","cpu_model":"%s","cores":%s,"country":"%s","cpu":%s,"mem":%s,"disk":%s,"load1":%s,"load5":%s,"load15":%s,"procs":%s,"swap":%s,"arch":"%s","virt":"%s","uptime":%s,"mem_total":%s,"disk_total":%s,"rx":%s,"tx":%s,"rx_total":%s,"tx_total":%s,"ping":{"ct":[%s,%s],"cu":[%s,%s],"cm":[%s,%s]}}' \
  "$nid" "$(esc "$(hostname)")" "$(esc "$osn")" "$(esc "$(uname -r)")" "$(esc "$cpum")" "$cores" "$country" \
  "$cpu" "$mem" "$dpct" "$l1" "$l5" "$l15" "$procs" "$swap" "$(esc "$arch")" "$(esc "$virt")" "$up" "$mt" "$dtot" $((rx2-rx1)) $((tx2-tx1)) "$rx2" "$tx2" "$a1" "$b1" "$a2" "$b2" "$a3" "$b3")
curl -fsS --max-time 10 -H "X-API-Key: $BEACON_KEY" -H 'Content-Type: application/json' -d "$json" "$BEACON_URL/api/report" >/dev/null
