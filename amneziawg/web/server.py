#!/usr/bin/env python3
"""
Minimal peer-management UI for the AmneziaWG backup VPN.

Dependency-free on purpose (same rule as stats/server.py): stdlib + the `awg` CLI
borrowed from the pinned server image, nothing else.

Source of truth is config/awg0.conf. Every mutation takes an flock on that file so
this and awg-client.sh can never interleave, and is applied to the live interface
over the UAPI socket with `awg set`, so adding or revoking a peer never disconnects
anyone else.

Auth is deliberately NOT implemented here: this listens only on the internal docker
network and is fronted by Caddy on a secret path with basic_auth.
"""
import base64, fcntl, json, os, re, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

IFACE   = os.environ.get("AWG_IFACE", "awg0")
CONFDIR = os.environ.get("AWG_CONFDIR", "/etc/amnezia/amneziawg")

# awg31.py lives in the repo's config/ dir, which is bind-mounted at CONFDIR. Importing
# it from there rather than copying into the image keeps one source of truth for the
# 3.1 obfuscation block — edit config/awg31.py and `docker restart amneziawg_web`.
sys.path.insert(0, CONFDIR)
import awg31  # noqa: E402
CONF    = os.path.join(CONFDIR, f"{IFACE}.conf")
CLIENTS = os.environ.get("AWG_CLIENTS", "/clients")
PORT    = int(os.environ.get("AWG_WEB_PORT", "8085"))
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# The container runs as root, so files it creates would be root-owned and unreadable to
# awg-client.sh running as the host user. Hand them back to that uid.
OWNER = (int(os.environ.get("AWG_OWNER_UID", "1000")),
         int(os.environ.get("AWG_OWNER_GID", "1000")))

# ---------------------------------------------------------------- helpers

def sh(*args, inp=None):
    return subprocess.run(args, input=inp, capture_output=True, text=True,
                          check=True).stdout.strip()

