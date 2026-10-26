#!/usr/bin/env python3
"""Beacon probe server (stdlib only): dashboard, node detail, alerts, admin API."""
import hmac, json, math, os, re, sys, threading, time, urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8080"))
KEYS = [k for k in os.environ.get("BEACON_API_KEYS", "").split(",") if k]
ADMIN = os.environ.get("BEACON_ADMIN_PASSWORD", "")
DATA = os.path.join(os.environ.get("BEACON_DATA_DIR", HERE), "beacon.json")
PUBLIC = os.environ.get("BEACON_PUBLIC_URL", "").rstrip("/")
TRUST = os.environ.get("BEACON_TRUST_PROXY", "").lower() in ("1", "true", "yes", "on")
OFFLINE = int(os.environ.get("BEACON_OFFLINE_SECONDS", "90"))
HIST = int(os.environ.get("BEACON_HISTORY", "1440"))  # 1 sample/min -> 24h
MAX_NODES, MAX_BODY = 200, 16384
if not KEYS or not ADMIN:
    sys.exit("Set BEACON_API_KEYS (comma separated) and BEACON_ADMIN_PASSWORD")

NUM = {"cpu": 100, "mem": 100, "disk": 100, "load1": 1e5, "load5": 1e5, "load15": 1e5, "procs": 1e7, "swap": 100,
       "rx": 1e12, "tx": 1e12, "rx_total": 1e18, "tx_total": 1e18, "uptime": 1e10, "mem_total": 1e15,
       "disk_total": 1e15, "cores": 4096}
STR = {"name": 64, "os": 64, "kernel": 64, "cpu_model": 96, "arch": 16, "virt": 24}
STATIC = [*STR, "country", "disk_total", "mem_total", "cores", "rx_total", "tx_total", "uptime", "load5", "load15", "procs", "swap"]
DEFAULT_TH = {"cpu": 90, "mem": 90, "disk": 90, "loss": 30, "latency": 300}
LIMITS = {"cpu": (1, 100), "mem": (1, 100), "disk": (1, 100), "loss": (1, 100), "latency": (10, 5000)}
# history row: [t, cpu, mem, rx, tx, ct_ms, cu_ms, cm_ms, disk, ct_loss, cu_loss, cm_loss]
lock, nodes, blocked, fails = threading.Lock(), {}, {}, {}
settings = {"thresholds": dict(DEFAULT_TH), "webhook": ""}


def load():
    try:
        with open(DATA) as f: raw = json.load(f)
    except (OSError, ValueError):
        return
    if "nodes" in raw:
        blocked.update(raw.get("blocked", {}))
        settings["thresholds"].update(raw.get("settings", {}).get("thresholds", {}))
        settings["webhook"] = raw.get("settings", {}).get("webhook", "")
        raw = raw["nodes"]
    for i, n in raw.items():
        n["hist"] = deque(n.get("hist", []), maxlen=HIST)
        nodes[i] = n


def save():
    with lock:
        snap = {"nodes": {i: {**n, "hist": list(n["hist"])} for i, n in nodes.items()}, "blocked": blocked, "settings": settings}
        text = json.dumps(snap)
    tmp = DATA + ".tmp"
    with open(tmp, "w") as f: f.write(text)
    os.replace(tmp, DATA)


def clean(d):
    n = {}
    for k, hi in NUM.items():
        v = float(d.get(k) or 0)
        n[k] = max(0.0, min(hi, v)) if math.isfinite(v) else 0.0
    for k, ln in STR.items():
        n[k] = re.sub(r"[^\w .,:/+()\-]", "", str(d.get(k, "")))[:ln]
    cc = str(d.get("country", "")).upper()
    n["country"] = cc if re.fullmatch(r"[A-Z]{2}", cc) else ""
    ping = d.get("ping") if isinstance(d.get("ping"), dict) else {}
    for c in ("ct", "cu", "cm"):
        p = ping.get(c) if isinstance(ping.get(c), list) and len(ping.get(c)) == 2 else [-1, 100]
        ms, loss = float(p[0]), float(p[1])
        n[c] = [round(ms, 1) if math.isfinite(ms) and ms >= 0 else -1, max(0, min(100, loss)) if math.isfinite(loss) else 100]
    return n


def ingest(nid, d):
    n, now = clean(d), int(time.time())
    with lock:
        if nid in blocked: return "blocked"
        if nid not in nodes and len(nodes) >= MAX_NODES: return "full"
        node = nodes.setdefault(nid, {"hist": deque(maxlen=HIST), "custom_name": ""})
        node.update({k: n[k] for k in STATIC})
        node["last"] = {k: n[k] for k in ("cpu", "mem", "disk", "load1", "rx", "tx", "ct", "cu", "cm")}
        node["seen"] = now
        node["hist"].append([now, n["cpu"], n["mem"], n["rx"], n["tx"], n["ct"][0], n["cu"][0], n["cm"][0], n["disk"],
                             n["ct"][1], n["cu"][1], n["cm"][1]])
    return "ok"


