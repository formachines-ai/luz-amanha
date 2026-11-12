#!/usr/bin/env python3
"""Luz Amanhã — static site builder. Python 3.12 standard library only.

    python3 build.py                 # fetch tomorrow (and the last 8 days) from OMIE, build public/
    python3 build.py --date 2026-11-03
    python3 build.py --offline       # no network: use data/omie/ cache only
    python3 build.py --selftest      # parse data/fixtures/ and check invariants, no network, no output

Pipeline: fetch (cache data/omie/<date>.txt) → parse (96/92/100 quarter-hours or 24/23/25 hours)
→ aggregate to Lisbon-time hours → cheapest / most expensive windows → HTML + JSON + feed into public/.
Everything configurable is in the CONSTANTS block below or in config/tariff.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------------------- CONSTANTS
ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data" / "omie"
FIXTURES_DIR = ROOT / "data" / "fixtures"
PUBLIC_DIR = ROOT / "public"

SITE_HOST = "luz.bymachines.ai"
SITE_BASE = f"https://{SITE_HOST}"
SITE_NAME = "Luz Amanhã"
RESPONSIBLE = "Dmitriy, bymachines.ai"
DISCLOSURE = f"Made by AI agents (agent-team) without human editing; responsible: {RESPONSIBLE}. License CC BY 4.0."
LEGAL_UPSTREAM = "https://formachines.ai/legal/"
REPO_URL = "https://github.com/formachines-ai/luz-amanha"
TELEGRAM_URL = "https://t.me/luz_amanha"   # real channel (owner created 2026-11-02)

# OMIE day-ahead marginal price files. Verified 2026-11-02 (see README "Data source"):
#   https://www.omie.es/es/file-download?parents=marginalpdbcpt&filename=marginalpdbcpt_YYYYMMDD.1
# `marginalpdbc` (Spain folder) carries the same body with a different header line.
OMIE_PARENT = "marginalpdbcpt"
# Fallback (owner 11.11): OMIE stopped publishing marginalpdbcpt after 20261110 (404 for 11.11 and later) while the
# combined file marginalpdbc is published; its column 5 (index 4) is the Portuguese price — checked on the split days
# 03–04.09 and 05–07.10.2026, where it equals marginalpdbcpt and differs from the Spanish column.
OMIE_PARENTS = (OMIE_PARENT, "marginalpdbc")
OMIE_URL = "https://www.omie.es/es/file-download?parents={parent}&filename={parent}_{ymd}.{version}"
OMIE_VERSION = "1"
OMIE_HEADER_PREFIX = "MARGINALPDBC"     # both MARGINALPDBCPT; and MARGINALPDBC;
OMIE_COL_PT = 4                          # 0-based column index of the Portuguese price
OMIE_COL_ES = 5
OMIE_PERIODS_TZ = ZoneInfo("Europe/Madrid")   # MIBEL day-ahead periods are defined in CET/CEST
DISPLAY_TZ = ZoneInfo("Europe/Lisbon")
HTTP_TIMEOUT_S = 30
USER_AGENT = f"luz-amanha-build/1.0 (+{SITE_BASE}; static site, 1 request per file per day)"
PUBLISH_HINT_LOCAL = "13:00"             # OMIE listing showed 13:27–14:18 Madrid time on 23–31.10.2026
RETRY_HINT_UTC = "13:45"
SCHEDULE_UTC = ("13:15", "13:45", "14:30", "16:00")  # build slots (README rule 3)


def next_slot(built: dt.datetime):
    """First build slot (UTC, "HH:MM") strictly later than the build time; None when no slot is left today."""
    hm = f"{built.astimezone(dt.timezone.utc):%H:%M}" if built.tzinfo else f"{built:%H:%M}"
    return next((x for x in SCHEDULE_UTC if x > hm), None)
MISSING: dict = {}  # filled by build() when tomorrow is missing

WINDOW_HOURS = 3                         # consecutive cheap/expensive window length
HISTORY_DAYS = 7                         # for the 7-day comparison
FETCH_BACK_DAYS = HISTORY_DAYS + 1       # yesterday + 7 days before it
FEED_ITEMS = 14

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
log = logging.getLogger("luz")


# ----------------------------------------------------------------------------- FETCH
class OmieMissing(Exception):
    """The file for that date is not published (HTTP 404 or an HTML body instead of data)."""


FETCH_CODE: dict = {}  # day -> HTTP status of the last real fetch ("error" on network failure)


def status_text(lang: str, t: dict, status: str, today_ok: bool = True) -> str:
    if status != "status_missing":
        return t[status]
    m = MISSING
    d = m.get("date")
    dd = f"{d:%d.%m}" if d else "?"
    la = m.get("last_attempt")
    if la is not None and la.tzinfo:
        la = la.astimezone(dt.timezone.utc)
    nxt = m.get("next_attempt")
    if lang == "pt":
        s = f"Ainda não temos os preços de {dd}."
        if la:
            s += f" Última tentativa de consulta à OMIE: {la:%d.%m} às {la:%H:%M} UTC;"
        if m.get("code") == 404:
            s += " o OMIE ainda não publicou o ficheiro (HTTP 404);"
        if nxt:
            s += f" próxima tentativa: {nxt} UTC;"
        s = s.rstrip(";") + "."
        return s + (" A tabela abaixo é de hoje, não de amanhã." if today_ok else "")
    s = f"We do not have the prices for {dd} yet."
    if la:
        s += f" Last attempt, OMIE request: {la:%d.%m} at {la:%H:%M} UTC;"
    if m.get("code") == 404:
        s += " OMIE has not published the file (HTTP 404);"
    if nxt:
        s += f" next attempt: {nxt} UTC;"
    s = s.rstrip(";") + "."
    return s + (" The hourly table below is for today, not for tomorrow." if today_ok else "")


def rebuilt_text(lang: str) -> str:
    b = MISSING.get("built")
    if b is None:
        return ""
    if b.tzinfo:
        b = b.astimezone(dt.timezone.utc)
    if lang == "pt":
        return f"Página atualizada às {b:%H:%M} UTC ({b:%d.%m})."
    return f"Page rebuilt at {b:%H:%M} UTC ({b:%d.%m})."


def omie_filename(day: dt.date) -> str:
    """Name of the OMIE file actually parsed for `day`: the header line of the cached file says which one it is."""
    parent = OMIE_PARENT
    p = cache_path(day)
    if p.exists():
        first = p.read_text(encoding="latin-1").lstrip().split(";", 1)[0].strip().lower()
        if first == "marginalpdbc":
            parent = "marginalpdbc"
    return f"{parent}_{day:%Y%m%d}.{OMIE_VERSION}"


def cache_path(day: dt.date) -> Path:
    return DATA_DIR / f"{day.isoformat()}.txt"


def looks_like_omie(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return len(lines) >= 3 and lines[0].startswith(OMIE_HEADER_PREFIX) and lines[-1] == "*"


def fetch_day(day: dt.date, offline: bool = False) -> str | None:
    """Return the raw OMIE text for `day`, from cache or network. None if not published yet."""
    p = cache_path(day)
    if p.exists():
        text = p.read_text(encoding="latin-1")
        if looks_like_omie(text):
            log.debug("cache hit %s", p.name)
            return text
        log.warning("cache file %s is not a valid OMIE file, refetching", p.name)
    if offline:
        log.info("offline: no cache for %s", day)
        return None
    text = None
    for parent in OMIE_PARENTS:
        url = OMIE_URL.format(parent=parent, ymd=f"{day:%Y%m%d}", version=OMIE_VERSION)
        fname = f"{parent}_{day:%Y%m%d}.{OMIE_VERSION}"
        log.info("GET %s", url)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                body = r.read()
                ctype = r.headers.get("Content-Type", "")
                status = r.status
        except urllib.error.HTTPError as e:
            FETCH_CODE[day] = e.code
            log.info("OMIE %s -> HTTP %s (not published yet or wrong name)", fname, e.code)
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            FETCH_CODE[day] = "error"
            log.error("network error for %s: %s", day, e)
            return None
        FETCH_CODE[day] = status
        cand = body.decode("latin-1")
        if status != 200 or "text/html" in ctype or not looks_like_omie(cand):
            log.info("OMIE %s -> %s %s, body is not an OMIE file (%d bytes)", fname, status, ctype, len(body))
            continue
        if parent != OMIE_PARENT:
            log.warning("OMIE %s missing; using the combined file %s (Portuguese price column)", omie_filename(day), fname)
        text = cand
        break
    if text is None:
        return None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="latin-1")
    log.info("saved %s (%d bytes, %d data rows)", p, len(text), text.count("\n") - 2)
    return text


# ----------------------------------------------------------------------------- PARSE
def parse_omie(text: str) -> dict:
    """Parse one file. Returns {'date': date, 'periods': [(period_no, pt, es), ...], 'resolution_min': 15|60}.

    Rows: `YYYY;MM;DD;period;pt;es;`. 96 rows = 15-min day, 92/100 = clock-change days; 24/23/25 = hourly files
    (format before October 2025). Any other count raises.
    """
    rows: list[tuple[int, float, float]] = []
    day: dt.date | None = None
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(OMIE_HEADER_PREFIX) or ln == "*":
            continue
        parts = ln.split(";")
        if len(parts) < 6:
            raise ValueError(f"short row: {ln!r}")
        y, m, d, k = (int(parts[i]) for i in range(4))
        pt, es = float(parts[OMIE_COL_PT]), float(parts[OMIE_COL_ES])
        row_day = dt.date(y, m, d)
        day = day or row_day
        if row_day != day:
            raise ValueError(f"mixed dates in file: {day} vs {row_day}")
        rows.append((k, pt, es))
    if day is None:
        raise ValueError("no data rows")
    rows.sort()
    n = len(rows)
    if n in (96, 92, 100):
        res = 15
    elif n in (24, 23, 25):
        res = 60
    else:
        raise ValueError(f"unexpected row count {n} for {day}")
    if [k for k, _, _ in rows] != list(range(1, n + 1)):
        raise ValueError(f"periods are not 1..{n} for {day}")
    for _, pt, es in rows:
        if pt != pt or es != es:  # NaN guard
            raise ValueError("NaN price")
    return {"date": day, "periods": rows, "resolution_min": res}


def period_starts(parsed: dict) -> list[tuple[dt.datetime, float, float]]:
    """Each period's start as an aware datetime in OMIE_PERIODS_TZ, walked in UTC so clock changes are exact."""
    day = parsed["date"]
    start = dt.datetime.combine(day, dt.time(0), tzinfo=OMIE_PERIODS_TZ).astimezone(dt.timezone.utc)
    step = dt.timedelta(minutes=parsed["resolution_min"])
    return [(start + step * (k - 1), pt, es) for k, pt, es in parsed["periods"]]


