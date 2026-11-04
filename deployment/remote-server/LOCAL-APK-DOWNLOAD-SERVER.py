import http.server
import pathlib
import shutil
import sys


apk_path = pathlib.Path(sys.argv[1]).resolve(strict=True)
port = int(sys.argv[2])


class ApkDownloadHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self._send_apk_headers()

    def do_GET(self):
        if not self._send_apk_headers():
            return
        with apk_path.open("rb") as apk_file:
            shutil.copyfileobj(apk_file, self.wfile)

    def _send_apk_headers(self):
        if self.path != "/download/android":
            self.send_error(404)
            return False
        self.send_response(200)
        self.send_header(
            "Content-Type", "application/vnd.android.package-archive"
        )
        self.send_header("Content-Length", str(apk_path.stat().st_size))
        self.send_header(
            "Content-Disposition", 'attachment; filename="LHC-Transport.apk"'
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return True

    def log_message(self, format_string, *args):
        print(
            "%s - %s" % (
                self.address_string(), format_string % args
            ),
            flush=True,
        )


server = http.server.ThreadingHTTPServer(("0.0.0.0", port), ApkDownloadHandler)
server.serve_forever()
