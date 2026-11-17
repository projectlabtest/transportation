"""Point the DuckDNS record at the Cloudflare Worker (CNAME).

Usage:  python deployment/setup-duckdns.py <duckdns_token> [hostname]
"""

import json
import sys
import urllib.parse
import urllib.request

DUCKDNS_UPDATE = "https://www.duckdns.org/update"


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    token = sys.argv[1].strip()
    host = (sys.argv[2] if len(sys.argv) > 2
            else "transport-booking.duckdns.org").strip()
    subdomain = host.split(".")[0]
    target = "transport-booking-proxy.transport-booking.workers.dev"

    params = {"domains": subdomain, "token": token, "cname": target, "verbose": "true"}
    url = DUCKDNS_UPDATE + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        out = r.read().decode().strip()
    print("DuckDNS response:", out)
    if "OK" not in out.upper():
        raise SystemExit("DuckDNS update may have failed.")
    print("CNAME set:", subdomain, "->", target)


if __name__ == "__main__":
    main()
