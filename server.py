#!/usr/bin/env python3
import http.server
import webbrowser
import threading
import os

PORT = 8080
DIR = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)
    def log_message(self, format, *args):
        pass  # silencia logs no terminal

def open_browser():
    webbrowser.open(f'http://localhost:{PORT}/dashboard.html')

print(f"Dashboard rodando em: http://localhost:{PORT}/dashboard.html")
print("Ctrl+C para parar\n")

threading.Timer(0.5, open_browser).start()

with http.server.HTTPServer(('', PORT), Handler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor parado.")
