#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor loc de parcare de resedinta - Sector 4 (resedinta.mobilitateurbana4.ro)

Verifica periodic endpoint-ul /ajax/check-available pentru unul sau mai multe
id-uri (adresa/loc) si te anunta cand raspunsul nu mai e "0", adica s-a
eliberat un loc in zona ta.

Ce stim sigur din captura DevTools:
  - POST https://resedinta.mobilitateurbana4.ro/ajax/check-available
  - body: id=<numar>   (id=9814 = adresa ta)
  - raspuns "0" cand NU exista loc disponibil
  - cand va exista, raspunsul e diferit de "0" (confirmam la prima alerta;
    scriptul logheaza raspunsul brut, ca sa ajustam regula daca e nevoie)

Ruleaza pe NAS, ca serviciu sau din cron. Nu necesita browser.
Dependinta: pip install requests
"""

import os
import time
import sys
import argparse
from datetime import datetime

import requests

# ===================== CONFIG =====================

def _env(name, default):
    """Citeste din variabila de mediu daca exista, altfel foloseste valoarea din fisier."""
    v = os.getenv(name)
    return v if v not in (None, "") else default

BASE = "https://resedinta.mobilitateurbana4.ro"
CHECK_URL = BASE + "/ajax/check-available"

# id-urile de urmarit. 9814 = adresa ta (din captura).
# In fisier: pune lista direct.   Override din env: IDS="9814,1234"
IDS = [int(x) for x in _env("IDS", "9814").replace(" ", "").split(",") if x]

# Cat de des verifica (secunde). 300 = 5 min. Nu cobori sub ~120.
INTERVAL = int(_env("INTERVAL", "300"))

# Cand un loc e disponibil, repeta alerta la fiecare atatea secunde,
# ca sa nu ratezi notificarea. 0 = anunta o singura data la trecere.
REPEAT_ALERT = int(_env("REPEAT_ALERT", "1800"))  # 30 min

# ---------------- NOTIFICARE (alege una) ----------------
# Varianta A - ntfy.sh (instalezi app-ul "ntfy" pe telefon, te abonezi la topic).
NTFY_TOPIC = _env("NTFY_TOPIC", "")    # ex: "parcare-vlad-7x9k2q" (greu de ghicit)
NTFY_SERVER = _env("NTFY_SERVER", "https://ntfy.sh")

# Varianta B - Telegram (bot de la @BotFather; chat_id de la @userinfobot).
TG_TOKEN = _env("TG_TOKEN", "")
TG_CHAT_ID = _env("TG_CHAT_ID", "")
# ========================================================

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# raspunsuri considerate "indisponibil"
INDISPONIBIL = {"", "0", "false", "null", "[]", "{}"}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fresh_session():
    """Sesiune noua + cookie PHPSESSID luat de pe homepage (ca un browser)."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
    })
    try:
        s.get(BASE + "/", timeout=20)
    except requests.RequestException as e:
        log(f"Atentie: nu am luat cookie de pe homepage ({e}). Continui oricum.")
    return s


def check(session, spot_id):
    """Returneaza (disponibil: bool, raspuns_brut: str)."""
    r = session.post(
        CHECK_URL,
        data={"id": str(spot_id)},
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": BASE,
            "Referer": BASE + "/",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=20,
    )
    body = (r.text or "").strip()
    return (body not in INDISPONIBIL), body


def notify(title, message):
    sent = False
    if NTFY_TOPIC:
        try:
            requests.post(
                f"{NTFY_SERVER}/{NTFY_TOPIC}",
                data=message.encode("utf-8"),
                headers={
                    "Title": title.encode("utf-8"),
                    "Priority": "urgent",
                    "Tags": "rotating_light",
                    "Click": BASE + "/",
                },
                timeout=15,
            )
            sent = True
        except requests.RequestException as e:
            log(f"ntfy esuat: {e}")
    if TG_TOKEN and TG_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                data={"chat_id": TG_CHAT_ID,
                      "text": f"{title}\n{message}\n{BASE}/"},
                timeout=15,
            )
            sent = True
        except requests.RequestException as e:
            log(f"Telegram esuat: {e}")
    if not sent:
        log(f"*** {title} -- {message} ***")


def run(once=False):
    log(f"Pornit. Urmaresc id-urile {IDS}, verific la {INTERVAL}s.")
    last_available = {i: False for i in IDS}
    last_alert_ts = {i: 0.0 for i in IDS}

    while True:
        session = fresh_session()
        for spot_id in IDS:
            try:
                available, body = check(session, spot_id)
            except requests.RequestException as e:
                log(f"id {spot_id}: eroare retea ({e})")
                continue

            now = time.time()
            if available:
                first_time = not last_available[spot_id]
                due_repeat = REPEAT_ALERT and (now - last_alert_ts[spot_id] >= REPEAT_ALERT)
                if first_time or due_repeat:
                    notify(
                        "Loc de parcare DISPONIBIL!",
                        f"S-a eliberat un loc (id {spot_id}). Intra rapid si "
                        f"trimite solicitarea. (raspuns server: {body})",
                    )
                    last_alert_ts[spot_id] = now
                    log(f"id {spot_id}: DISPONIBIL (raspuns {body!r}) -> alerta trimisa")
                else:
                    log(f"id {spot_id}: inca disponibil (raspuns {body!r})")
                last_available[spot_id] = True
            else:
                if last_available[spot_id]:
                    log(f"id {spot_id}: a redevenit indisponibil")
                else:
                    log(f"id {spot_id}: indisponibil (raspuns {body!r})")
                last_available[spot_id] = False

        if once:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Monitor parcare resedinta S4")
    ap.add_argument("--once", action="store_true",
                    help="o singura verificare, apoi iese (pentru test)")
    args = ap.parse_args()
    try:
        run(once=args.once)
    except KeyboardInterrupt:
        log("Oprit.")
        sys.exit(0)
