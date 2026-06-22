#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor pret produs JYSK (saltea REVA) - anunta cand pretul SCADE.

Citeste pagina produsului, extrage pretul, il compara cu ultimul pret
salvat in pret_state.json si trimite notificare (ntfy/Telegram) la scadere.
Pretul curent il scrie inapoi in pret_state.json (comis de workflow).

Ruleaza in GitHub Actions sau oriunde. Dependinta: pip install requests
"""

import os
import re
import sys
import json
from datetime import datetime

import requests


def _env(name, default):
    v = os.getenv(name)
    return v if v not in (None, "") else default


# ===================== CONFIG =====================
PRODUCT_URL = _env(
    "PRODUCT_URL",
    "https://jysk.ro/dormitor/saltele/saltele-cu-arcuri/saltea-arcuri-160x200cm-reva-alb-tare",
)
STATE_FILE = _env("STATE_FILE", "pret_state.json")

# Optional: prag. Daca pretul ajunge <= acest prag (si inainte era peste),
# primesti o notificare separata. Lasa gol ca sa-l ignori.  Ex: TARGET_PRICE="3500"
TARGET_PRICE = _env("TARGET_PRICE", "")

# Notificare (acelasi topic ntfy ca la parcare e perfect)
NTFY_TOPIC = _env("NTFY_TOPIC", "")
NTFY_SERVER = _env("NTFY_SERVER", "https://ntfy.sh")
TG_TOKEN = _env("TG_TOKEN", "")
TG_CHAT_ID = _env("TG_CHAT_ID", "")
# ==================================================

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _to_float(s):
    s = s.strip().replace("\xa0", " ").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.match(r"^\d{1,3}(\.\d{3})+$", s):  # 4.299 = separator de mii
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _find_price_in_jsonld(node):
    """Cauta recursiv un camp 'price' / 'lowPrice' intr-un bloc JSON-LD."""
    if isinstance(node, dict):
        for key in ("price", "lowPrice"):
            if key in node:
                p = _to_float(str(node[key]))
                if p:
                    return p
        for v in node.values():
            p = _find_price_in_jsonld(v)
            if p:
                return p
    elif isinstance(node, list):
        for v in node:
            p = _find_price_in_jsonld(v)
            if p:
                return p
    return None


def extract_price(html):
    """Returneaza (pret: float|None, metoda: str)."""
    # 1) JSON-LD (cel mai sigur, prinde si pretul redus)
    for m in re.finditer(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I
    ):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        p = _find_price_in_jsonld(data)
        if p:
            return p, "json-ld"

    # 2) Ancora pe text: "<numar> Lei /buc" (pretul principal al produsului)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"(\d[\d.,\s]{0,12}?)\s*Lei\s*/\s*buc", text, re.I)
    if m:
        p = _to_float(m.group(1))
        if p:
            return p, "text /buc"

    return None, "n/a"


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def notify(title, message):
    sent = False
    if NTFY_TOPIC:
        try:
            requests.post(
                f"{NTFY_SERVER}/{NTFY_TOPIC}",
                data=message.encode("utf-8"),
                headers={
                    "Title": title.encode("utf-8"),
                    "Priority": "high",
                    "Tags": "money_with_wings",
                    "Click": PRODUCT_URL,
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
                data={"chat_id": TG_CHAT_ID, "text": f"{title}\n{message}\n{PRODUCT_URL}"},
                timeout=15,
            )
            sent = True
        except requests.RequestException as e:
            log(f"Telegram esuat: {e}")
    if not sent:
        log(f"*** {title} -- {message} ***")


def main():
    try:
        r = requests.get(
            PRODUCT_URL,
            headers={"User-Agent": UA, "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8"},
            timeout=30,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log(f"Eroare retea ({e}). Reincerc data viitoare.")
        return 0  # tranzitoriu, nu stricam starea

    price, method = extract_price(r.text)
    if price is None:
        log("Nu am gasit pretul in pagina. Probabil s-a schimbat structura -> de reglat.")
        return 1  # apare rosu in Actions ca sa observi

    log(f"Pret curent: {price:.2f} lei (metoda: {method})")

    state = load_state()
    prev = state.get("price")

    if prev is None:
        log("Primul pret salvat (baseline). Fara notificare.")
    elif price < prev:
        notify(
            "Pret SALTEA scazut!",
            f"REVA 160x200: a scazut de la {prev:.0f} la {price:.0f} lei "
            f"(-{prev - price:.0f} lei).",
        )
        log(f"SCADERE {prev:.0f} -> {price:.0f} -> alerta trimisa")
    elif price > prev:
        log(f"A crescut {prev:.0f} -> {price:.0f} (fara notificare)")
    else:
        log("Pret neschimbat.")

    # prag optional
    if TARGET_PRICE:
        target = _to_float(TARGET_PRICE)
        if target and price <= target and (prev is None or prev > target):
            notify(
                "Pret SALTEA sub prag!",
                f"REVA 160x200 e la {price:.0f} lei, sub pragul tau de {target:.0f} lei.",
            )
            log(f"Sub prag ({target:.0f}) -> alerta trimisa")

    state["price"] = price
    state["updated"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
