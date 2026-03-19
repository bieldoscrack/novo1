"""
bot_bridge.py — Cole este código no seu bot para alimentar o dashboard

Uso:
  from bot_bridge import DashboardBridge
  dash = DashboardBridge()

  # Quando tiver dados novos:
  dash.update(
      pnl=1234.56,
      balance=5000.00,
      active_bets=3,
      wins=42, total_bets=80,
      signal="BULLISH BTC",
      log=["[12:00:01] Aposta confirmada $120 YES...", ...],
      markets=[{"name":"BTC>70k Apr","price":0.61,"change":2.3}],
      binance={"BTC/USDT":{"price":68420,"change":2.1}},
      resolved=[{"market":"SOL>200","outcome":"YES","pnl":44}],
      confidence=[
          {"label":"BTC model","pct":78,"color":"#00ff88"},
          {"label":"Edge score","pct":83,"color":"#ff8800"},
      ]
  )
"""
import requests, time, logging

log = logging.getLogger(__name__)

class DashboardBridge:
    def __init__(self, url='http://localhost:8080/api/update'):
        self.url = url
        self._log_buffer = []

    def add_log(self, msg: str):
        """Adiciona linha ao log (chame isso ao invés de print)"""
        ts = time.strftime('%H:%M:%S')
        self._log_buffer.append(f'[{ts}] {msg}')
        if len(self._log_buffer) > 50:
            self._log_buffer.pop(0)
        log.info(msg)

    def update(self, **kwargs):
        """Envia estado atual para o dashboard. Todos os campos são opcionais."""
        payload = {
            'pnl':           kwargs.get('pnl', 0),
            'balance':       kwargs.get('balance', 0),
            'active_bets':   kwargs.get('active_bets', 0),
            'wins':          kwargs.get('wins', 0),
            'total_bets':    kwargs.get('total_bets', 0),
            'signal':        kwargs.get('signal', 'SCANNING'),
            'session_label': kwargs.get('session_label', 'sessão atual'),
            'log':           kwargs.get('log', self._log_buffer),
            'markets':       kwargs.get('markets', []),
            'binance':       kwargs.get('binance', {}),
            'resolved':      kwargs.get('resolved', []),
            'confidence':    kwargs.get('confidence', []),
        }
        try:
            requests.post(self.url, json=payload, timeout=1)
        except Exception:
            pass  # dashboard offline não quebra o bot


# ── Exemplo de uso standalone (teste) ──
if __name__ == '__main__':
    import random, time

    dash = DashboardBridge()
    pnl = 0
    wins = 0
    total = 0

    print("Enviando dados de teste para o dashboard… (Ctrl+C para parar)")

    while True:
        won = random.random() > 0.45
        trade_pnl = random.uniform(20, 150) if won else -random.uniform(10, 80)
        pnl   += trade_pnl
        total += 1
        if won: wins += 1

        dash.add_log(f"Aposta {'WON' if won else 'LOST'}: {trade_pnl:+.2f}")

        dash.update(
            pnl=round(pnl, 2),
            balance=round(5000 + pnl, 2),
            active_bets=random.randint(1, 10),
            wins=wins,
            total_bets=total,
            signal=random.choice(['BULLISH BTC','BEARISH ETH','LONG BTC','SCANNING']),
            markets=[
                {"name":"BTC>70k Apr","price":round(random.uniform(.45,.75),2),"change":round(random.uniform(-3,5),1)},
                {"name":"ETH<3k Mar", "price":round(random.uniform(.3,.6),2), "change":round(random.uniform(-2,3),1)},
            ],
            binance={
                "BTC/USDT":{"price":round(68000+random.uniform(-500,500),0),"change":round(random.uniform(-2,3),2)},
                "ETH/USDT":{"price":round(3200+random.uniform(-100,100),0), "change":round(random.uniform(-2,2),2)},
            },
            resolved=[
                {"market":"SOL>200","outcome":"YES","pnl":44},
                {"market":"ETH<3k", "outcome":"NO", "pnl":-30},
            ],
            confidence=[
                {"label":"BTC model",    "pct":78, "color":"#00ff88"},
                {"label":"ETH model",    "pct":62, "color":"#00aaff"},
                {"label":"Market timing","pct":55, "color":"#ffcc00"},
                {"label":"Edge score",   "pct":83, "color":"#ff8800"},
            ]
        )
        time.sleep(3)
