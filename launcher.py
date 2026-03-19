#!/usr/bin/env python3
"""
launcher.py — Polymarket Hunter
  Inicia o bot + o dashboard juntos.
  Tudo que aparece no CMD aparece no dashboard ao vivo.

  CONFIGURAÇÃO:
    BOT_DIR  → pasta onde está o seu bot (onde fica o package.json)
    BOT_CMD  → comando para iniciar o bot

  COMO USAR:
    1. Coloque este arquivo na mesma pasta que o dashboard.html e server.py
    2. Ajuste BOT_DIR abaixo
    3. Execute: python launcher.py   (ou use o Rodar Bot Completo.bat)
"""

import subprocess, threading, time, re, json, os, sys, webbrowser, http.server

# ══════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO — ajuste BOT_DIR para a pasta do seu bot
# ══════════════════════════════════════════════════════════════
PORT     = 8080
DASH_DIR = os.path.dirname(os.path.abspath(__file__))

# Caminho do bot — por padrão, diretório pai desta pasta.
# Altere para o caminho completo se necessário, ex:
#   BOT_DIR = r"C:\Users\Biel\polynovointegrardashboard"
BOT_DIR  = r"C:\Users\biel\Desktop\PolyBot NEW"

BOT_CMD  = ['npm', 'start']   # comando que inicia o bot
# ══════════════════════════════════════════════════════════════

STATE_F = os.path.join(DASH_DIR, 'bot_state.json')

_state = {
    "_ts": 0,
    "pnl": 0.0, "balance": 0.0, "active_bets": 0,
    "wins": 0, "total_bets": 0,
    "signal": "INICIANDO",
    "session_label": "aguardando bot...",
    "log": [], "markets": [], "resolved": [],
    "binance": {}, "confidence": [],
    "modules": [
        {"name": "Copy Trade",           "status": "Aguardando", "rows": []},
        {"name": "Bond Feed",            "status": "Aguardando", "rows": []},
        {"name": "Weather Feed",         "status": "Aguardando", "rows": []},
        {"name": "Crypto Up/Down 5 Min", "status": "Aguardando", "rows": []},
    ],
    "bot_running": False,
}

_lock = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────────

def _ts():
    return int(time.time())

def _save():
    _state["_ts"] = _ts()
    try:
        with open(STATE_F, 'w', encoding='utf-8') as f:
            json.dump(_state, f, ensure_ascii=False)
    except Exception:
        pass

def add_log(line: str):
    """Adiciona linha ao log do dashboard (com timestamp)."""
    ts = time.strftime('%H:%M:%S')
    entry = f"[{ts}] {line}"
    with _lock:
        _state["log"].append(entry)
        if len(_state["log"]) > 300:
            _state["log"].pop(0)


# ── Parser de saída do bot ──────────────────────────────────────────────────

# Padrões para extrair dados do output do bot
_RE_PNL        = re.compile(r'[Pp][\s&]*[Ll]\s*[=:]\s*([+-]?\$?\s*[\d.,]+)', re.I)
_RE_PNL2       = re.compile(r'lucro|profit|pnl\s*([+-]?\$?\s*[\d.,]+)', re.I)
_RE_BALANCE    = re.compile(r'saldo|balance\s*[=:]\s*\$?\s*([\d.,]+)', re.I)
_RE_ACTIVE_BETS= re.compile(r'apostas?\s+ativas?\s*[=:]?\s*(\d+)', re.I)
_RE_WINS       = re.compile(r'\b(WON|WIN|GANHOU|acertou)\b', re.I)
_RE_LOSS       = re.compile(r'\b(LOST|LOSS|PERDEU|errou)\b', re.I)
_RE_BETS_TOTAL = re.compile(r'trades?\s*[=:]?\s*(\d+)\s*/\s*(\d+)', re.I)
_RE_PRICE      = re.compile(r'([A-Z]{2,6})[/\s]USDT\s*[=:@]\s*\$?\s*([\d.,]+)', re.I)
_RE_MARKET     = re.compile(r'mercado\s*[=:]?\s*(.+?)@\s*([\d.]+)', re.I)
_RE_RESOLVE    = re.compile(r'(resolvido|resolved|settled).+?(YES|NO).+?([+-]?\$?[\d.,]+)', re.I)
_RE_SIGNAL     = re.compile(r'\b(BULLISH|BEARISH|LONG|SHORT|SCANNING|ARBITRAGE|NEUTRAL)\b', re.I)
_RE_MODULE     = re.compile(r'\[(Copy Trade|Bond Feed|Weather Feed|Crypto[^]]*)\]\s*(.*)', re.I)


def _to_float(s: str) -> float:
    return float(s.replace('$', '').replace(',', '.').strip())


