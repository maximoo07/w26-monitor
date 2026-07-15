#!/usr/bin/env python3
"""
W26 (WORLD CUP 2026) Price Monitor -> Notifiche push ntfy.sh
Versione "web service" per hosting gratuito 24/7 su Render.com

Espone un piccolo endpoint HTTP (/) che risponde "OK" — serve solo per
far credere a Render/UptimeRobot che questo e' un sito web, cosi il
servizio gratuito non va mai in stand-by. Il vero lavoro (controllo
prezzo ogni 15s + notifiche push via ntfy.sh) gira in un thread in
background.
"""

import os
import time
import threading
import requests
from datetime import datetime
from flask import Flask

# ============================== CONFIG ==============================

TOKEN_ADDRESS = "AYYfBtUEwQp5ynUD4AUd8azoyxgtp7oH7QsSBvCmmoon"  # W26 su Solana

# Su Render, meglio mettere questo come "Environment Variable" nel pannello
# invece di scriverlo qui in chiaro (specialmente se il repo GitHub e' pubblico).
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "INSERISCI_QUI_IL_TUO_TOPIC_NTFY")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

CHECK_INTERVAL_SECONDS = 15
MEDIUM_MOVE_PCT = 5.0
STRONG_MOVE_PCT = 12.0
MARKET_CAP_THRESHOLD_EUR = 2_000_000
EUR_USD_RATE = 1.08

DEXSCREENER_URL = f"https://api.dexscreener.com/latest/dex/tokens/{TOKEN_ADDRESS}"

# ======================================================================

app = Flask(__name__)


@app.route("/")
def health_check():
    """Endpoint per UptimeRobot / Render: conferma solo che il processo e' vivo."""
    return "OK - W26 monitor attivo", 200


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def send_notification(message: str, title: str = "W26 Alert", priority: str = "default") -> None:
    """Invia una notifica push tramite ntfy.sh."""
    try:
        r = requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[{now()}] ERRORE invio ntfy: {r.status_code} - {r.text}")
    except requests.RequestException as e:
        print(f"[{now()}] ERRORE connessione ntfy: {e}")


def fetch_price_and_mcap():
    try:
        r = requests.get(DEXSCREENER_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        pairs = data.get("pairs") or []
        if not pairs:
            print(f"[{now()}] Nessun pair trovato su DexScreener.")
            return None
        best_pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        price_usd = float(best_pair["priceUsd"])
        market_cap = best_pair.get("marketCap") or best_pair.get("fdv") or 0
        liquidity = best_pair.get("liquidity", {}).get("usd", 0)
        return price_usd, float(market_cap), float(liquidity)
    except (requests.RequestException, KeyError, ValueError, TypeError) as e:
        print(f"[{now()}] ERRORE lettura dati DexScreener: {e}")
        return None


def monitor_loop():
    print(f"[{now()}] Avvio monitor W26 in background. Controllo ogni {CHECK_INTERVAL_SECONDS}s.")

    result = None
    while result is None:
        result = fetch_price_and_mcap()
        if result is None:
            print(f"[{now()}] Prezzo iniziale non disponibile, riprovo tra 15s...")
            time.sleep(15)

    checkpoint_price, market_cap, liquidity = result
    print(f"[{now()}] Prezzo iniziale: ${checkpoint_price:.8f} | Market cap: ${market_cap:,.0f}")

    send_notification(
        f"Prezzo iniziale: ${checkpoint_price:.8f}\n"
        f"Market cap: ${market_cap:,.0f}\n"
        f"Liquidita: ${liquidity:,.0f}",
        title="Monitor W26 avviato",
    )

    mcap_alert_sent = market_cap >= MARKET_CAP_THRESHOLD_EUR * EUR_USD_RATE
    strong_move_triggered = False

    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)

        result = fetch_price_and_mcap()
        if result is None:
            continue

        current_price, market_cap, liquidity = result
        pct_change = ((current_price - checkpoint_price) / checkpoint_price) * 100

        print(f"[{now()}] Prezzo: ${current_price:.8f} | Var: {pct_change:+.2f}% | MCap: ${market_cap:,.0f}")

        mcap_threshold_usd = MARKET_CAP_THRESHOLD_EUR * EUR_USD_RATE
        if market_cap >= mcap_threshold_usd and not mcap_alert_sent:
            send_notification(
                f"W26 ha superato {MARKET_CAP_THRESHOLD_EUR:,.0f} EUR di market cap!\n"
                f"Market cap attuale: ${market_cap:,.0f}\n"
                f"Prezzo: ${current_price:.8f}",
                title="📈 MARKET CAP ALERT",
                priority="high",
            )
            mcap_alert_sent = True
        elif market_cap < mcap_threshold_usd * 0.9:
            mcap_alert_sent = False

        if abs(pct_change) >= STRONG_MOVE_PCT and not strong_move_triggered:
            direction = "🚀 SALITA FORTE" if pct_change > 0 else "🔻 CROLLO FORTE"
            send_notification(
                f"W26 si e' mosso del {pct_change:+.2f}%\n"
                f"Prezzo: ${current_price:.8f} (checkpoint: ${checkpoint_price:.8f})",
                title=f"🚨 ALLERTA {direction}",
                priority="urgent",
            )
            strong_move_triggered = True
            checkpoint_price = current_price
        elif abs(pct_change) >= MEDIUM_MOVE_PCT:
            direction = "su" if pct_change > 0 else "giu"
            send_notification(
                f"W26 e' {direction} del {pct_change:+.2f}%\n"
                f"Prezzo: ${current_price:.8f} (checkpoint: ${checkpoint_price:.8f})",
                title="⚠️ Avviso movimento prezzo",
                priority="default",
            )
            checkpoint_price = current_price
            strong_move_triggered = False


# Avvia il thread di monitoraggio non appena il modulo viene importato da gunicorn
monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
monitor_thread.start()

if __name__ == "__main__":
    # Solo per test in locale; su Render sara' gunicorn ad avviare "app"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