def active(n, now):
    """Alerts currently firing: a metric must be bad for the last 3 samples in a row."""
    if now - n["seen"] >= OFFLINE: return ["offline"]
    th, h, a = settings["thresholds"], list(n["hist"])[-3:], []
    if len(h) < 3: return a
    for name, i in (("cpu", 1), ("mem", 2), ("disk", 8)):
        if all(len(r) > i and r[i] > th[name] for r in h): a.append(name)
    for c, i in (("ct", 5), ("cu", 6), ("cm", 7)):
        if all(r[i] < 0 for r in h): a.append(c + "_down")
        elif all(r[i] > th["latency"] for r in h): a.append(c + "_slow")
        if all(len(r) > i + 4 and r[i + 4] > th["loss"] for r in h) and not all(r[i] < 0 for r in h): a.append(c + "_loss")
    return a


def events(n):
    """History of past breaches, derived on read (costs no extra storage)."""
    th, h, ev = settings["thresholds"], list(n["hist"]), []

    def scan(kind, metric, idx, bad):
        run = []
        for r in h + [None]:
            if r is not None and len(r) > idx and bad(r[idx]):
                run.append(r); continue
            if len(run) >= 3:
                ev.append({"t": run[0][0], "kind": kind, "metric": metric, "mins": len(run), "peak": max(x[idx] for x in run)})
            run = []
    for m, i in (("cpu", 1), ("mem", 2), ("disk", 8)): scan("high", m, i, lambda v, m=m: v > th[m])
    for m, i in (("ct", 5), ("cu", 6), ("cm", 7)):
        scan("ping_down", m, i, lambda v: v < 0)
        scan("ping_slow", m, i, lambda v: v > th["latency"])
        scan("ping_loss", m, i + 4, lambda v: v > th["loss"])
    for a, b in zip(h, h[1:]):
        if b[0] - a[0] > OFFLINE + 30: ev.append({"t": a[0], "kind": "offline", "metric": "", "mins": round((b[0] - a[0]) / 60), "peak": 0})
    down = {(e["metric"], e["t"]) for e in ev if e["kind"] == "ping_down"}  # a timeout is 100% loss: log it once
    ev = [e for e in ev if not (e["kind"] == "ping_loss" and (e["metric"], e["t"]) in down)]
    return sorted(ev, key=lambda e: -e["t"])[:50]


def sample(n, points, rng, now):
    h = [r for r in n["hist"] if r[0] >= now - rng]
    return h[::max(1, math.ceil(len(h) / points))]


def view(nid, n, h, now):
    out = {k: n.get(k) for k in [*STATIC, "last", "seen"]}
    out.update(id=nid, name=n.get("custom_name") or n["name"], online=now - n["seen"] < OFFLINE, alerts=active(n, now), hist=h)
    return out  # never includes an IP


CAR, SUF = {"ct": "电信", "cu": "联通", "cm": "移动"}, {"down": "Ping 超时", "slow": "延迟过高", "loss": "丢包过高"}
BASE = {"offline": "离线", "cpu": "CPU 过高", "mem": "内存过高", "disk": "硬盘过高"}


def label(a):
    if a in BASE: return BASE[a]
    c, k = a.split("_"); return CAR[c] + SUF[k]