# ----------------------------------------------------------------------------- HOURS (Lisbon)
def lisbon_hours(day: dt.date, files: dict[dt.date, dict]) -> list[dict]:
    """Hours of the Lisbon calendar day `day`: [{'label','start_utc','pt','es','n'}...] (23/24/25 entries).

    OMIE periods are CET; Lisbon is one hour behind, so the Lisbon hour 23:00 needs the NEXT day's file.
    Missing hours have pt=None.
    """
    pool: dict[dt.datetime, list[tuple[float, float]]] = {}
    for d in (day - dt.timedelta(days=1), day, day + dt.timedelta(days=1)):
        parsed = files.get(d)
        if not parsed:
            continue
        for start_utc, pt, es in period_starts(parsed):
            hour_utc = start_utc.replace(minute=0, second=0, microsecond=0)
            pool.setdefault(hour_utc, []).append((pt, es))
    day_start = dt.datetime.combine(day, dt.time(0), tzinfo=DISPLAY_TZ).astimezone(dt.timezone.utc)
    day_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=DISPLAY_TZ).astimezone(dt.timezone.utc)
    out: list[dict] = []
    seen_labels: dict[str, int] = {}
    t = day_start
    while t < day_end:
        local = t.astimezone(DISPLAY_TZ)
        label = f"{local:%H:%M}"
        seen_labels[label] = seen_labels.get(label, 0) + 1
        if seen_labels[label] > 1:
            label += " (b)"   # repeated hour on the clock-change night
        vals = pool.get(t)
        pt = round(sum(v[0] for v in vals) / len(vals), 2) if vals else None
        es = round(sum(v[1] for v in vals) / len(vals), 2) if vals else None
        out.append({"label": label, "start_utc": t.isoformat(), "pt": pt, "es": es, "n": len(vals) if vals else 0})
        t += dt.timedelta(hours=1)
    return out


def night_stats(hours: list[dict]) -> dict:
    """Mean of the Lisbon hours starting 00..05; None when none is known (never invented)."""
    night = [h["pt"] for h in hours if h["label"][:2] in ("00", "01", "02", "03", "04", "05") and h["pt"] is not None]
    return {"night_avg_00_06": round(sum(night) / len(night), 2) if night else None, "night_hours_00_06": len(night)}


def stats(hours: list[dict]) -> dict:
    """Averages and windows over the hours that have a price. Windows are on consecutive available hours."""
    vals = [(i, h["pt"]) for i, h in enumerate(hours) if h["pt"] is not None]
    if not vals:
        return {"available": 0, "hours_expected": len(hours), "complete": False, **night_stats(hours)}
    prices = [v for _, v in vals]
    avg = round(sum(prices) / len(prices), 2)
    cheapest = sorted(vals, key=lambda x: (x[1], x[0]))[:WINDOW_HOURS]
    dearest = sorted(vals, key=lambda x: (-x[1], x[0]))[:WINDOW_HOURS]

    def best_window(sign: int) -> dict | None:
        best = None
        for s in range(len(hours) - WINDOW_HOURS + 1):
            seg = hours[s:s + WINDOW_HOURS]
            if any(h["pt"] is None for h in seg):
                continue
            m = sum(h["pt"] for h in seg) / WINDOW_HOURS
            if best is None or sign * m < sign * best["avg"]:
                best = {"start": s, "end": s + WINDOW_HOURS, "avg": round(m, 2)}
        return best

    return {
        "available": len(vals), "hours_expected": len(hours), "complete": len(vals) >= len(hours), "avg": avg, "min": min(prices), "max": max(prices),
        "cheap_window": best_window(+1), "dear_window": best_window(-1),
        "cheapest_hours": sorted(i for i, _ in cheapest), "dearest_hours": sorted(i for i, _ in dearest),
        **night_stats(hours),
    }


# ----------------------------------------------------------------------------- TARIFF (estimate)
def load_tariff() -> dict:
    p = CONFIG_DIR / "tariff.json"
    t = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"enabled": False}
    if t.get("enabled") and not (t.get("snapshot_date") and t.get("valid_from")):
        log.warning("tariff.json enabled without snapshot_date/valid_from — estimate disabled")
        t["enabled"] = False
    return t


def estimate_kwh(pt_eur_mwh: float, t: dict) -> float:
    base = pt_eur_mwh / 1000.0 * float(t.get("losses_factor", 1.0))
    adders = float(t.get("network_access_eur_per_kwh", 0)) + float(t.get("supplier_margin_eur_per_kwh", 0)) \
        + float(t.get("other_adders_eur_per_kwh", 0))
    return round((base + adders) * (1.0 + float(t.get("vat", 0))), 4)


# ----------------------------------------------------------------------------- RENDER
def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def fmt(v: float | None, nd: int = 2) -> str:
    if v is None:
        return "—"
    s = f"{v:.{nd}f}"
    return s[1:] if s.startswith("-") and float(s) == 0 else s


def window_text(hours: list[dict], w: dict | None) -> str:
    if not w:
        return "—"
    a = hours[w["start"]]["label"].split()[0]
    b = hours[w["end"] - 1]["label"].split()[0]
    b_end = f"{(int(b[:2]) + 1) % 24:02d}:00"
    return f"{a}–{b_end}"


def svg_chart(hours: list[dict], st: dict) -> str:
    """Inline SVG bar chart, no JS. Green = cheapest consecutive window, red = dearest, grey = no data."""
    n = len(hours)
    w, h, pad_l, pad_b, pad_t = 760, 260, 44, 30, 14
    bw = (w - pad_l - 8) / n
    prices = [x["pt"] for x in hours if x["pt"] is not None]
    lo = min(0.0, min(prices)) if prices else 0.0
    hi = max(prices) if prices else 1.0
    hi = hi if hi > lo else lo + 1
    cw, dw = st.get("cheap_window"), st.get("dear_window")

    def y(v: float) -> float:
        return pad_t + (h - pad_t - pad_b) * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Preço por hora, €/MWh">']
    for g in range(5):
        v = lo + (hi - lo) * g / 4
        parts.append(f'<line class="grid" x1="{pad_l}" y1="{y(v):.1f}" x2="{w - 4}" y2="{y(v):.1f}"/>'
                     f'<text class="ax" x="{pad_l - 4}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.0f}</text>')
    for i, x in enumerate(hours):
        cls = "bar"
        if cw and cw["start"] <= i < cw["end"]:
            cls += " cheap"
        elif dw and dw["start"] <= i < dw["end"]:
            cls += " dear"
        x0 = pad_l + i * bw + 1
        if x["pt"] is None:
            parts.append(f'<rect class="bar nodata" x="{x0:.1f}" y="{pad_t}" width="{bw - 2:.1f}" height="{h - pad_t - pad_b}">'
                         f'<title>{esc(x["label"])}: sem dados / no data</title></rect>')
        else:
            top, base = y(max(x["pt"], 0)), y(min(x["pt"], 0))
            parts.append(f'<rect class="{cls}" x="{x0:.1f}" y="{top:.1f}" width="{bw - 2:.1f}" height="{max(base - top, 1):.1f}">'
                         f'<title>{esc(x["label"])}: {x["pt"]:.2f} €/MWh</title></rect>')
        if i % 3 == 0 or n < 12:
            parts.append(f'<text class="ax" x="{x0 + bw / 2:.1f}" y="{h - 8}" text-anchor="middle">{esc(x["label"][:2])}</text>')
    parts.append("</svg>")
    return "".join(parts)


HOUR23_ROW_NOTE = {"pt": "publicado amanhã pela OMIE", "en": "published by OMIE tomorrow"}


ZERONEG_LABEL = {"pt": "zero/negativo", "en": "zero/negative"}


def zeroneg_line(hours: list[dict], lang: str) -> str:
    hs = [x["label"][:2] for x in hours if x["pt"] is not None and x["pt"] <= 0]
    if not hs:
        return ""
    n, lst = len(hs), ", ".join(hs)
    if lang == "pt":
        txt = f"{n} {'hora' if n == 1 else 'horas'} com preço OMIE ≤ 0 €/MWh: {lst} (preço grossista, não é a sua fatura)."
    else:
        txt = f"{n} {'hour' if n == 1 else 'hours'} with OMIE price ≤ 0 €/MWh: {lst} (wholesale price, not your bill)."
    return f'<p class="note zeroneg-line">{esc(txt)}</p>'


