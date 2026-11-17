// Cloudflare Worker - permanent proxy to the Cloudflare named tunnel.
// The Workers.dev hostname is permanent, so the DuckDNS CNAME never changes.
export default {
  async fetch(request, env) {
    const origin = new URL(request.url);
    origin.protocol = "https:";
    origin.hostname = env.TUNNEL_HOST;
    origin.port = "";

    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("cf-connecting-ip");

    const init = {
      method: request.method,
      headers: headers,
      redirect: "manual",
    };

    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = await request.arrayBuffer();
    }

    const res = await fetch(origin.toString(), init);
    const outHeaders = new Headers(res.headers);
    outHeaders.delete("cf-ray");
    outHeaders.set("x-proxied-by", "transport-worker");
    return new Response(res.body, {
      status: res.status,
      headers: outHeaders,
    });
  },
};
