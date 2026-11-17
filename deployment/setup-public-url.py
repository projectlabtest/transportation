"""Set up a permanent public URL for the Transport Booking app.

Creates a Cloudflare named tunnel + Worker proxy so the DuckDNS domain
works even behind CGNAT (no port forwarding needed).

Usage:
    python setup-public-url.py <CF_API_TOKEN> <duckdns_hostname>

Requires: CLOUDFLARE_ACCOUNT_ID env var, or it is read from wrangler.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deployment"
WORKER = DEPLOY / "worker-proxy.js"
DB = ROOT / "transport.db"
TUNNEL_NAME = "transport-booking"
WORKER_NAME = "transport-booking-proxy"
LOCAL_ORIGIN = "http://127.0.0.1:5019"
API = "https://api.cloudflare.com/client/v4"


def api(token, method, path, body=None):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise SystemExit(f"Cloudflare API error {e.code}:\n{detail}")


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    token = sys.argv[1]
    duckdns = sys.argv[2]

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not account_id:
        raise SystemExit("Set CLOUDFLARE_ACCOUNT_ID environment variable.")
    if not duckdns.endswith(".duckdns.org"):
        raise SystemExit("Hostname must end with .duckdns.org")

    print("1/5 Checking Cloudflare account...")
    zones = api(token, "GET", f"/zones?account.id={account_id}")
    print(f"    Account {account_id} OK")

    print("2/5 Creating named tunnel...")
    existing = api(token, "GET",
                   f"/accounts/{account_id}/cfd_tunnel?name={TUNNEL_NAME}&is_deleted=false")
    if existing["result"]:
        tunnel = existing["result"][0]
        print(f"    Tunnel already exists: {tunnel['id']}")
    else:
        created = api(token, "POST", f"/accounts/{account_id}/cfd_tunnel",
                      {"name": TUNNEL_NAME, "tunnel_type": "cfd_tunnel"})
        tunnel = created["result"]
        print(f"    Created tunnel: {tunnel['id']}")

    tunnel_id = tunnel["id"]

    print("3/5 Configuring tunnel to reach the local server...")
    config = {
        "ingress": [
            {"hostname": duckdns, "service": LOCAL_ORIGIN},
            {"service": "http_status:404"},
        ],
        "warp-routing": {"enabled": False},
        "originRequest": {"httpHostHeader": "127.0.0.1"},
    }
    api(token, "PUT", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
        {"config": config})
    print(f"    Tunnel -> {LOCAL_ORIGIN}")

    print("4/5 Deploying Worker proxy...")
    worker_src = WORKER.read_text(encoding="utf-8")
    metadata = {
        "main_module": "worker-proxy.js",
        "bindings": [
            {"type": "plain_text", "name": "TUNNEL_HOST",
             "text": f"{tunnel_id}.cfargotunnel.com"},
        ],
        "compatibility_date": "2025-01-01",
    }
    # Upload as a multipart module upload.
    boundary = "----opencode" + str(int(time.time()))
    parts = []
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="metadata"; '
        f'filename="metadata.json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"{json.dumps(metadata)}\r\n".encode()
    )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="worker-proxy.js"; '
        f'filename="worker-proxy.js"\r\n'
        f"Content-Type: application/javascript\r\n\r\n".encode()
        + worker_src.encode()
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        f"{API}/accounts/{account_id}/workers/scripts/{WORKER_NAME}",
        data=body, method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            print(f"    Worker deployed ({r.status})")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Worker upload failed {e.code}:\n{e.read().decode()}")

    print("5/5 Getting the workers.dev hostname...")
    subdomain = os.environ.get("CF_WORKERS_SUBDOMAIN", "")
    if not subdomain:
        subs = api(token, "GET", f"/accounts/{account_id}/workers/subdomain")
        subdomain = subs["result"]["subdomain"]
    workers_url = f"{WORKER_NAME}.{subdomain}.workers.dev"
    print(f"    Worker URL: {workers_url}")

    print()
    print("=" * 60)
    print("NEXT STEPS (manual):")
    print(f"1. In DuckDNS (https://www.duckdns.org), set record for")
    print(f"   '{duckdns.split('.')[0]}' to CNAME -> {workers_url}")
    print("   (DuckDNS CNAME target must be the full workers.dev URL)")
    print()
    print("2. Run the tunnel (named tunnel needs a config file):")
    print(f"   cloudflared tunnel --no-autoupdate run {TUNNEL_NAME}")
    print()
    print("3. Set Public URL in QR Code Settings to:")
    print(f"   https://{duckdns}")
    print("=" * 60)

    return workers_url


if __name__ == "__main__":
    main()