def params():
    d = {}
    with open(os.path.join(CONFDIR, "params.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k] = v
    return d

def parse_conf():
    """-> [{'name','pubkey','psk','ip'}] in file order."""
    peers, cur = [], None
    for line in open(CONF):
        m = re.match(r"^#\s*client:\s*(\S+)", line)
        if m:
            cur = {"name": m.group(1), "pubkey": "", "psk": "", "ip": ""}
            peers.append(cur)
            continue
        if cur is None:
            continue
        for key, field in (("PublicKey", "pubkey"), ("PresharedKey", "psk"),
                           ("AllowedIPs", "ip")):
            m = re.match(rf"^{key}\s*=\s*(\S+)", line)
            if m:
                cur[field] = m.group(1)
    return peers

def live():
    """Parse `awg show <iface> dump` -> (iface_info, {pubkey: stats})."""
    try:
        out = sh("awg", "show", IFACE, "dump")
    except Exception:
        return {"up": False}, {}
    lines = out.splitlines()
    if not lines:
        return {"up": False}, {}
    hdr = lines[0].split("\t")
    info = {"up": True, "pubkey": hdr[1] if len(hdr) > 1 else "",
            "port": hdr[2] if len(hdr) > 2 else ""}
    stats = {}
    for ln in lines[1:]:
        f = ln.split("\t")
        if len(f) < 8:
            continue
        stats[f[0]] = {"endpoint": f[2], "handshake": int(f[4] or 0),
                       "rx": int(f[5] or 0), "tx": int(f[6] or 0)}
    return info, stats

def next_ip(peers, p):
    base = p["AWG_SERVER_IP"].rsplit(".", 1)[0]
    used = {x["ip"].split("/")[0] for x in peers}
    for n in range(2, 255):
        cand = f"{base}.{n}"
        if cand not in used:
            return cand
    raise RuntimeError(f"no free address left in {p['AWG_SUBNET']}")

def client_conf_text(name, priv, psk, ip, p):
    # Only the server-side params come from params.env — these are the ones the protocol
    # forces to be identical on both ends. The 3.1 client-side block is generated fresh
    # per client by config/awg31.py (mounted at /etc/amnezia/amneziawg/awg31.py), the
    # same module awg-client.sh calls, so the CLI and this UI emit identical shapes.
    keys = ["S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4"]
    obf = "\n".join(f"{k:<4} = {p[k]}" for k in keys if k in p)
    o31, keepalive = awg31.block()
    return f"""# AmneziaWG client "{name}" — import into the AmneziaWG app (NOT stock WireGuard).
# Requires app v3.0.1 (2026-07-24) or newer: older builds reject the I1/I2 lines.
# S1-S4 / H1-H4 must stay exactly as generated; changing one byte breaks the handshake.
# Everything below them is client-side only — it may differ per client, and the server
# does not need it. See amneziawg.md "Obfuscation".
[Interface]
PrivateKey = {priv}
Address    = {ip}/32
DNS        = 1.1.1.1, 8.8.8.8
MTU        = {p['AWG_MTU']}
{obf}
{o31}

[Peer]
PublicKey           = {p['SERVER_PUBKEY']}
PresharedKey        = {psk}
Endpoint            = {p['AWG_ENDPOINT']}:{p['AWG_PORT']}
AllowedIPs          = 0.0.0.0/0, ::/0
PersistentKeepalive = {keepalive}
"""

class Locked:
    """flock the config for the duration of a mutation."""
    def __enter__(self):
        self.f = open(CONF, "r+")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self.f
    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()

# ---------------------------------------------------------------- actions

def add_peer(name):
    if not NAME_RE.match(name):
        raise ValueError("name must be 1-32 chars of A-Z a-z 0-9 _ -")
    p = params()
    with Locked():
        peers = parse_conf()
        if any(x["name"] == name for x in peers):
            raise ValueError(f"client '{name}' already exists")
        priv = sh("awg", "genkey")
        pub  = sh("awg", "pubkey", inp=priv)
        psk  = sh("awg", "genpsk")
        ip   = next_ip(peers, p)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(CONF, "a") as f:
            f.write(f"\n# client: {name}  (added {stamp})\n[Peer]\n"
                    f"PublicKey    = {pub}\nPresharedKey = {psk}\n"
                    f"AllowedIPs   = {ip}/32\n")
        os.makedirs(CLIENTS, exist_ok=True)
        path = os.path.join(CLIENTS, f"{name}.conf")
        old = os.umask(0o077)
        try:
            with open(path, "w") as f:
                f.write(client_conf_text(name, priv, psk, ip, p))
            os.chown(path, *OWNER)
        finally:
            os.umask(old)
        pskf = f"/tmp/psk.{os.getpid()}"
        old = os.umask(0o077)
        try:
            with open(pskf, "w") as f:
                f.write(psk)
        finally:
            os.umask(old)
        try:
            sh("awg", "set", IFACE, "peer", pub,
               "preshared-key", pskf, "allowed-ips", f"{ip}/32")
        finally:
            os.unlink(pskf)
    return {"name": name, "ip": ip, "pubkey": pub}

def remove_peer(name):
    with Locked():
        peers = parse_conf()
        match = next((x for x in peers if x["name"] == name), None)
        if not match:
            raise ValueError(f"no such client: {name}")
        text = open(CONF).read()
        text = re.sub(r"\n# client: %s .*?(?=\n# client: |\Z)" % re.escape(name),
                      "", text, flags=re.S)
        with open(CONF, "w") as f:
            f.write(text)
        try:
            os.unlink(os.path.join(CLIENTS, f"{name}.conf"))
        except FileNotFoundError:
            pass
        sh("awg", "set", IFACE, "peer", match["pubkey"], "remove")
    return {"removed": name}

def state():
    info, stats = live()
    peers = []
    for c in parse_conf():
        s = stats.get(c["pubkey"], {})
        hs = s.get("handshake", 0)
        peers.append({
            "name": c["name"], "ip": c["ip"], "pubkey": c["pubkey"],
            "endpoint": s.get("endpoint", ""),
            "handshake": hs, "age": int(time.time()) - hs if hs else None,
            "rx": s.get("rx", 0), "tx": s.get("tx", 0),
            "has_conf": os.path.exists(os.path.join(CLIENTS, c["name"] + ".conf")),
        })
    p = params()
    info.update({"endpoint": f"{p['AWG_ENDPOINT']}:{p['AWG_PORT']}",
                 "subnet": p["AWG_SUBNET"], "iface": IFACE, "now": int(time.time())})
    return {"iface": info, "peers": peers}

# ---------------------------------------------------------------- page

PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>AmneziaWG peers</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171a;--dim:#6b7280;--line:#e3e6ea;
      --ok:#12874a;--okbg:#e8f5ee;--off:#9aa1ab;--danger:#b42318;--accent:#2f5fd0}
@media(prefers-color-scheme:dark){:root{--bg:#101317;--card:#171b21;--fg:#e7eaee;
      --dim:#98a1ad;--line:#262c34;--ok:#3ddc84;--okbg:#12291c;--off:#5a626d;
      --danger:#ff6b5e;--accent:#7aa2f7}}
*{box-sizing:border-box}
body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);
     font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto}
h1{font-size:20px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
.sub code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
      padding:16px;margin-bottom:16px}
.row{display:flex;gap:8px;flex-wrap:wrap}
input[type=text]{flex:1;min-width:200px;padding:9px 12px;border:1px solid var(--line);
      border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}
button{padding:9px 14px;border:1px solid var(--line);border-radius:8px;cursor:pointer;
      background:var(--card);color:var(--fg);font:inherit}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.link{border:0;background:none;color:var(--accent);padding:4px 6px;font-size:13px}
button.link.danger{color:var(--danger)}
button:disabled{opacity:.5;cursor:default}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
   color:var(--dim);font-weight:600;padding:0 8px 8px;border-bottom:1px solid var(--line)}
td{padding:11px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
tr:last-child td{border-bottom:0}
.name{font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--dim)}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:3px 9px;
      border-radius:999px;background:var(--bg);color:var(--dim);white-space:nowrap}
