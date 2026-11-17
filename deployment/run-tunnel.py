"""Start the Cloudflare tunnel and keep the public URL in sync.

Flow:
  1. Start a Cloudflare quick tunnel to the local app.
  2. Point the Worker proxy at the tunnel (the tunnel URL changes on restart).
  3. Save the public URL to the database so QR codes stay correct.

Usage:
    python deployment/run-tunnel.py <CF_API_TOKEN> [duckdns-hostname]
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deployment"
DB = ROOT / "transport.db"
CLOUDFLARED = DEPLOY / "cloudflared.exe"
WRANGLER_TOML = DEPLOY / "wrangler.toml"
ACCOUNT = "7fc744c462e7c17cef5503657b7f6014"
WORKER_NAME = "transport-booking-proxy"
PUBLIC_HOST = "transport-booking-proxy.transport-booking.workers.dev"


def update_toml(tunnel_host):
    text = WRANGLER_TOML.read_text(encoding="utf-8")
    text = re.sub(r'TUNNEL_HOST = "[^"]*"',
                  f'TUNNEL_HOST = "{tunnel_host}"', text)
    WRANGLER_TOML.write_text(text, encoding="utf-8")


def _wrangler_path():
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "npm" / "wrangler.cmd",
        Path(os.environ.get("APPDATA", "")) / "npm" / "wrangler",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return "wrangler"


def deploy_worker(token):
    env = dict(os.environ)
    env["CLOUDFLARE_API_TOKEN"] = token
    env["CLOUDFLARE_ACCOUNT_ID"] = ACCOUNT
    result = subprocess.run(
        [_wrangler_path(), "deploy", "--config", str(WRANGLER_TOML)],
        cwd=str(DEPLOY), env=env, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError((result.stdout + result.stderr)[-600:])
    return result.stdout


def save_public_url(url):
    conn = sqlite3.connect(str(DB))
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('public_url', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=?", (url, url))
    conn.commit()
    conn.close()


def main():
    token = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CF_TOKEN", "")
    duckdns = sys.argv[2] if len(sys.argv) > 2 else "transport-booking.duckdns.org"
    if not token:
        raise SystemExit("Cloudflare API token required (arg 1 or CF_TOKEN).")

    print("Starting Cloudflare tunnel...")
    proc = subprocess.Popen(
        [str(CLOUDFLARED), "tunnel", "--url", "http://127.0.0.1:5019"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1)

    tunnel_url = None
    deadline = time.time() + 120
    while time.time() < deadline and proc.poll() is None:
        line = proc.stdout.readline()
        if not line:
            continue
        line = line.strip()
        if not line:
            continue
        m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if m:
            tunnel_url = m.group(0)
            break
        print("  " + line, flush=True)

    if not tunnel_url:
        raise SystemExit("Could not read the tunnel URL.")

    tunnel_host = tunnel_url.replace("https://", "")
    print("Tunnel URL:", tunnel_url, flush=True)

    # Keep draining cloudflared output so its pipe never blocks.
    def drain():
        for line in proc.stdout:
            line = line.strip()
            if line and "Registered tunnel" in line:
                print("  " + line, flush=True)
    threading.Thread(target=drain, daemon=True).start()

    # The tunnel needs time to register before it serves traffic.
    print("Waiting for the tunnel to come online...", flush=True)
    online = False
    for _ in range(30):
        time.sleep(5)
        try:
            req = urllib.request.Request(tunnel_url + "/login")
            req.add_header("User-Agent",
                           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120.0 Safari/537.36")
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status == 200:
                    online = True
                    break
        except Exception:
            continue
    print("Tunnel online" if online else "Tunnel not confirmed yet",
          flush=True)

    try:
        update_toml(tunnel_host)
        deploy_worker(token)
        print("Worker updated ->", tunnel_host, flush=True)
    except Exception as exc:
        print("Worker update failed:", exc, flush=True)

    public_url = "https://" + duckdns
    save_public_url(public_url)
    print("Public URL saved:", public_url, flush=True)
    print("Direct tunnel URL:", tunnel_url, flush=True)
    print()
    print("Keep this window open. Press Ctrl+C to stop.", flush=True)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
