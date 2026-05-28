#!/usr/bin/env python3
"""Windows-side LAN proxy for the WSL Immich Frameo bridge.
Binds on Windows LAN and forwards HTTP to WSL/localhost bridge.
"""
import http.server
import socketserver
import urllib.request
import urllib.error
import sys

LISTEN_HOST = '0.0.0.0'
LISTEN_PORT = 8098
UPSTREAM = 'http://127.0.0.1:8099'

class Proxy(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write('%s - - [%s] %s\n' % (self.client_address[0], self.log_date_time_string(), fmt % args))
    def do_GET(self):
        url = UPSTREAM + self.path
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'Immich-Frame-Kiosk-Proxy'})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                self.send_response(r.status)
                self.send_header('Content-Type', r.headers.get('Content-Type','application/octet-stream'))
                self.send_header('Cache-Control','no-store')
                self.send_header('Access-Control-Allow-Origin','*')
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            body = ('Proxy error: %s: %s' % (type(e).__name__, e)).encode()
            self.send_response(502)
            self.send_header('Content-Type','text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)

class Reuse(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

if __name__ == '__main__':
    print(f'Frameo LAN proxy listening on http://{LISTEN_HOST}:{LISTEN_PORT}/ -> {UPSTREAM}', flush=True)
    Reuse((LISTEN_HOST, LISTEN_PORT), Proxy).serve_forever()