def parse_line(line: str):
    """Extrai dados estruturados de uma linha de output do bot."""
    try:
        # PnL
        m = _RE_PNL.search(line)
        if m:
            _state["pnl"] = _to_float(m.group(1))

        # Balance
        m = _RE_BALANCE.search(line)
        if m:
            _state["balance"] = _to_float(m.group(1))

        # Apostas ativas
        m = _RE_ACTIVE_BETS.search(line)
        if m:
            _state["active_bets"] = int(m.group(1))

        # Win
        if _RE_WINS.search(line):
            _state["wins"]       = _state.get("wins", 0) + 1
            _state["total_bets"] = _state.get("total_bets", 0) + 1

        # Loss
        elif _RE_LOSS.search(line):
            _state["total_bets"] = _state.get("total_bets", 0) + 1

        # Trades N/M
        m = _RE_BETS_TOTAL.search(line)
        if m:
            _state["wins"]       = int(m.group(1))
            _state["total_bets"] = int(m.group(2))

        # Sinal
        m = _RE_SIGNAL.search(line)
        if m:
            _state["signal"] = m.group(1).upper()

        # Preço Binance
        m = _RE_PRICE.search(line)
        if m:
            sym = m.group(1).upper() + '/USDT'
            try:
                price = _to_float(m.group(2))
                _state.setdefault("binance", {})[sym] = {"price": price, "change": 0}
            except Exception:
                pass

        # Módulo
        m = _RE_MODULE.search(line)
        if m:
            mod_name = m.group(1).strip()
            mod_msg  = m.group(2).strip()
            for mod in _state.get("modules", []):
                if mod_name.lower() in mod["name"].lower():
                    mod["status"] = mod_msg[:30] if mod_msg else "Ativo"
                    break

        # Resolve
        m = _RE_RESOLVE.search(line)
        if m:
            resolved = _state.setdefault("resolved", [])
            resolved.append({
                "market":  "mercado",
                "outcome": m.group(2).upper(),
                "pnl":     _to_float(m.group(3)) if m.group(3) else 0,
            })
            if len(resolved) > 20:
                resolved.pop(0)

    except Exception:
        pass   # parser nunca quebra o bot


# ── Runner do bot ───────────────────────────────────────────────────────────

def run_bot():
    """Inicia o bot como subprocess e captura tudo que ele imprime."""
    with _lock:
        _state["bot_running"] = True
        _state["signal"]      = "INICIANDO BOT"
    add_log("═" * 48)
    add_log("  POLYMARKET HUNTER — INICIANDO")
    add_log(f"  Diretório: {BOT_DIR}")
    add_log(f"  Comando  : {' '.join(BOT_CMD)}")
    add_log("═" * 48)
    _save()

    # Verifica se o diretório existe
    if not os.path.isdir(BOT_DIR):
        add_log(f"ERRO: BOT_DIR não existe: {BOT_DIR}")
        add_log("Edite launcher.py e ajuste BOT_DIR para a pasta do seu bot.")
        _save()
        return

    try:
        proc = subprocess.Popen(
            BOT_CMD,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=BOT_DIR,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )

        with _lock:
            _state["signal"] = "RODANDO"

        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            print(line, flush=True)   # ainda mostra no CMD
            with _lock:
                add_log(line)
                parse_line(line)
            _save()

        proc.wait()
        code = proc.returncode
        add_log("═" * 48)
        add_log(f"  BOT ENCERRADO  (código de saída: {code})")
        add_log("═" * 48)

    except FileNotFoundError:
        add_log(f"ERRO: '{BOT_CMD[0]}' não encontrado.")
        add_log("Certifique-se de que o Node.js / npm está instalado e no PATH.")
    except Exception as e:
        add_log(f"ERRO AO INICIAR BOT: {e}")
    finally:
        with _lock:
            _state["bot_running"] = False
            _state["signal"]      = "BOT ENCERRADO"
        _save()


# ── Servidor HTTP ───────────────────────────────────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DASH_DIR, **kw)

    def do_GET(self):
        if self.path == '/api/state':
            with _lock:
                body = json.dumps(_state).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/update':
            n = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(n)
            try:
                data = json.loads(body)
                with _lock:
                    for k, v in data.items():
                        _state[k] = v
                    _state["_ts"] = _ts()
                _save()
                self._json(200, b'{"ok":true}')
            except Exception as e:
                self._json(400, json.dumps({"error": str(e)}).encode())
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _json(self, code, body: bytes):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, fmt, *args):
        # Silencia logs de requests bem-sucedidos
        if args and str(args[1]) not in ('200', '304'):
            print(f'[HTTP] {fmt % args}')


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    print(f"\n{'═'*54}")
    print(f"  POLYMARKET HUNTER · LAUNCHER")
    print(f"{'═'*54}")
    print(f"  Dashboard : http://localhost:{PORT}/dashboard.html")
    print(f"  API state : GET  http://localhost:{PORT}/api/state")
    print(f"  API update: POST http://localhost:{PORT}/api/update")
    print(f"  Bot dir   : {BOT_DIR}")
    print(f"{'═'*54}\n")

    # Inicializa state file
    _save()

    # Abre o browser após 1 segundo
    threading.Timer(
        1.0,
        lambda: webbrowser.open(f'http://localhost:{PORT}/dashboard.html')
    ).start()

    # Inicia o bot em background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Inicia o servidor HTTP (bloqueia aqui)
    server = http.server.HTTPServer(('', PORT), _Handler)
    try:
        print('[SERVER] Aguardando conexões… (Ctrl+C para parar)\n')
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[SERVER] Parado pelo usuário.')


if __name__ == '__main__':
    main()