def post(text):
    try:
        req = urllib.request.Request(settings["webhook"], json.dumps({"text": text, "content": text}).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        print("webhook failed:", e, file=sys.stderr)


def background():
    prev, last_save = None, time.time()
    while True:
        time.sleep(30)
        now, cur = time.time(), {}
        with lock:
            for i, n in nodes.items(): cur[i] = (n.get("custom_name") or n["name"], set(active(n, now)))
        if prev is not None and settings["webhook"]:  # notify on state change only
            for i, (name, s) in cur.items():
                old = prev.get(i, (name, set()))[1]
                for a in sorted(s - old): post(f"🔴 {name}: {label(a)}")
                for a in sorted(old - s): post(f"🟢 {name}: {label(a)} 已恢复")
        prev = cur
        try: save()
        except OSError as e: print("save failed:", e, file=sys.stderr)


def authed(h, a): return hmac.compare_digest(a.encode(), h.encode())


PAGES = {"/": "index.html", "/index.html": "index.html", "/admin": "admin.html", "/admin.html": "admin.html", "/agent.sh": "agent.sh"}
NID = r"/api/nodes/([a-f0-9]{8,32})"


class H(BaseHTTPRequestHandler):
    server_version = "Beacon"
    def log_message(self, *a): pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        for k, v in {"Content-Type": ctype + "; charset=utf-8", "Content-Length": str(len(b)), "Cache-Control": "no-store",
                     "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                     "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:"}.items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY: raise ValueError("too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def guard(self):
        """Admin auth with login rate limit (5 failures / 5 min / IP). Sends the error itself."""
        ip = (self.headers.get("X-Forwarded-For", "").split(",")[0].strip() if TRUST else "") or self.client_address[0]
        now = time.time(); q = fails.setdefault(ip, deque())
        if len(fails) > 1000: fails.clear()
        while q and now - q[0] > 300: q.popleft()
        if len(q) >= 5: self.send(429, {"error": "too many attempts"}); return False
        if authed(ADMIN, self.headers.get("X-Admin-Password", "")): return True
        q.append(now); self.send(403, {"error": "forbidden"}); return False

    def do_GET(self):
        u = urlparse(self.path); p, now = u.path, time.time()
        if p in PAGES:
            ct = "text/x-shellscript" if p == "/agent.sh" else "text/html"
            return self.send(200, open(os.path.join(HERE, PAGES[p]), "rb").read(), ct)
        if p == "/api/health": return self.send(200, {"ok": True})
        if p == "/api/nodes":
            with lock: return self.send(200, [view(i, n, sample(n, 60, 3600, now), now) for i, n in nodes.items()])
        m = re.fullmatch(NID, p)
        if m:
            try: rng = max(300, min(86400, int(parse_qs(u.query).get("range", ["86400"])[0])))
            except ValueError: rng = 86400
            with lock:
                n = nodes.get(m.group(1))
                if not n: return self.send(404, {"error": "not found"})
                out = view(m.group(1), n, sample(n, 300, rng, now), now)
                out.update(events=events(n), thresholds=settings["thresholds"])
                return self.send(200, out)
        if p == "/api/admin/info":
            if not self.guard(): return
            with lock:
                return self.send(200, {"keys": KEYS, "settings": settings,
                    "nodes": [{"id": i, "name": n["name"], "custom_name": n.get("custom_name", ""), "country": n.get("country", ""),
                               "online": now - n["seen"] < OFFLINE, "seen": n["seen"]} for i, n in nodes.items()],
                    "blocked": [{"id": i, "name": v} for i, v in blocked.items()]})
        if p == "/install.sh":
            key = parse_qs(u.query).get("key", [""])[0]
            if not any(authed(k, key) for k in KEYS): return self.send(403, {"error": "bad key"})
            host = self.headers.get("Host", "")
            if not PUBLIC and not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", host): return self.send(400, {"error": "bad host"})
            url = PUBLIC or "http://" + host
            return self.send(200, f"""#!/bin/sh
set -e
curl -fsSL '{url}/agent.sh' -o /usr/local/bin/beacon-agent && chmod 755 /usr/local/bin/beacon-agent
umask 077; printf 'export BEACON_URL=%s\\nexport BEACON_KEY=%s\\n' '{url}' '{key}' > /etc/beacon.env
(crontab -l 2>/dev/null | grep -v beacon-agent; echo '* * * * * . /etc/beacon.env; /usr/local/bin/beacon-agent >/dev/null 2>&1') | crontab -
echo "Beacon agent installed; first report within a minute."
""", "text/x-shellscript")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        try: d = self.body()
        except ValueError: return self.send(400, {"error": "bad body"})
        if not isinstance(d, dict): return self.send(400, {"error": "bad body"})
        if p == "/api/report":
            if not any(authed(k, self.headers.get("X-API-Key", "")) for k in KEYS): return self.send(401, {"error": "bad key"})
            nid = str(d.get("id", ""))
            if not re.fullmatch(r"[a-f0-9]{8,32}", nid): return self.send(400, {"error": "bad id"})
            try: r = ingest(nid, d)
            except (TypeError, ValueError): return self.send(400, {"error": "bad data"})
            return self.send({"ok": 200, "blocked": 403, "full": 429}[r], {"result": r})
        if p == "/api/admin/settings":
            if not self.guard(): return
            th, new = d.get("thresholds") or {}, {}
            for k, (lo, hi) in LIMITS.items():
                try: v = float(th.get(k, settings["thresholds"][k]))
                except (TypeError, ValueError): return self.send(400, {"error": "bad threshold"})
                new[k] = int(max(lo, min(hi, v))) if math.isfinite(v) else DEFAULT_TH[k]
            wh = str(d.get("webhook", "")).strip()
            if wh and not re.fullmatch(r"https?://\S{1,500}", wh): return self.send(400, {"error": "bad webhook"})
            with lock: settings.update(thresholds=new, webhook=wh)
            save(); return self.send(200, {"ok": True})
        if p == "/api/admin/unblock":
            if not self.guard(): return
            with lock: blocked.pop(str(d.get("id", "")), None)
            save(); return self.send(200, {"ok": True})
        m = re.fullmatch(NID, p)
        if m:
            if not self.guard(): return
            with lock:
                if m.group(1) not in nodes: return self.send(404, {"error": "not found"})
                nodes[m.group(1)]["custom_name"] = re.sub(r"[^\w .\-]", "", str(d.get("name", "")))[:64]
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})

    def do_DELETE(self):
        m = re.fullmatch(NID, urlparse(self.path).path)
        if not m: return self.send(404, {"error": "not found"})
        if not self.guard(): return
        with lock:
            n = nodes.pop(m.group(1), None)
            if n: blocked[m.group(1)] = n.get("custom_name") or n["name"]
        save(); self.send(200, {"ok": True})


if __name__ == "__main__":
    load(); threading.Thread(target=background, daemon=True).start()
    print(f"Beacon listening on :{PORT}")
    try: ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
    finally: save()
