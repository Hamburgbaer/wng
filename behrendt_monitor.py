#!/usr/bin/env python3
"""
Überwacht https://behrendt.hamburg/wohnung-mieten/ und schickt eine
Push-Nachricht (ntfy.sh) und/oder Telegram, sobald neue Inhalte
(= neue Wohnungsinserate) auftauchen.

Die Inserate werden auf der Seite vermutlich per JavaScript nachgeladen.
Deshalb rendert das Skript die Seite mit Playwright (echter Browser).

Installation (einmalig):
    pip install playwright requests
    playwright install chromium

Start (Dauerbetrieb, prüft alle 5 Minuten):
    python behrendt_monitor.py

Oder einmalig, z.B. per Cron / GitHub Actions:
    python behrendt_monitor.py --once
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

URL = "https://behrendt.hamburg/wohnung-mieten/"
STATE_FILE = Path(__file__).with_name("behrendt_state.json")
INTERVAL_SECONDS = 300  # 5 Minuten

# --- Benachrichtigung: mindestens eine Variante einrichten -----------------
# Variante A (am einfachsten): ntfy.sh -> App "ntfy" installieren,
# ein geheimes Topic abonnieren, z.B. "behrendt-wohnung-xyz123"
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")

# Variante B: Telegram-Bot (Token von @BotFather, Chat-ID von @userinfobot)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Zeilen, die zum Seitenrahmen gehören und ignoriert werden sollen
IGNORE_SNIPPETS = [
    "Cleantalk",
    "Platzhalterinhalt",
    "Erforderlichen Service",
    "Mehr Informationen",
    "Weitere Informationen",
    "Inhalt entsperren",
    "Modal",
    "Datenschutz",
    "Impressum",
]


def notify(title: str, message: str, link: str = URL) -> None:
    sent = False
    if NTFY_TOPIC:
        try:
            requests.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=message.encode("utf-8"),
                headers={
                    "Title": title.encode("utf-8"),
                    "Click": link,
                    "Priority": "high",
                    "Tags": "house",
                },
                timeout=15,
            )
            sent = True
        except requests.RequestException as e:
            print(f"ntfy-Fehler: {e}", file=sys.stderr)
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": f"{title}\n\n{message}\n{link}"},
                timeout=15,
            )
            sent = True
        except requests.RequestException as e:
            print(f"Telegram-Fehler: {e}", file=sys.stderr)
    if not sent:
        print(f"[KEINE BENACHRICHTIGUNG KONFIGURIERT]\n{title}\n{message}")


def fetch_snapshot() -> dict:
    """Lädt die Seite im Browser und gibt Textzeilen + Links zurück."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="de-DE")
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)  # Zeit für nachgeladene Inserate

        lines = set()
        links = {}

        # Haupttext der Seite + Inhalt aller Frames (falls Inserate in iframes liegen)
        for frame in page.frames:
            try:
                text = frame.inner_text("body")
            except Exception:
                continue
            for line in text.splitlines():
                line = line.strip()
                if len(line) < 3:
                    continue
                if any(s.lower() in line.lower() for s in IGNORE_SNIPPETS):
                    continue
                lines.add(line)
            try:
                for a in frame.query_selector_all("a[href]"):
                    href = a.get_attribute("href") or ""
                    label = (a.inner_text() or "").strip().replace("\n", " ")
                    if href.startswith("http"):
                        links[href] = label
            except Exception:
                pass

        browser.close()
    return {"lines": sorted(lines), "links": links}


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return None


def save_state(snapshot: dict) -> None:
    STATE_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def check() -> None:
    snap = fetch_snapshot()
    old = load_state()

    if old is None:
        save_state(snap)
        print(f"Ersterfassung gespeichert ({len(snap['lines'])} Zeilen, {len(snap['links'])} Links).")
        notify("Behrendt-Monitor gestartet", "Überwachung läuft. Du wirst bei neuen Inhalten benachrichtigt.")
        return

    new_lines = [l for l in snap["lines"] if l not in set(old["lines"])]
    new_links = {h: t for h, t in snap["links"].items() if h not in old["links"]}

    if new_lines or new_links:
        parts = []
        for href, label in list(new_links.items())[:5]:
            parts.append(f"• {label or 'Neuer Link'}\n  {href}")
        if not parts:
            parts = [f"• {l}" for l in new_lines[:8]]
        notify("🏠 Neues Behrendt-Inserat?", "\n".join(parts))
        print("Änderung erkannt:", new_lines[:8], list(new_links)[:5])
    else:
        print(time.strftime("%H:%M:%S"), "keine Änderung")

    save_state(snap)  # Zustand immer aktualisieren


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Nur einmal prüfen und beenden")
    args = ap.parse_args()

    if args.once:
        check()
        return

    while True:
        try:
            check()
        except Exception as e:
            print(f"Fehler beim Prüfen: {e}", file=sys.stderr)
        time.sleep(INTERVAL_SECONDS + random.randint(0, 60))


if __name__ == "__main__":
    main()
