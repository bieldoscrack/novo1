#!/usr/bin/env python3
"""
Servidor do Dashboard — Polymarket Hunter
  GET  /dashboard.html  → serve o painel
  GET  /api/state       → retorna estado atual (lido do bot_state.json)
  POST /api/update      → bot envia JSON para atualizar o dashboard
"""
import http.server, json, os, threading, webbrowser, time

PORT     = 8080
DIR      = os.path.dirname(os.path.abspath(__file__))
STATE_F  = os.path.join(DIR, 'bot_state.json')

# Estado inicial vazio
EMPTY = {
    "_ts": 0,
    "pnl": 0, "balance": 0, "active_bets": 0,
    "wins": 0, "total_bets": 0,
    "signal": "SCANNING", "session_label": "sem dados",
    "log": [], "markets": [], "resolved": [],
    "binance": {},
    "confidence": []
}

if not os.path.exists(STATE_F):
    with open(STATE_F, 'w') as f:
        json.dump(EMPTY, f, indent=2)

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIR, **kw)

    def do_GET(self):
        if self.path == '/api/state':
            try:
                with open(STATE_F) as f:
                    data = f.read()
            except Exception:
                data = json.dumps(EMPTY)
            self._json(200, data)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/update':
            n = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(n)
            try:
                state = json.loads(body)
                state['_ts'] = int(time.time())
                with open(STATE_F, 'w') as f:
                    json.dump(state, f, indent=2)
                self._json(200, '{"ok":true}')
                print(f"[BOT] P&L={state.get('pnl',0):+.2f}  "
                      f"bets={state.get('active_bets',0)}  "
                      f"signal={state.get('signal','?')}")
            except Exception as e:
                self._json(400, f'{{"error":"{e}"}}')
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors(); self.end_headers()

    def _json(self, code, body):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(b)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, fmt, *args):
        # Só mostra erros, não cada request
        if args and str(args[1]) not in ('200','304'):
            print(f"[HTTP] {fmt % args}")

threading.Timer(0.6, lambda: webbrowser.open(f'http://localhost:{PORT}/dashboard.html')).start()

print(f"\n{'='*50}")
print(f"  Dashboard: http://localhost:{PORT}/dashboard.html")
print(f"  Bot API:   POST http://localhost:{PORT}/api/update")
print(f"{'='*50}\n")
print("Conecte seu bot enviando POST /api/update com JSON.")
print("Ctrl+C para parar.\n")

with http.server.HTTPServer(('', PORT), Handler) as s:
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor parado.")