def hours_table(hours: list[dict], st: dict, tariff: dict, lang: str) -> str:
    cw, dw = st.get("cheap_window"), st.get("dear_window")
    head = {"pt": ("Hora de Lisboa / Lisbon time", "€/MWh (OMIE PT)", "€/kWh grossista (energia)", "Estimativa €/kWh"),
            "en": ("Hora de Lisboa / Lisbon time", "€/MWh (OMIE PT)", "€/kWh wholesale (energy)", "Estimate €/kWh")}[lang]
    cols = head[:3] + ((head[3],) if tariff.get("enabled") else ())
    out = ["<table class=\"hours\"><thead><tr>" + "".join(f"<th>{esc(c)}</th>" for c in cols) + "</tr></thead><tbody>"]
    for i, x in enumerate(hours):
        cls = "cheap" if cw and cw["start"] <= i < cw["end"] else "dear" if dw and dw["start"] <= i < dw["end"] else ""
        cells = [esc(x["label"]), fmt(x["pt"]), fmt(None if x["pt"] is None else x["pt"] / 1000, 3)]
        if tariff.get("enabled"):
            cells.append(fmt(None if x["pt"] is None else estimate_kwh(x["pt"], tariff), 3))
        if x["pt"] is None and x["label"] == "23:00":
            ncols = len(cols) - 1
            out.append(f'<tr class="{cls} pending"><td>{cells[0]}</td><td colspan="{ncols}">{esc(HOUR23_ROW_NOTE[lang])}</td></tr>')
            continue
        if x["pt"] is not None and x["pt"] <= 0:
            cells[1] = f'{cells[1]} <strong class="zeroneg">{ZERONEG_LABEL[lang]}</strong>'
            cls = (cls + " zeroneg-row").strip()
        out.append(f'<tr class="{cls}">' + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


T = {
    "pt": {
        "title": "Luz amanhã — preço da eletricidade por hora (OMIE Portugal)",
        "h1": "Luz amanhã",
        "h1_day": "Preço da luz em Portugal",
        "h1_today": "Luz hoje",
        "title_today": "Luz hoje",
        "lead": "Preço grossista da eletricidade em Portugal por hora (mercado diário OMIE) e as horas mais baratas para ligar a máquina, o termoacumulador ou carregar o carro.",
        "for_day": "Preços para",
        "status_ok": "Dados de amanhã publicados pelo OMIE.",
        "status_missing": "",
        "cheap": "Mais barato (3 horas seguidas)", "dear": "Mais caro (3 horas seguidas)",
        "cheapest3": "3 horas mais baratas (horas isoladas, não necessariamente seguidas)", "dearest3": "3 horas mais caras",
        "night": "Noite 00:00–05:59 (6 h, média das horas 00–05)", "night_avg": "média", "night_cheap": "janela mais barata", "night_none": "sem dados",
        "avg": "Média do dia", "avg_part": "Média das horas disponíveis",
        "incomplete": "Dados para {n} de {m} horas; falta {missing} (a OMIE publica amanhã). Janelas e média usam só as horas conhecidas.",
        "tz": "Horas de Portugal continental (Europe/Lisbon). OMIE usa hora de Madrid (CET), uma hora à frente; aqui as horas são de Lisboa.",
        "cmp_basis": "Comparações: médias por hora de Lisboa (horas disponíveis) de cada dia, a mesma base da média acima.",
        "vs_yday": "vs. ontem", "vs_week": "vs. média dos últimos 7 dias",
        "hour23": "A hora 23:00 só fica disponível no dia seguinte (o OMIE publica em hora de Espanha).",
        "fact": "€/MWh é o preço marginal do mercado diário OMIE para Portugal — um facto, sem impostos, redes nem margem do comercializador.",
        "kwh_note": "Preço OMIE ÷ 1000 = €/kWh de energia no mercado; o seu comercializador soma perdas, margem, tarifas de acesso (TAR) e IVA — não é a sua fatura.",
        "est_off": "Preço grossista OMIE, sem impostos, redes nem margem; a sua fatura depende do contrato.",
        "est_on": "Estimativa, não é a sua fatura:",
        "archive": "Arquivo por dia", "json": "JSON", "feed": "RSS",
        "tg": "Receber todos os dias no Telegram",
        "footer": "Feito por agentes de IA (agent-team) sem edição humana · responsável: ",
        "updated": "atualizado",
        "nodata_all": "Sem dados para este dia.",
        "caveat": "Horas mais baratas no preço grossista OMIE. O efeito na sua fatura depende do contrato; num tarifário fixo o preço não muda de hora a hora.",
        "tg_line": "Todos os dias: às 07:00 UTC as horas mais baratas de hoje e por volta das 14:05 UTC as de amanhã (preço OMIE).",
        "when_tomorrow": "Os preços de amanhã costumam aparecer aqui entre as {a} e as {b} UTC (hora de Lisboa no inverno).",
        "rss_note": "RSS: /feed.xml dá um item por dia, com os preços.",
        "en_link": "English below",
    },
    "en": {
        "title": "Luz amanhã — tomorrow's electricity price by hour (OMIE Portugal)",
        "h1": "Electricity price tomorrow — Portugal",
        "h1_day": "Electricity price in Portugal",
        "h1_today": "Electricity price today",
        "lead": "Wholesale hourly electricity price for Portugal (OMIE day-ahead market) and the cheapest hours to run the washing machine, the water heater or charge the car.",
        "for_day": "Prices for",
        "status_ok": "Tomorrow's prices are published by OMIE.",
        "status_missing": "",
        "cheap": "Cheapest (3 consecutive hours)", "dear": "Most expensive (3 consecutive hours)",
        "cheapest3": "3 cheapest single hours, not necessarily consecutive", "dearest3": "3 most expensive hours",
        "night": "Night 00:00–05:59 (6 h, mean of hours 00–05)", "night_avg": "average", "night_cheap": "cheapest window", "night_none": "no data",
        "avg": "Day average", "avg_part": "Average of the available hours",
        "incomplete": "Data for {n} of {m} hours; {missing} is missing (OMIE publishes it tomorrow). Windows and average use only the known hours.",
        "tz": "Hours in mainland Portugal time (Europe/Lisbon). OMIE uses Madrid time (CET), one hour ahead; hours here are Lisbon time.",
        "cmp_basis": "Comparisons: averages of Lisbon hours (available hours) for each day, the same basis as the average above.",
        "vs_yday": "vs. yesterday", "vs_week": "vs. last 7 days average",
        "hour23": "The 23:00 hour becomes available the next day (OMIE publishes in Spanish time).",
        "fact": "€/MWh is the OMIE day-ahead marginal price for Portugal — a fact, before taxes, grid fees and supplier margin.",
        "kwh_note": "OMIE price ÷ 1000 = €/kWh of energy on the market; your supplier adds losses, margin, access tariffs (TAR) and VAT — it is not your bill.",
        "est_off": "OMIE wholesale price, without taxes, networks or margin; your bill depends on your contract.",
        "est_on": "Estimate, not your bill:",
        "archive": "Archive by day", "json": "JSON", "feed": "RSS",
        "tg": "Get it daily on Telegram",
        "footer": "Made by AI agents (agent-team) without human editing · responsible: ",
        "updated": "updated",
        "nodata_all": "No data for this day.",
        "caveat": "Cheapest hours in the OMIE wholesale price. The effect on your bill depends on your contract; on a fixed tariff the price does not change by the hour.",
        "tg_line": "Every day: today's cheapest hours at 07:00 UTC and tomorrow's at about 14:05 UTC (OMIE price).",
        "when_tomorrow": "Tomorrow's prices usually appear here between {a} and {b} UTC (Lisbon time in winter).",
        "rss_note": "RSS: /feed.xml gives one item per day, with the prices.",
        "en_link": "",
    },
}


FEEDBACK = {
    "pt": {"q": "Foi útil?", "up": "útil", "down": "não útil", "report": "Reportar um erro", "issue": "Erro na página"},
    "en": {"q": "Was this useful?", "up": "useful", "down": "not useful", "report": "Report an error", "issue": "Error on page"},
}


def feedback_links(lang: str, day: dt.date) -> str:
    f = FEEDBACK[lang]
    title = urllib.parse.quote(f"{f['issue']} {day.isoformat()}: ")
    body = urllib.parse.quote(f"Page: {SITE_BASE}/d/{day.isoformat()}/\n\n")
    return (f'<p class="feedback">{esc(f["q"])} <a href="/go/vote/{day.isoformat()}/up/" rel="nofollow">{esc(f["up"])}</a> · '
            f'<a href="/go/vote/{day.isoformat()}/down/" rel="nofollow">{esc(f["down"])}</a> · '
            f'<a href="{REPO_URL}/issues/new?title={title}&amp;body={body}" rel="nofollow noopener">{esc(f["report"])}</a></p>')


def vote_pages(day: dt.date, built: dt.datetime) -> None:
    back = f"/d/{day.isoformat()}/"
    for direction in ("up", "down"):
        body = (f'<section lang="pt"><h1>Obrigado</h1><p>Voto registado. <a href="{back}">Voltar à página de {day:%d.%m.%Y}</a></p>'
                f'<p lang="en">Thanks. <a href="{back}">Back to {day.isoformat()}</a></p></section>')
        html_text = page("Voto — " + SITE_NAME, body, built, SITE_BASE + f"/go/vote/{day.isoformat()}/{direction}/").replace(
            '<meta name="viewport"', f'<meta http-equiv="refresh" content="0;url={back}">\n<meta name="robots" content="noindex">\n<meta name="viewport"', 1)
        write(PUBLIC_DIR / "go" / "vote" / day.isoformat() / direction / "index.html", html_text)


def day_section(lang: str, day: dt.date, hours: list[dict], st: dict, cmp: dict, tariff: dict, status: str | None, neutral: bool = False) -> str:
    t = T[lang]
    weekday_pt = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")[day.weekday()]
    labels = {"pt": f"{day:%d.%m.%Y} ({weekday_pt})", "en": f"{day:%A %d %B %Y}"}[lang]
    hd = "h1" if lang == "pt" else "h2"
    today_mode = status == "status_missing" and not neutral
    h1 = t["h1_day"] if neutral else t["h1_today"] if today_mode else t["h1"]
    p = [f'<section id="{lang}" lang="{lang}">', f"<{hd}>{esc(h1)} — {esc(labels)}</{hd}>"]
    if lang == "pt":
        p.append('<p class="note en-jump"><a href="#en" lang="en">English</a></p>')
    if status and today_mode:
        today_ok = bool(st.get("available"))
        p.append(f'<p class="status">{esc(status_text(lang, t, status, today_ok))}</p>')
        if not today_ok:
            p.append(f'<p class="status today-empty">{esc(("Hoje, %s: sem preços OMIE publicados, por isso não há janela mais barata." if lang == "pt" else "Today, %s: no OMIE prices published, so there is no cheapest window.") % f"{day:%d.%m}")}</p>')
        if rebuilt_text(lang):
            p.append(f'<p class="note rebuilt">{esc(rebuilt_text(lang))}</p>')
    p.append(f"<p class=\"lead\">{esc(t['lead'])}</p>")
    if status and not today_mode:
        p.append(f'<p class="status">{esc(status_text(lang, t, status))}</p>')
    p.append(f'<p class="note tg-line">{esc(t["tg_line"])} <a href="/go/telegram/">Telegram</a></p>')
    if not neutral:
        p.append(f'<p class="note when">{esc(t["when_tomorrow"].format(a=SCHEDULE_UTC[0], b=SCHEDULE_UTC[2]))}</p>')
        p.append(f'<p class="note rss-note"><a href="/feed.xml">{esc(t["rss_note"])}</a></p>')
    if not st.get("available"):
        p.append(f"<p>{esc(t['nodata_all'])}</p>" + feedback_links(lang, day) + "</section>")
        return "".join(p)
    ch = [i for i in st["cheapest_hours"]]
    partial = not st.get("complete", True)
    if partial:
        miss = ", ".join(x["label"] for x in hours if x["pt"] is None)
        p.append(f'<p class="note incomplete">{esc(t["incomplete"].format(n=st["available"], m=st["hours_expected"], missing=miss))}</p>')
    p.append(f'<p class="note">{esc(t["tz"])}</p>')
    cwn0 = st["cheap_window"]
    p.append('<div class="windows"><dl class="kpi">')
    nl = ""
    if cwn0 and cwn0["end"] <= 7:
        wt = window_text(hours, cwn0).replace(":00", "") + "h"
        nl = ("A janela mais barata deste dia (%s) é de madrugada; depois dela, veja as horas restantes na tabela." if lang == "pt"
              else "This day's cheapest window (%s) is at night; after it, check the remaining hours in the table.") % wt
        nl = f'<dd class="note night-window">{esc(nl)}</dd>'
    p.append(f"<div><dt>{esc(t['cheap'])}</dt><dd class=\"g\">{esc(window_text(hours, st['cheap_window']))} · {fmt(st['cheap_window']['avg']) if st['cheap_window'] else '—'} €/MWh</dd>{nl}</div>")
    p.append(f"<div><dt>{esc(t['dear'])}</dt><dd class=\"r\">{esc(window_text(hours, st['dear_window']))} · {fmt(st['dear_window']['avg']) if st['dear_window'] else '—'} €/MWh</dd></div>")
    p.append(f"<div><dt>{esc(t['cheapest3'])}</dt><dd>{esc(', '.join(hours[i]['label'] for i in ch))}</dd></div>")
    p.append(f"<div><dt>{esc(t['dearest3'])}</dt><dd>{esc(', '.join(hours[i]['label'] for i in st['dearest_hours']))}</dd></div>")
    na, nn = st.get("night_avg_00_06"), st.get("night_hours_00_06", 0)
    cwn = st["cheap_window"]
    ntxt = (f"{fmt(na)} €/MWh" + ("" if nn == 6 else f" ({nn}/6 h)")) if na is not None else f"{t['night_none']} (0/6 h)"
    ntxt = f"{t['night_avg']} {ntxt}" if na is not None else ntxt
    ncmp = ""
    if cwn:
        wtxt = window_text(hours, cwn)
        inside = int(hours[cwn["end"] - 1]["label"][:2]) <= 5
        if lang == "pt":
            ncmp = f"Para comparação, a janela mais barata entre todas as horas disponíveis é {wtxt}, " + ("dentro da noite." if inside else "fora da noite.")
        else:
            ncmp = f"For comparison, the cheapest window across all available hours is {wtxt}, " + ("inside the night period." if inside else "outside the night period.")
        ncmp = f'<dd class="note night-compare">{esc(ncmp)}</dd>'
    p.append(f"<div class=\"night\"><dt>{esc(t['night'])}</dt><dd>{esc(ntxt)}</dd>{ncmp}</div>")
    yd = cmp.get("vs_yesterday")
    wk = cmp.get("vs_week")
    p.append(f"<div><dt>{esc(t['avg_part'] if partial else t['avg'])}</dt><dd>{fmt(st['avg'])} €/MWh"
             + (f" · {esc(t['vs_yday'])} {yd:+.1f}%" if yd is not None else "")
             + (f" · {esc(t['vs_week'])} {wk:+.1f}%" if wk is not None else "") + "</dd></div>")
    p.append("</dl>")
    p.append(f'<p class="note caveat">{esc(t["caveat"])}</p></div>')
    if yd is not None or wk is not None:
        p.append(f'<p class="note">{esc(t["cmp_basis"])}</p>')
    p.append(svg_chart(hours, st))
    if any(x["pt"] is None for x in hours):
        p.append(f'<p class="note">{esc(t["hour23"])}</p>')
    p.append(f'<p class="note">{esc(t["fact"])}</p>')
    if tariff.get("enabled"):
        p.append(f'<p class="note"><strong>{esc(t["est_on"])}</strong> {esc(tariff.get("label_" + lang, ""))} — '
                 f'{esc(tariff.get("source", ""))}, {esc(tariff.get("valid_from", ""))} (snapshot {esc(tariff.get("snapshot_date", ""))}).</p>')
    else:
        p.append(f'<p class="note">{esc(t["est_off"])}</p>')
    p.append(f'<p class="note kwh-note">{esc(t["kwh_note"])}</p>')
    p.append(zeroneg_line(hours, lang))
    p.append(hours_table(hours, st, tariff, lang))
    p.append(feedback_links(lang, day))
    p.append("</section>")
    return "".join(p)


def page(title: str, body: str, built: dt.datetime, canonical: str, lang_first: str = "pt", desc: str = "", jsonld: dict | None = None, og_title: str = "", og_desc: str = "", hreflang: bool = False) -> str:
    t = T["pt"]
    og_t = og_title or title
    og_d = og_desc or desc or T['en']['lead']
    d_ = desc or T['en']['lead']
    ld = ('\n<script type="application/ld+json">' + json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/") + "</script>") if jsonld else ""
    hl = "".join(f'\n<link rel="alternate" hreflang="{h}" href="{esc(canonical + a)}">' for h, a in (("pt", ""), ("en", "#en"), ("x-default", ""))) if hreflang else ""
    return f"""<!DOCTYPE html>
<html lang="{lang_first}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(d_)}">
<meta property="og:title" content="{esc(og_t)}">
<meta property="og:description" content="{esc(og_d)}">
<meta property="og:url" content="{esc(canonical)}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{esc(og_t)}">
<meta name="twitter:description" content="{esc(og_d)}">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<link rel="canonical" href="{esc(canonical)}">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE_NAME)}" href="{SITE_BASE}/feed.xml">{hl}
<link rel="icon" type="image/svg+xml" href="/brand/avatar.svg">
<link rel="stylesheet" href="/style.css">{ld}
</head>
<body>
<header class="top"><a href="/" class="brand">{esc(SITE_NAME)}</a>
<nav><a href="#en">EN</a> <a href="/d/">{esc(t['archive'])}</a> <a href="/tomorrow.json">{esc(t['json'])}</a> <a href="/feed.xml">{esc(t['feed'])}</a> <a href="/go/telegram/">Telegram</a> <a href="/legal/">Legal</a></nav></header>
<main>
{body}
</main>
<footer><p>{esc(t['footer'])}{esc(RESPONSIBLE)} · {esc(t['updated'])} {built:%Y-%m-%d %H:%M} UTC</p>
<p>{esc(T['en']['footer'])}{esc(RESPONSIBLE)} · {esc(T['en']['updated'])} {built:%Y-%m-%d %H:%M} UTC · <a href="/legal/">legal</a> · <a href="/ha/">Home Assistant</a> · data: <a href="https://www.omie.es/">OMIE</a> · CC BY 4.0</p></footer>
</body>
</html>
"""


def day_json(day: dt.date, hours: list[dict], st: dict, cmp: dict, tariff: dict, built: dt.datetime, source_file: str | None) -> dict:
    return {
        "date": day.isoformat(), "available": bool(st.get("available")), "timezone": "Europe/Lisbon", "unit": "EUR/MWh", "market": "OMIE day-ahead, Portugal",
        "source_file": source_file, "built_utc": built.isoformat(timespec="seconds"),
        "hours": [{"hour": x["label"], "start_utc": x["start_utc"], "price_pt_eur_mwh": x["pt"], "price_es_eur_mwh": x["es"],
                   "energy_eur_kwh": None if x["pt"] is None else round(x["pt"] / 1000, 4) + 0.0,
                   **({"missing_reason": "needs_next_day_omie_file" if x["label"] == "23:00" else "not_in_omie_file"} if x["pt"] is None else {}),
                   **({"estimate_eur_kwh": None if x["pt"] is None else estimate_kwh(x["pt"], tariff)} if tariff.get("enabled") else {})}
                  for x in hours],
        "stats": {k: v for k, v in st.items()},
        "comparison": cmp,
        "cheap_window": {"hours": window_text(hours, st["cheap_window"]), "avg_eur_mwh": st["cheap_window"]["avg"]} if st.get("cheap_window") else None,
        "dear_window": {"hours": window_text(hours, st["dear_window"]), "avg_eur_mwh": st["dear_window"]["avg"]} if st.get("dear_window") else None,
        "estimate": {"enabled": bool(tariff.get("enabled")), "note": "estimate, not a bill", **({k: tariff[k] for k in ("source", "valid_from", "snapshot_date")} if tariff.get("enabled") else {})},
        "disclosure": DISCLOSURE,
    }


# ----------------------------------------------------------------------------- STATIC PAGES
def unavailable_json(day: dt.date, built: dt.datetime, code: int | None = None, next_attempt: object = None, with_note: bool = False) -> dict:
    """JSON shape when a day has no OMIE data: no prices, windows or hours are invented."""
    d: dict = {"date": day.isoformat(), "available": False}
    if with_note:
        at = built.isoformat(timespec="seconds")
        if code is None:
            prev = _last_online_attempt(day)
            if prev:  # --offline rebuild: keep the evidence of the last online fetch
                d.update(note=prev["note"], last_attempt=prev["last_attempt"], last_attempt_http=prev["last_attempt_http"], next_attempt_utc=next_attempt)
                d.update(built_utc=at, timezone="Europe/Lisbon", disclosure=DISCLOSURE)
                return d
            note = "no file for this date at the last attempt"
        else:
            url = OMIE_URL.format(parent=OMIE_PARENT, ymd=day.strftime("%Y%m%d"), version=OMIE_VERSION)
            what = "not published by OMIE" if code == 404 else "OMIE fetch failed"
            note = f"{what} (HTTP {code}) at online fetch {at} UTC: {url}"
        d.update(note=note, last_attempt=at, last_attempt_http=code, next_attempt_utc=next_attempt)
    d.update(built_utc=built.isoformat(timespec="seconds"), timezone="Europe/Lisbon", disclosure=DISCLOSURE)
    return d


def _last_online_attempt(day: dt.date) -> dict | None:
    """note/last_attempt of the last ONLINE fetch, from the committed public/tomorrow.json (same date, unavailable, http status set)."""
    try:
        j = json.loads((PUBLIC_DIR / "tomorrow.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (isinstance(j, dict) and j.get("date") == day.isoformat() and j.get("available") is False
            and j.get("last_attempt_http") is not None and j.get("note") and j.get("last_attempt")):
        return {k: j[k] for k in ("note", "last_attempt", "last_attempt_http")}
    return None


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log.debug("wrote %s (%d bytes)", path.relative_to(ROOT), len(text))


YAML1 = 'rest:\n  - resource: https://luz.bymachines.ai/today.json\n    scan_interval: 1800\n    sensor:\n      - name: "Luz hoje janela barata"\n        value_template: >-\n          {{ value_json.cheap_window.hours\n             if value_json.available and value_json.cheap_window\n             else \'unavailable\' }}\n        json_attributes:\n          - date\n          - available\n          - cheap_window\n          - dear_window\n          - stats\n      - name: "Luz hoje janela cara"\n        value_template: >-\n          {{ value_json.dear_window.hours\n             if value_json.available and value_json.dear_window\n             else \'unavailable\' }}\n      - name: "Luz hoje preco medio"\n        unit_of_measurement: "EUR/MWh"\n        value_template: >-\n          {{ value_json.stats.avg\n             if value_json.available and value_json.stats\n             else \'unavailable\' }}'
YAML2 = 'rest:\n  - resource: https://luz.bymachines.ai/tomorrow.json\n    scan_interval: 1800\n    sensor:\n      - name: "Luz amanha janela barata"\n        value_template: >-\n          {{ value_json.cheap_window.hours\n             if value_json.available and value_json.cheap_window\n             else \'unavailable\' }}\n        json_attributes:\n          - date\n          - available\n          - cheap_window\n          - dear_window\n          - stats\n      - name: "Luz amanha janela cara"\n        value_template: >-\n          {{ value_json.dear_window.hours\n             if value_json.available and value_json.dear_window\n             else \'unavailable\' }}\n      - name: "Luz amanha preco medio"\n        unit_of_measurement: "EUR/MWh"\n        value_template: >-\n          {{ value_json.stats.avg\n             if value_json.available and value_json.stats\n             else \'unavailable\' }}'


def static_pages(built: dt.datetime) -> None:
    legal = f"""<section lang="pt"><h1>Legal</h1>
<p><strong>Responsável:</strong> {esc(RESPONSIBLE)}. Este site é feito e atualizado por agentes de IA (agent-team) sem edição humana; o responsável é uma pessoa.</p>
<p><strong>Fonte dos dados:</strong> OMIE — Operador do Mercado Ibérico de Energia, ficheiros públicos do mercado diário (<code>marginalpdbcpt_AAAAMMDD.1</code> ou, quando este não é publicado, <code>marginalpdbc_AAAAMMDD.1</code>, coluna 5 = Portugal; €/MWh; o ficheiro usado em cada dia está em <code>source_file</code>). Os valores são reproduzidos sem alteração; a agregação por hora é a média das quatro quartos de hora.</p>
<p><strong>Não é aconselhamento financeiro nem tarifário.</strong> O preço OMIE não é a sua fatura: não inclui redes, impostos, taxas nem a margem do comercializador. Qualquer valor marcado «estimativa» é uma aproximação com parcelas datadas e pode estar errado. Confirme sempre com o seu comercializador.</p>
<p><strong>Privacidade:</strong> sem cookies, sem rastreio, sem formulários. O servidor guarda registos de acesso técnicos (endereço IP, user-agent) por um período limitado para contagem de visitas e segurança.</p>
<p><strong>Licença:</strong> textos, gráficos e JSON deste site — <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Os dados de origem pertencem ao OMIE.</p>
<p>Termos completos, contacto e política de dados: <a href="{LEGAL_UPSTREAM}">{LEGAL_UPSTREAM}</a>.</p></section>
<section id="en" lang="en"><h2>Legal (EN)</h2>
<p><strong>Responsible:</strong> {esc(RESPONSIBLE)}. Built and updated by AI agents (agent-team) without human editing; the responsible party is a person.</p>
<p><strong>Data source:</strong> OMIE, the Iberian electricity market operator, public day-ahead files (<code>marginalpdbcpt_YYYYMMDD.1</code> or, when that is not published, <code>marginalpdbc_YYYYMMDD.1</code>, column 5 = Portugal; €/MWh; the file used for each day is in <code>source_file</code>). Values are reproduced unchanged; hourly values are the mean of the four quarter-hours.</p>
<p><strong>Not financial or tariff advice.</strong> The OMIE price is not your bill: it excludes grid fees, taxes, levies and supplier margin. Anything labelled "estimate" is an approximation with dated components and may be wrong. Always check with your supplier.</p>
<p><strong>Privacy:</strong> no cookies, no tracking, no forms. The server keeps technical access logs (IP address, user agent) for a limited time for visit counting and security.</p>
<p><strong>License:</strong> site texts, charts and JSON — CC BY 4.0. Source data belongs to OMIE.</p>
<p>Full terms, contact and data policy: <a href="{LEGAL_UPSTREAM}">{LEGAL_UPSTREAM}</a>.</p></section>"""
    write(PUBLIC_DIR / "legal" / "index.html", page("Legal — " + SITE_NAME, legal, built, SITE_BASE + "/legal/"))

    ha = f"""<section lang="pt"><h1>Home Assistant: janela mais barata e mais cara do dia</h1>
<p>Dois sensores <code>rest</code> prontos a colar que leem <a href="/today.json">today.json</a> e <a href="/tomorrow.json">tomorrow.json</a> e dão a janela de 3 horas seguidas mais barata (<code>cheap_window</code>) e mais cara (<code>dear_window</code>). Preço OMIE em bruto, sem margens.</p>
<p>Projeto independente: não é uma integração oficial do Home Assistant nem do hass_omie, e não têm qualquer apoio deles.</p>
<h2>Hoje (today.json)</h2>
<pre><code>__Y1__</code></pre>
<h2>Amanhã (tomorrow.json)</h2>
<pre><code>__Y2__</code></pre>
<h2>Notas</h2>
<ul>
<li><strong>Quando aparece amanhã:</strong> a OMIE publica por volta das 13:00–14:20 (hora de Madrid); o site é reconstruído às __S__ UTC. Antes disso <code>tomorrow.json</code> tem <code>"available": false</code>, ou seja, ainda não saiu, e os campos das janelas não existem — os sensores ficam <code>unavailable</code>.</li>
<li><strong>scan_interval:</strong> 1800 segundos (30 min) é o mínimo que recomendamos; os dados mudam no máximo algumas vezes por dia.</li>
<li><strong>Campos:</strong> <code>date</code>, <code>available</code>, <code>cheap_window.hours</code> (por exemplo «10:00–13:00», hora de Lisboa), <code>cheap_window.avg_eur_mwh</code>, <code>dear_window.hours</code>, <code>stats.avg</code>, <code>stats.min</code>, <code>stats.max</code>.</li>
<li><strong>Fonte:</strong> OMIE, ficheiro <code>marginalpdbcpt_AAAAMMDD.1</code> ou, quando não existe, <code>marginalpdbc_AAAAMMDD.1</code> (coluna 5 = Portugal). Valores em bruto em €/MWh (hora = média de quatro quartos de hora), sem redes, impostos nem margem do comercializador.</li>
<li>Não é aconselhamento tarifário nem financeiro.</li>
</ul></section>
<section id="en" lang="en"><h2>Home Assistant: cheapest and dearest window of the day</h2>
<p>Two copy-paste <code>rest</code> sensors reading <code>today.json</code> and <code>tomorrow.json</code> (YAML above) for the cheapest (<code>cheap_window</code>) and dearest (<code>dear_window</code>) run of 3 consecutive hours. Raw OMIE price, no markups.</p>
<p>Independent project: not an official Home Assistant or hass_omie integration, and not endorsed by either.</p>
<ul>
<li><strong>When tomorrow appears:</strong> OMIE publishes around 13:00–14:20 Madrid time; the site rebuilds at __S__ UTC. Until then <code>tomorrow.json</code> says <code>"available": false</code> (not out yet) and has no window fields, so the sensors show <code>unavailable</code>.</li>
<li><strong>scan_interval:</strong> 1800 seconds (30 min) is the minimum we ask for; the data changes a few times a day at most.</li>
<li><strong>Source:</strong> OMIE file <code>marginalpdbcpt_YYYYMMDD.1</code> or, when it is not published, <code>marginalpdbc_YYYYMMDD.1</code> (column 5 = Portugal). Raw prices in EUR/MWh (an hour = mean of four quarter-hours), excluding grid fees, taxes and supplier margin. Hours are Lisbon time.</li>
<li>Not tariff or financial advice.</li>
</ul></section>"""
    ha = ha.replace("__Y1__", esc(YAML1)).replace("__Y2__", esc(YAML2)).replace("__S__", ", ".join(SCHEDULE_UTC))
    ha_desc = "Home Assistant REST sensors (YAML) for the cheapest and dearest 3-hour OMIE price window in Portugal, from today.json and tomorrow.json."
    ha_ld = {"@context": "https://schema.org", "@type": "WebPage", "name": "Home Assistant — " + SITE_NAME, "url": SITE_BASE + "/ha/",
             "description": ha_desc, "inLanguage": ["pt", "en"],
             "isPartOf": {"@type": "WebSite", "name": SITE_NAME, "url": SITE_BASE + "/"},
             "publisher": {"@type": "Organization", "name": "bymachines.ai", "url": "https://bymachines.ai/"}}
    write(PUBLIC_DIR / "ha" / "index.html", page("Home Assistant — " + SITE_NAME, ha, built, SITE_BASE + "/ha/", desc=ha_desc, jsonld=ha_ld, hreflang=True))

    brand = f"""<section lang="pt"><h1>Marca / Brand</h1>
<p>Avatar do site e dos canais. Dois desenhos de geometria simples, legíveis num círculo de 48 px, sem texto. Escolhido: <strong>A</strong>.</p>
<p lang="en">Avatar for the site and its channels. Two simple-geometry designs that read in a 48 px circle, no text. Chosen: <strong>A</strong>.</p>
<h2>A — sunrise (escolhido / chosen)</h2>
<p><img src="/brand/avatar-a.svg" width="128" height="128" alt="Avatar A: sun rising over the horizon"> <img src="/brand/avatar-a.svg" width="48" height="48" alt="" style="border-radius:50%"></p>
<h2>B — three price bars</h2>
<p><img src="/brand/avatar-b.svg" width="128" height="128" alt="Avatar B: three bars, green low, red high"> <img src="/brand/avatar-b.svg" width="48" height="48" alt="" style="border-radius:50%"></p>
<h2>Porquê A / Why A</h2>
<p>«Luz amanhã» = o sol a nascer; forma única e contrastada, aguenta o recorte em círculo. B lembra o gráfico, mas as três barras viram ruído a 48 px e verde/vermelho sozinhos são fracos para daltónicos.</p>
<p lang="en">"Luz amanhã" = tomorrow's light: a sunrise is one bold shape that survives the circular crop. B echoes the chart, but three bars turn to noise at 48 px and green/red alone is weak for colour-blind users.</p>
<h2>Ficheiros / Files</h2>
<ul><li><a href="/brand/avatar.svg">/brand/avatar.svg</a> (512×512, escolhido / chosen)</li><li><a href="/brand/avatar-512.png">avatar-512.png</a> (PNG 512×512) · <a href="/brand/avatar-1024.png">avatar-1024.png</a> (PNG 1024×1024) — Telegram, etc.</li><li><a href="/brand/avatar-a.svg">avatar-a.svg</a> · <a href="/brand/avatar-b.svg">avatar-b.svg</a></li></ul>
<h2>Canais / Channels</h2>
<ul><li>Telegram <a href="{esc(TELEGRAM_URL)}">@luz_amanha</a> — a carregar pelo responsável / to be uploaded by the owner</li><li>Favicon — <a href="/">luz.bymachines.ai</a> (já ativo / active)</li></ul></section>"""
    write(PUBLIC_DIR / "brand" / "index.html", page("Brand — " + SITE_NAME, brand, built, SITE_BASE + "/brand/"))

    tg = f"""<section lang="pt"><h1>Telegram</h1>
<p>A redirecionar para o canal… Se não abrir: <a href="{esc(TELEGRAM_URL)}">{esc(TELEGRAM_URL)}</a></p>
<p lang="en">Redirecting to the Telegram channel… If nothing happens: <a href="{esc(TELEGRAM_URL)}">{esc(TELEGRAM_URL)}</a></p></section>"""
    tg_page = page("Telegram — " + SITE_NAME, tg, built, SITE_BASE + "/go/telegram/").replace(
        '<meta name="viewport"', f'<meta http-equiv="refresh" content="1;url={esc(TELEGRAM_URL)}">\n<meta name="robots" content="noindex">\n<meta name="viewport"', 1)
    write(PUBLIC_DIR / "go" / "telegram" / "index.html", tg_page)

    nf = """<section lang="pt"><h1>Página não encontrada (404)</h1>
<p>Esta página não existe. Páginas que existem: <a href="/">preço de amanhã</a> · <a href="/d/">arquivo por dia</a> (<code>/d/AAAA-MM-DD/</code>) · <a href="/tomorrow.json">tomorrow.json</a> · <a href="/today.json">today.json</a> · <a href="/feed.xml">feed</a> · <a href="/legal/">legal</a>.</p>
<p lang="en">Page not found. Existing pages: <a href="/">tomorrow's price</a> · <a href="/d/">archive</a> · <a href="/tomorrow.json">tomorrow.json</a> · <a href="/llms.txt">llms.txt</a>.</p></section>"""
    write(PUBLIC_DIR / "404.html", page("404 — " + SITE_NAME, nf, built, SITE_BASE + "/404.html"))

    write(PUBLIC_DIR / "robots.txt", f"User-agent: *\nAllow: /\nDisallow: /go/\nSitemap: {SITE_BASE}/sitemap.xml\n")
    write(PUBLIC_DIR / "llms.txt", f"""# {SITE_NAME} ({SITE_HOST})

> Tomorrow's hourly wholesale electricity price for Portugal (OMIE day-ahead market, EUR/MWh) with the cheapest and most expensive 3-hour windows. Portuguese and English. Made by AI agents (agent-team) without human editing; responsible: {RESPONSIBLE}. License CC BY 4.0.

## Machine-readable
- {SITE_BASE}/tomorrow.json — tomorrow (Lisbon calendar day), hours in Europe/Lisbon time, price_pt_eur_mwh per hour, cheap_window, dear_window, stats
- {SITE_BASE}/today.json — today
- Field definitions: cheapest_hours = the 3 cheapest single hours of the day (not necessarily consecutive); cheap_window and stats.cheap_window = the cheapest 3 consecutive hours (lowest average). The two can differ.
- missing_reason: present only on an hours[] entry whose price_pt_eur_mwh is null (never on hours with a price). Values: needs_next_day_omie_file = the Lisbon hour 23:00 lies in the next day's OMIE file, not yet published; not_in_omie_file = any other hour absent from the OMIE files we hold. No price is ever estimated for these hours.
- {SITE_BASE}/d/YYYY-MM-DD/index.json — archive, one file per day. /d/YYYY-MM-DD/ and its index.json exist only after OMIE publishes that day (about 13:00 CET); until then tomorrow.json has available:false and the archive URL returns 404.
- When available is false (tomorrow.json every day from 00:00 until OMIE publishes, about 13:15 UTC): present = date, available (false), built_utc, timezone, disclosure, and for tomorrow.json also note, last_attempt, last_attempt_http, next_attempt_utc (today.json has no note). Absent = unit, market, source_file, hours, stats, comparison, cheap_window, dear_window, estimate. No price is carried over or invented. Check available first, then read cheap_window, dear_window and stats only when it is true.
- {SITE_BASE}/feed.xml — RSS, one item per published day
- {SITE_BASE}/build.json — when the site was last built and which OMIE files it used

## Facts about the data
- Source: OMIE public file. The Portugal price comes from marginalpdbcpt_YYYYMMDD.1 or, when that is not published (since 2026-11-11), from column 5 of marginalpdbc_YYYYMMDD.1 (https://www.omie.es/). The file actually used for each day is in source_file of that day's JSON. 96 quarter-hour periods in CET/CEST, averaged per Lisbon hour.
- Hours are Lisbon time. OMIE files use Madrid time (CET), one hour ahead, so the Lisbon 23:00 of day D is the mean of the first 4 quarter-hours of the Portugal column in the file for D+1; null (missing_reason needs_next_day_omie_file) until that file exists, never interpolated.
- OMIE publishes around 13:00–14:20 Madrid time for the next day; the site rebuilds at 13:15, 13:45, 14:30 and 16:00 UTC.
- The Lisbon hour 23:00 of a day needs the next day's file, so for tomorrow it is missing until the day after.
- A price of 0 or below (EUR/MWh) is a real OMIE value, not an error or a broken sensor; such hours are labelled zero/negative on the page.
- EUR/MWh is a fact. Any EUR/kWh "estimate" adds dated regulated components set by the responsible person and is not a bill.

## Pages
- {SITE_BASE}/ — tomorrow (or today if OMIE has not published yet), PT then EN
- {SITE_BASE}/d/ — archive index
- {SITE_BASE}/ha/ — copy-paste Home Assistant REST sensors for the cheapest and dearest window (today.json, tomorrow.json), PT+EN
- {SITE_BASE}/legal/ — responsible party, source, license, no tracking
""")


# ----------------------------------------------------------------------------- BUILD
def comparison(day: dt.date, files: dict[dt.date, dict]) -> dict:
    """Average of day vs. yesterday and vs. the mean of the previous 7 days, all on Lisbon-hour averages (available hours)."""
    def lisbon_avg(d: dt.date) -> float | None:
        if d not in files and d + dt.timedelta(days=1) not in files:
            return None
        return stats(lisbon_hours(d, files)).get("avg")
    def file_avg(d: dt.date) -> float | None:
        f = files.get(d)
        return round(sum(p for _, p, _ in f["periods"]) / len(f["periods"]), 2) if f else None
    today_avg = lisbon_avg(day)
    y_avg = lisbon_avg(day - dt.timedelta(days=1))
    week = [a for a in (lisbon_avg(day - dt.timedelta(days=i)) for i in range(1, HISTORY_DAYS + 1)) if a is not None]
    out = {"day_avg_file": file_avg(day), "yesterday_avg": y_avg, "week_avg": round(sum(week) / len(week), 2) if week else None,
           "week_days": len(week), "vs_yesterday": None, "vs_week": None,
           "basis": "Lisbon-hour averages over available hours (yesterday_avg, week_avg, vs_*); day_avg_file = OMIE file day, Madrid time"}
    if today_avg is not None and y_avg:
        out["vs_yesterday"] = round((today_avg / y_avg - 1) * 100, 1)
    if today_avg is not None and out["week_avg"]:
        out["vs_week"] = round((today_avg / out["week_avg"] - 1) * 100, 1)
    return out


NAV_DAYS: list[dt.date] = []  # every dated page that exists (set in build before rendering); used for prev/next and recent links


def day_nav(day: dt.date) -> str:
    """Plain prev/next links between existing dated pages (PT+EN)."""
    prev = max((d for d in NAV_DAYS if d < day), default=None)
    nxt = min((d for d in NAV_DAYS if d > day), default=None)
    parts = []
    if prev:
        parts.append(f'<a href="/d/{prev.isoformat()}/" rel="prev">← Dia anterior / Previous day: {prev:%d.%m.%Y}</a>')
    if nxt:
        parts.append(f'<a href="/d/{nxt.isoformat()}/" rel="next">Dia seguinte / Next day: {nxt:%d.%m.%Y} →</a>')
    parts.append('<a href="/d/">Arquivo / Archive</a>')
    return '<p class="note daynav">' + " · ".join(parts) + "</p>\n"


def recent_days_html() -> str:
    rec = sorted(NAV_DAYS, reverse=True)[:7]
    links = " · ".join(f'<a href="/d/{d.isoformat()}/">{d:%d.%m.%Y}</a>' for d in rec)
    return (f'\n<p class="note recent-days">Últimos dias / Recent days: {links} · <a href="/d/">Arquivo completo / Full archive</a></p>')


def render_day(day: dt.date, files: dict, tariff: dict, built: dt.datetime, status: str | None, index_page: bool) -> dict:
    hours = lisbon_hours(day, files)
    st = stats(hours)
    cmp = comparison(day, files)
    src = omie_filename(day) if day in files else None
    body = day_section("pt", day, hours, st, cmp, tariff, status) + day_section("en", day, hours, st, cmp, tariff, status)
    day_body = day_section("pt", day, hours, st, cmp, tariff, None, True) + day_section("en", day, hours, st, cmp, tariff, None, True)
    title = f"{SITE_NAME} — {day:%d.%m.%Y}"
    if status == "status_missing":  # index shows today: no 'amanhã' anywhere in title/og:title
        title = f"{T['pt']['title_today']} — {day:%d.%m.%Y}"
    canonical = SITE_BASE + "/" if index_page else f"{SITE_BASE}/d/{day.isoformat()}/"
    desc = ""
    if st.get("available"):
        desc = (f"Luz {day:%d.%m}: mais barato {window_text(hours, st['cheap_window'])} ({fmt(st['cheap_window']['avg'])} €/MWh), "
                f"mais caro {window_text(hours, st['dear_window'])} ({fmt(st['dear_window']['avg'])} €/MWh), média {fmt(st['avg'])} €/MWh.")
    og_title = og_desc = ""
    if st.get("available") and st.get("cheap_window"):
        cw = st["cheap_window"]
        og_title = f"Luz {day:%d.%m} — preço OMIE por hora (Portugal)"
        n = f" (dados para {st['available']} de {st['hours_expected']} horas)" if not st["complete"] else ""
        n_en = f" (data for {st['available']} of {st['hours_expected']} hours)" if not st["complete"] else ""
        og_desc = (f"Janela de 3 horas mais barata: {window_text(hours, cw)}, média {fmt(cw['avg'])} €/MWh (OMIE grossista){n}. "
                   f"EN: cheapest 3-hour window {window_text(hours, cw)}, average {fmt(cw['avg'])} €/MWh (OMIE wholesale){n_en}.")
    site_ld = {"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME, "url": SITE_BASE + "/",
               "inLanguage": ["pt", "en"], "description": T["en"]["lead"],
               "publisher": {"@type": "Organization", "name": "bymachines.ai", "url": "https://bymachines.ai/"}}
    day_ld = {"@context": "https://schema.org", "@type": "Dataset", "name": f"{SITE_NAME} — OMIE Portugal day-ahead price {day.isoformat()}",
              "description": desc or T["en"]["lead"], "temporalCoverage": day.isoformat(), "dateModified": built.date().isoformat(),
              "url": f"{SITE_BASE}/d/{day.isoformat()}/", "license": "https://creativecommons.org/licenses/by/4.0/",
              "inLanguage": ["pt", "en"],
              "creator": {"@type": "Organization", "name": "bymachines.ai", "url": "https://bymachines.ai/"},
              "publisher": {"@type": "Organization", "name": "bymachines.ai", "url": "https://bymachines.ai/"},
              "isBasedOn": {"@type": "Dataset", "name": "OMIE marginalpdbcpt / marginalpdbc day-ahead market results", "url": "https://www.omie.es/"},
              "distribution": {"@type": "DataDownload", "encodingFormat": "application/json",
                               "contentUrl": f"{SITE_BASE}/d/{day.isoformat()}/index.json"}}
    html_text = page(title, day_nav(day) + day_body, built, f"{SITE_BASE}/d/{day.isoformat()}/", desc=desc, jsonld=day_ld, og_title=og_title, og_desc=og_desc)
    data = day_json(day, hours, st, cmp, tariff, built, src)
    write(PUBLIC_DIR / "d" / day.isoformat() / "index.html", html_text)
    vote_pages(day, built)
    write(PUBLIC_DIR / "d" / day.isoformat() / "index.json", json.dumps(data, ensure_ascii=False, indent=1))
    if index_page:
        bd, td = built.date().isoformat(), built.astimezone(DISPLAY_TZ).date().isoformat()
        body += (f'\n<p class="note stale-note">Dados de hoje gerados em {bd}; se esta data não for hoje, veja <a href="/d/{td}/">/d/{td}/</a>. '
                 f'<br>Today\'s data built on {bd}; if this is not today, see <a href="/d/{td}/">/d/{td}/</a>.</p>')
        body += recent_days_html()
        write(PUBLIC_DIR / "index.html", page(title, body, built, SITE_BASE + "/", desc=desc, jsonld=site_ld, og_title=og_title, og_desc=og_desc))
    return data


def archive_index(days: list[dt.date], per_day: dict[dt.date, dict], built: dt.datetime) -> None:
    """One summary row per day; every value is read from the same dict that is written to /d/YYYY-MM-DD/index.json."""
    rows = []
    wins: list[str] = []  # cheap window of every row with data, newest first
    for d in sorted(days, reverse=True):
        iso = d.isoformat()
        j = per_day.get(d) or {}
        st = j.get("stats") or {}
        cw = j.get("cheap_window")
        if st.get("available"):
            wins.append(cw["hours"] if cw else "")
            mh = next((h["hour"] for h in j["hours"] if h["price_pt_eur_mwh"] == st["min"]), "")
            cheap = f'{esc(cw["hours"])} · {cw["avg_eur_mwh"]:.2f}' if cw else "–"
            dw = j.get("dear_window")
            dear = f'{esc(dw["hours"])} · {dw["avg_eur_mwh"]:.2f}' if dw else "–"
            davg = f'{st["avg"]:.2f}'
            mn = f'{st["min"]:.2f} ({esc(mh)})'
            known = "24/24" if st["complete"] and st["available"] == 24 else f'{st["available"]}/24'
            if st["hours_expected"] != 24 and st["complete"]:
                known = f'{st["available"]}/{st["hours_expected"]}'
        else:
            cheap = dear = davg = mn = known = "–"
        rows.append(f'<tr><th scope="row"><a href="/d/{iso}/">{d:%d.%m.%Y}</a></th><td>{cheap}</td><td>{dear}</td><td>{davg}</td><td>{mn}</td><td>{known}</td>'
                    f'<td><a href="/d/{iso}/index.json">json</a></td></tr>')
    summary = ""
    counts = {w: wins.count(w) for w in wins if w}
    if counts:
        top = max(counts.values())
        best = next(w for w in wins if counts.get(w) == top)  # ties: the most recent day's window
        m = len(wins)
        summary = (f'<p><strong>Janela mais barata mais frequente: {esc(best)} em {top} dos últimos {m} dias.</strong> '
                   f'<span lang="en">Most frequent cheapest window: {esc(best)} on {top} of the last {m} days.</span></p>')
    body = ('<section lang="pt"><h1>Arquivo por dia / Archive</h1>'
            '<p>Preço grossista OMIE (Portugal), €/MWh. Janela barata = 3 horas seguidas com a média mais baixa; janela cara = 3 horas seguidas com a média mais alta; média do dia = média das horas disponíveis; mínimo = hora mais barata; horas conhecidas = N/24. '
            '<span id="en" lang="en">OMIE wholesale price, €/MWh. Dear window = most expensive 3 consecutive hours (average); day average = mean of the available hours. Cheap window = cheapest 3 consecutive hours (average); minimum = cheapest single hour; known hours = N/24.</span></p>'
            + summary +
            '<table class="archive"><thead><tr><th scope="col">Dia / Day</th><th scope="col">Janela barata · média €/MWh<br><span lang="en">Cheap window · average €/MWh</span></th>'
            '<th scope="col">Janela cara · média €/MWh<br><span lang="en">Dear window · average €/MWh</span></th><th scope="col">Média do dia €/MWh<br><span lang="en">Day average €/MWh</span></th>'
            '<th scope="col">Mínimo €/MWh (hora)<br><span lang="en">Minimum €/MWh (hour)</span></th><th scope="col">Horas / Hours</th><th scope="col">JSON</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></section>')
    write(PUBLIC_DIR / "d" / "index.html", page("Arquivo — " + SITE_NAME, body, built, SITE_BASE + "/d/"))


def feed(days: list[dt.date], per_day: dict[dt.date, dict], built: dt.datetime) -> None:
    items = []
    for d in sorted(days, reverse=True)[:FEED_ITEMS]:
        j = per_day.get(d)
        if not j or not j["stats"].get("available"):
            continue
        s = j["stats"]
        txt = (f"Luz {d:%d.%m}: mais barato {j['cheap_window']['hours']} ({j['cheap_window']['avg_eur_mwh']:.2f} €/MWh), "
               f"mais caro {j['dear_window']['hours']} ({j['dear_window']['avg_eur_mwh']:.2f} €/MWh), média {s['avg']:.2f} €/MWh. "
               f"Cheapest {j['cheap_window']['hours']}, dearest {j['dear_window']['hours']}. Publicado por um agente de IA; responsável: {RESPONSIBLE}.")
        pub = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(13, 30), tzinfo=dt.timezone.utc)
        title = f"Luz {d:%d.%m.%Y}: mais barato " + j["cheap_window"]["hours"]
        items.append(f"<item><title>{esc(title)}</title>"
                     f"<link>{SITE_BASE}/d/{d.isoformat()}/</link><guid>{SITE_BASE}/d/{d.isoformat()}/</guid>"
                     f"<pubDate>{pub:%a, %d %b %Y %H:%M:%S} +0000</pubDate><description>{esc(txt)}</description></item>")
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{esc(SITE_NAME)}</title><link>{SITE_BASE}/</link>'
           f"<description>{esc(T['en']['lead'])} Made by AI agents; responsible: {esc(RESPONSIBLE)}.</description><language>pt</language>"
           f"<lastBuildDate>{built:%a, %d %b %Y %H:%M:%S} +0000</lastBuildDate>{''.join(items)}</channel></rss>")
    write(PUBLIC_DIR / "feed.xml", xml)
    urls = [SITE_BASE + "/", SITE_BASE + "/d/", SITE_BASE + "/ha/", SITE_BASE + "/legal/", SITE_BASE + "/brand/"] + [f"{SITE_BASE}/d/{d.isoformat()}/" for d in sorted(set(days) | set(NAV_DAYS))]
    write(PUBLIC_DIR / "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          + "".join(f"<url><loc>{u}</loc><lastmod>{built:%Y-%m-%d}</lastmod></url>" for u in urls) + "</urlset>")


def existing_available(day: dt.date, *names: str) -> dict | None:
    """Offline keep-existing: already published JSON for exactly `day` with available:true, else None."""
    for n in names:
        try:
            j = json.loads((PUBLIC_DIR / n).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(j, dict) and j.get("date") == day.isoformat() and j.get("available") is True and j.get("hours"):
            return j
    return None


def check_rollover(today: dt.date) -> None:
    """Self-check: public/today.json must carry the Lisbon date at build time, else fail loudly (T-0102)."""
    got = json.loads((PUBLIC_DIR / "today.json").read_text(encoding="utf-8")).get("date")
    if got != today.isoformat():
        raise SystemExit(f"FATAL: public/today.json date {got} != Lisbon date {today.isoformat()} at build time")


def build(target: dt.date, offline: bool, now: dt.datetime | None = None) -> int:
    """`now` (aware datetime) is the build time; default = the clock. today = Europe/Lisbon calendar date of `now`."""
    built = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(microsecond=0)
    today = built.astimezone(DISPLAY_TZ).date()
    tariff = load_tariff()
    log.info("build start target=%s today(Lisbon)=%s offline=%s tariff_enabled=%s", target, today, offline, tariff.get("enabled"))

    files: dict[dt.date, dict] = {}
    wanted = [target + dt.timedelta(days=1), target] + [target - dt.timedelta(days=i) for i in range(1, FETCH_BACK_DAYS + 1)]
    for d in wanted:
        text = fetch_day(d, offline=offline)
        if text:
            try:
                files[d] = parse_omie(text)
            except ValueError as e:
                log.error("parse failed for %s: %s", d, e)
    # also every cached day (archive pages are regenerated from cache; cheap)
    for p in sorted(DATA_DIR.glob("*.txt")):
        d = dt.date.fromisoformat(p.stem)
        if d not in files:
            try:
                files[d] = parse_omie(p.read_text(encoding="latin-1"))
            except ValueError as e:
                log.error("parse failed for cached %s: %s", p.name, e)
    log.info("files available: %s", ", ".join(d.isoformat() for d in sorted(files)))

    have_target = target in files
    kept: dict[dt.date, dict] = {}  # offline only: published days the cache lacks; never written over with available:false
    if offline:
        for d, name in ((target, "tomorrow.json"), (today, "today.json")):
            if d not in files and d not in kept:
                j = existing_available(d, name, f"d/{d.isoformat()}/index.json")
                if j:
                    kept[d] = j
                    log.warning("offline: no cached OMIE file for %s; keeping existing available %s", d, name)
    main_day = target if have_target else today
    status = "status_ok" if have_target else "status_missing"
    if not have_target:
        nxt = next_slot(built)
        code = FETCH_CODE.get(target)
        prev = _last_online_attempt(target) if code is None else None
        la = built if code is not None else (dt.datetime.fromisoformat(prev["last_attempt"]) if prev else built)  # same value unavailable_json writes
        MISSING.update(date=target, last_attempt=la, built=built, next_attempt=nxt, code=code if code is not None else (prev or {}).get("last_attempt_http"))
    if not have_target and today not in files:
        log.error("neither %s nor today %s available — index will say so", target, today)
    per_day: dict[dt.date, dict] = {}
    on_disk = set()
    for p in (PUBLIC_DIR / "d").glob("????-??-??/index.html"):
        try:
            on_disk.add(dt.date.fromisoformat(p.parent.name))
        except ValueError:
            pass
    NAV_DAYS[:] = sorted(set(files) | set(kept) | on_disk | {main_day})
    for d in sorted(files):
        per_day[d] = render_day(d, files, tariff, built, None, index_page=False)
    keep_index = target in kept  # keep the published index.html (it shows the target day)
    main = render_day(main_day, files, tariff, built, status, index_page=not keep_index)
    per_day[main_day] = main
    tomorrow = per_day.get(target) or kept.get(target) or unavailable_json(target, built, FETCH_CODE.get(target), MISSING.get("next_attempt"), with_note=True)
    write(PUBLIC_DIR / "tomorrow.json", json.dumps(tomorrow, ensure_ascii=False, indent=1))
    write(PUBLIC_DIR / "today.json", json.dumps(per_day.get(today) or kept.get(today) or unavailable_json(today, built), ensure_ascii=False, indent=1))
    check_rollover(today)
    per_day.update({d: j for d, j in kept.items() if d not in per_day})
    all_days = sorted(set(files) | set(kept))
    archive_index(all_days, per_day, built)
    feed(all_days, per_day, built)
    static_pages(built)
    write(PUBLIC_DIR / "build.json", json.dumps({
        "built_utc": built.isoformat(timespec="seconds"), "target_date": target.isoformat(), "target_available": have_target,
        "index_shows": main_day.isoformat(), "files": {d.isoformat(): omie_filename(d) for d in sorted(files)},
        "source": OMIE_URL.format(parent=OMIE_PARENT, ymd="YYYYMMDD", version=OMIE_VERSION), "tariff_enabled": bool(tariff.get("enabled")),
        "builder": "build.py (python3 stdlib)",
    }, ensure_ascii=False, indent=1))
    log.info("build done: index shows %s (%s), %d archive days, tariff_enabled=%s", main_day, status, len(files), tariff.get("enabled"))
    return 0 if (have_target or today in files) else 2


# ----------------------------------------------------------------------------- SELFTEST
def selftest() -> int:
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        log.log(logging.INFO if cond else logging.ERROR, "%s %s", "PASS" if cond else "FAIL", msg)
        ok = ok and cond

    fx = {p.name: parse_omie(p.read_text(encoding="latin-1")) for p in sorted(FIXTURES_DIR.glob("marginalpdbcpt_*.1"))}
    f29 = fx["marginalpdbcpt_20261029.1"]
    check(len(f29["periods"]) == 96 and f29["resolution_min"] == 15, "20261029: 96 quarter-hours")
    check(sum(1 for _, pt, es in f29["periods"] if pt != es) == 24, "20261029: 24 split periods (PT != ES)")
    p69 = f29["periods"][68]
    check(p69 == (69, 282.01, 251.9), f"20261029 period 69 = PT 282.01 / ES 251.9 (got {p69})")
    f25 = fx["marginalpdbcpt_20261025.1"]
    check(len(f25["periods"]) == 100, "20261025: 100 rows (25-hour day)")
    hrs25 = lisbon_hours(dt.date(2026, 10, 25), {dt.date(2026, 10, 25): f25})
    check(len(hrs25) == 25, f"20261025: 25 Lisbon hours (got {len(hrs25)})")
    check(sum(1 for h in hrs25 if "(b)" in h["label"]) == 1, "20261025: one repeated hour label")
    f03 = fx["marginalpdbcpt_20261103.1"]
    hrs = lisbon_hours(dt.date(2026, 11, 3), {dt.date(2026, 11, 3): f03})
    check(len(hrs) == 24 and hrs[0]["label"] == "00:00", "20261103: 24 Lisbon hours starting 00:00")
    check(hrs[23]["pt"] is None and hrs[22]["pt"] is not None, "20261103: hour 23 missing without next-day file, hour 22 present")
    check(hrs[0]["pt"] == round(sum(p for _, p, _ in f03["periods"][4:8]) / 4, 2), "20261103: Lisbon 00:00 = mean of OMIE periods 5..8 (CET 01:00)")
    st = stats(hrs)
    check(st["available"] == 23 and st["cheap_window"] is not None and st["dear_window"] is not None, "stats: windows computed on 23 hours")
    cw = st["cheap_window"]
    check(all(hrs[i]["pt"] is not None for i in range(cw["start"], cw["end"])), "cheap window has no gaps")
    check(sum(1 for p in [h["pt"] for h in hrs] if p is None) == 1, "exactly one missing hour")
    bad = "MARGINALPDBCPT;\n2026;11;03;1;1;1;\n*\n"
    try:
        parse_omie(bad); check(False, "row-count guard")
    except ValueError:
        check(True, "row-count guard rejects 1-row file")
    check(not looks_like_omie("<!DOCTYPE html><html>404</html>"), "HTML body is not an OMIE file")
    log.info("selftest %s", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="target day YYYY-MM-DD (default: tomorrow in Europe/Lisbon)")
    ap.add_argument("--offline", action="store_true", help="never touch the network; use data/omie/ cache")
    ap.add_argument("--selftest", action="store_true", help="parse fixtures, check invariants, exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format=LOG_FORMAT, stream=sys.stderr)
    if a.selftest:
        return selftest()
    target = dt.date.fromisoformat(a.date) if a.date else dt.datetime.now(DISPLAY_TZ).date() + dt.timedelta(days=1)  # Lisbon date at build time
    return build(target, offline=a.offline)


if __name__ == "__main__":
    sys.exit(main())