.pill.on{background:var(--okbg);color:var(--ok)}
.dot{width:6px;height:6px;border-radius:50%;background:var(--off)}
.pill.on .dot{background:var(--ok)}
.right{text-align:right;white-space:nowrap}
.empty{color:var(--dim);text-align:center;padding:28px 0;font-size:14px}
.msg{padding:10px 12px;border-radius:8px;margin-bottom:12px;font-size:13px;display:none}
.msg.err{display:block;background:#fdecea;color:#8a1c12}
.msg.ok{display:block;background:var(--okbg);color:var(--ok)}
@media(prefers-color-scheme:dark){.msg.err{background:#2a1512;color:var(--danger)}}
dialog{border:1px solid var(--line);border-radius:14px;background:var(--card);
       color:var(--fg);padding:20px;max-width:min(92vw,520px);width:100%}
dialog::backdrop{background:#0008}
dialog h2{margin:0 0 4px;font-size:16px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;
    overflow-x:auto;font-size:11.5px;line-height:1.45;max-height:220px}
.qr{display:block;margin:14px auto;width:210px;height:210px;image-rendering:pixelated;
    background:#fff;padding:8px;border-radius:8px}
.hint{color:var(--dim);font-size:12px}
</style>
<div class=wrap>
<h1>AmneziaWG peers</h1>
<div class=sub id=hdr>loading…</div>
<div class=msg id=msg></div>

<div class=card>
  <form class=row id=addform>
    <input type=text id=name placeholder="new client name (a-z 0-9 _ -)" autocomplete=off>
    <button class=primary id=addbtn>Add client</button>
  </form>
  <div class=hint style="margin-top:8px">Applied to the live tunnel immediately — existing
    peers are not disconnected.</div>
</div>

<div class=card>
  <table><thead><tr>
    <th>Client</th><th>Address</th><th>Last handshake</th><th>Transfer</th><th></th>
  </tr></thead><tbody id=rows></tbody></table>
  <div class=empty id=empty style=display:none>No peers yet — add one above.</div>
</div>
</div>

<dialog id=dlg>
  <h2 id=dlgtitle></h2>
  <div class=hint>Scan with the <b>AmneziaWG</b> app (stock WireGuard cannot connect).</div>
  <img class=qr id=qr alt="config QR code">
  <pre id=conf></pre>
  <div class=row style="margin-top:12px;justify-content:flex-end">
    <button id=dl>Download .conf</button>
    <button class=primary id=close>Done</button>
  </div>
</dialog>

<script>
const $=s=>document.querySelector(s);
let current=null;

const ago=s=>{if(s===null)return"never";if(s<60)return s+"s ago";
  if(s<3600)return Math.floor(s/60)+"m ago";if(s<86400)return Math.floor(s/3600)+"h ago";
  return Math.floor(s/86400)+"d ago"};
const bytes=n=>{if(!n)return"0";const u=["B","KiB","MiB","GiB","TiB"];let i=0;
  while(n>=1024&&i<u.length-1){n/=1024;i++}return n.toFixed(i?1:0)+" "+u[i]};

function flash(text,cls){const m=$("#msg");m.textContent=text;m.className="msg "+cls;
  if(cls==="ok")setTimeout(()=>m.className="msg",4000)}

async function api(path,opts){const r=await fetch(path,opts);
  const t=await r.text();let j={};try{j=JSON.parse(t)}catch(e){}
  if(!r.ok)throw new Error(j.error||t||r.status);return j}

async function refresh(){
  try{
    const s=await api("api/state");
    $("#hdr").innerHTML=(s.iface.up?"tunnel up":"<b>tunnel DOWN</b>")+
      " · <code>"+s.iface.endpoint+"</code> · "+s.peers.length+" peer(s) · "+s.iface.subnet;
    const tb=$("#rows");tb.innerHTML="";
    $("#empty").style.display=s.peers.length?"none":"block";
    for(const p of s.peers){
      const on=p.age!==null&&p.age<180;
      const tr=document.createElement("tr");
      tr.innerHTML=`<td><div class=name></div><div class="mono pk"></div></td>
        <td class=mono>${p.ip}</td>
        <td><span class="pill ${on?"on":""}"><span class=dot></span>${ago(p.age)}</span></td>
        <td class=mono>↓${bytes(p.rx)} ↑${bytes(p.tx)}</td>
        <td class=right>
          <button class="link show">Config</button>
          <button class="link danger del">Revoke</button></td>`;
      tr.querySelector(".name").textContent=p.name;
      tr.querySelector(".pk").textContent=p.pubkey.slice(0,16)+"…";
      tr.querySelector(".show").onclick=()=>showConf(p.name);
      tr.querySelector(".del").onclick=()=>del(p.name);
      tr.querySelector(".show").disabled=!p.has_conf;
      tb.appendChild(tr);
    }
  }catch(e){flash("refresh failed: "+e.message,"err")}
}

async function showConf(name){
  current=name;
  $("#dlgtitle").textContent=name;
  $("#conf").textContent=await (await fetch("api/peers/"+encodeURIComponent(name)+"/conf")).text();
  $("#qr").src="api/peers/"+encodeURIComponent(name)+"/qr";
  $("#dlg").showModal();
}

$("#addform").onsubmit=async e=>{
  e.preventDefault();
  const name=$("#name").value.trim();if(!name)return;
  $("#addbtn").disabled=true;
  try{
    await api("api/peers",{method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({name})});
    $("#name").value="";flash("added "+name,"ok");await refresh();await showConf(name);
  }catch(e){flash(e.message,"err")}finally{$("#addbtn").disabled=false}
};

async function del(name){
  if(!confirm("Revoke '"+name+"'? Its config stops working immediately."))return;
  try{await api("api/peers/"+encodeURIComponent(name),{method:"DELETE"});
    flash("revoked "+name,"ok");refresh()}catch(e){flash(e.message,"err")}
}

$("#close").onclick=()=>$("#dlg").close();
$("#dl").onclick=()=>{const a=document.createElement("a");
  a.href="api/peers/"+encodeURIComponent(current)+"/conf?dl=1";
  a.download=current+".conf";a.click()};

refresh();setInterval(refresh,5000);
</script>
"""

# ---------------------------------------------------------------- http

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        print("%s %s" % (self.address_string(), fmt % a), flush=True)

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _peer_name(self, path, suffix):
        m = re.match(rf"^/api/peers/([^/]+){suffix}$", path)
        return unquote(m.group(1)) if m else None

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/", "/index.html"):
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if p == "/api/state":
                return self._send(200, state())
            name = self._peer_name(p, "/conf")
            if name:
                f = os.path.join(CLIENTS, f"{name}.conf")
                if not NAME_RE.match(name) or not os.path.exists(f):
                    return self._send(404, {"error": "no config on file"})
                extra = ({"Content-Disposition": f'attachment; filename="{name}.conf"'}
                         if "dl=1" in self.path else None)
                return self._send(200, open(f).read(), "text/plain; charset=utf-8", extra)
            name = self._peer_name(p, "/qr")
            if name:
                f = os.path.join(CLIENTS, f"{name}.conf")
                if not NAME_RE.match(name) or not os.path.exists(f):
                    return self._send(404, {"error": "no config on file"})
                # Minified payload: the 3.1 block made the full file too dense to scan
                # reliably (125 modules vs 97). See awg31.qr_payload.
                png = subprocess.run(["qrencode", "-t", "PNG", "-s", "6", "-o", "-"],
                                     input=awg31.qr_payload(open(f).read()).encode(),
                                     capture_output=True, check=True).stdout
                return self._send(200, png, "image/png")
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        try:
            if self.path.split("?")[0] != "/api/peers":
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4096:
                return self._send(413, {"error": "too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            return self._send(200, add_peer((body.get("name") or "").strip()))
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_DELETE(self):
        try:
            name = self._peer_name(self.path.split("?")[0], "")
            if not name:
                return self._send(404, {"error": "not found"})
            return self._send(200, remove_peer(name))
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    print(f"amneziawg-web on :{PORT} managing {CONF}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
