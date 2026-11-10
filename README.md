# Luz Amanhã — luz.bymachines.ai

Static site: tomorrow's hourly wholesale electricity price for Portugal (OMIE day-ahead market) with the cheapest and most
expensive 3-hour windows. Portuguese first, English below (`#en`). Built and maintained by AI agents (agent-team);
responsible person: Dmitriy, bymachines.ai. No JavaScript, no tracking, no forms.

## Build

```
python3 build.py                   # tomorrow (Europe/Lisbon) + the 8 previous days; writes public/
python3 build.py --date 2026-11-03 # a specific day
python3 build.py --offline         # no network, cache only (data/omie/)
python3 build.py --selftest        # parse data/fixtures/, check invariants; no network, no output files
```

Python 3.12, **standard library only** (urllib, json, zoneinfo, html). No pip, no Node, no build step on the server:
the server only publishes the committed `public/` directory (`git pull`). Exit code 0 = index built from tomorrow or
today; 2 = neither available (index still written, says so).

Schedule (server cron, UTC): `13:15`, `13:45`, `14:30` and `16:00` after OMIE publishes, plus `00:05` Europe/Lisbon (rollover: `today.json`/`index.html` move to the new Lisbon day; `today` = the Lisbon calendar date at build time, and the build exits with an error if `today.json.date` differs) and `07:00` UTC for the morning post. A build is

**Note (T-0107):** the 00:05 Lisbon rollover cron is not confirmed to exist; until it does, run `python3 build.py --offline` (or the normal build) by hand after Lisbon midnight, otherwise `today.json` goes stale. `index.html` carries a static PT+EN line with the build date of the "today" data and a link to `/d/<date>/`.
idempotent; running it again only refreshes the "updated" timestamp.

## Offline rebuild

`python3 build.py --offline` (and any build whose download fails) reads `data/omie/<YYYY-MM-DD>.txt` first. If tomorrow's
file is cached, `tomorrow.json`, `/` and `/d/<tomorrow>/` are built from it (`available: true`, same `hours[]` as the online
build). `tomorrow.json` becomes `available: false` only when there is neither a cached nor a downloaded file for tomorrow;
no price is ever carried over or invented. Offline keep-existing: if the cache lacks the file for tomorrow (or today) but the committed `public/tomorrow.json`/`today.json` (or `d/<day>/index.json`) is `available: true` for that same date, offline mode keeps it, leaves `index.html` untouched and logs a warning instead of writing `available: false`; the online build is unchanged. Test: `python3 -m unittest tests.test_offline_keep`. Test: `python3 -m unittest tests.test_offline_build`.

## What to commit

- `build.py`, `config/`, `public/style.css`, `data/fixtures/`, this README — source.
- `data/omie/<YYYY-MM-DD>.txt` — raw OMIE files, one per market day, **committed** (the archive must survive a server loss).
- `public/` — the generated site, **committed** (the server deploys it as-is). Never edit generated files by hand;
  change `build.py` or `config/` and rebuild. `public/style.css` is the only hand-written file in `public/`.

## Layout

| Path | What |
|---|---|
| `build.py` | the whole pipeline: fetch → parse → Lisbon hours → windows → HTML/JSON/RSS. Constants at the top. |
| `config/tariff.json` | owner-set components for the €/kWh **estimate** (see "Rules"). `enabled: false` by default. |
| `data/omie/` | cache of raw OMIE files (`latin-1`, verbatim). |
| `data/fixtures/` | three real files for `--selftest` (split day, 25-hour day, ordinary day) + `README.md` with the format. |
| `public/index.html` | tomorrow (or today when OMIE has not published yet), PT then EN. |
| `public/d/YYYY-MM-DD/index.html`, `index.json` | archive, one page + one JSON per Lisbon day. `public/d/` = index. |
| `public/tomorrow.json`, `today.json` | same JSON as the archive files; `tomorrow.json` carries `available: false` when not published. |
| `public/feed.xml`, `sitemap.xml`, `llms.txt`, `robots.txt`, `404.html`, `build.json` | feeds and metadata. |
| `public/legal/` | responsible person, source, "not advice", CC BY 4.0, link to https://formachines.ai/legal/. |
| `public/go/telegram/` | meta-refresh redirect to the Telegram channel (URL = `TELEGRAM_URL` in `build.py`). Hits on `/go/telegram/` are the click counter; `robots.txt` disallows `/go/`. |

## Data source — verified 2026-11-02 16:51 UTC from this Mac

URL (one GET per day, no account, no key):

```
https://www.omie.es/es/file-download?parents=marginalpdbcpt&filename=marginalpdbcpt_YYYYMMDD.1
```

The bracket form `parents%5B0%5D=` answers 302 to the form above; the form above answers 200
`application/octet-stream`, `content-disposition: attachment; filename=marginalpdbcpt_20261103.1`.
`marginalpdbc_YYYYMMDD.1` (the "España" folder) is byte-identical except for the header line.
A day that is not published yet answers **HTTP 404 with an HTML body** (~27 KB) — the fetcher treats 404 and any
non-OMIE body as "not published" and caches nothing.

Format, verbatim (`marginalpdbcpt_20261103.1`, 98 lines):

```
MARGINALPDBCPT;
2026;11;03;1;221.16;221.16;
2026;11;03;2;214.97;214.97;
...
2026;11;03;96;239;239;
*
```

- Columns: `year;month;day;period;price_PT;price_ES;` — **column 5 is Portugal, column 6 is Spain.** Checked on
  2026-10-29 (a market-splitting day, 24 periods differ): period 69 = `282.01;251.9`, and the omie.es market-results
  page labels the series `282.01, 300, 320, 300, 315, …` as "Precio marginal portugués" and `251.9, 300, 320, 300, 260, …`
  as the Spanish price. On most days both columns are equal (no splitting).
- Rows: **96 quarter-hours** (15-minute market since October 2025). Clock-change days: 100 rows (2026-10-25, DST end) or
  92 rows. Files before October 2025 had 24/23/25 hourly rows — the parser accepts both resolutions and rejects anything else.
- Unit: €/MWh. Negative prices are possible and kept.
- Periods are in **CET/CEST** (MIBEL programming day, `OMIE_PERIODS_TZ = Europe/Madrid`); Lisbon is one hour behind, so
  OMIE period 1 (00:00–00:15 CET) is 23:00 of the previous day in Portugal. `build.py` converts every period to UTC and
  groups by Lisbon hour (`DISPLAY_TZ = Europe/Lisbon`), averaging the four quarter-hours. Consequence: the Lisbon hour
  **23:00 of day D comes from file D+1**, so tomorrow's page shows 23 hours until the next publication; the page says so.
- Publication time, from the omie.es file listing ("Modificado", Madrid time): 23.10 13:58 · 24.10 14:00 · 25.10 13:27 ·
  29.10 14:08 · 30.10 14:13 · 01.11 13:50 · 02.11 13:52. Some days were re-uploaded later (27.10 02:25, 28.10 19:16,
  23.10 23:36) — the `.1` version name did not change; `.2` does not exist for those days (404). The site rebuilds at
  13:15 and 13:45 UTC; a rebuild overwrites the cached file only if the cached one is invalid, so a silent re-upload is
  **not** picked up (open point for the developer: refetch files younger than 48 h and diff).
- Weekends and holidays: files exist every day (checked 24.10–03.11, including Sunday 01.11 and Saturday 31.10).

Reserve sources, not implemented: REN Data Hub (https://datahub.ren.pt/ — answers 200 but the daily-market URLs tried
on 02.11 returned 404; the exact endpoint is unknown) and ENTSO-E Transparency (needs an API key the team does not have).

## Rules (promises in VISION.md, enforced here)

1. **€/MWh is a fact.** It is the OMIE marginal price for Portugal, reproduced unchanged; the hourly value is the mean of
   four quarter-hours. `energy_eur_kwh` = price/1000 is a unit conversion, labelled "energia / energy" — not a bill.
2. **€/kWh with components is an estimate** and is shown only when `config/tariff.json` has `enabled: true` **and** a
   `snapshot_date` and `valid_from`. The owner sets the components (ERSE network access, supplier margin, losses, VAT);
   agents never invent them. Every estimate carries the label "estimativa, não é a sua fatura / estimate, not your bill"
   and the dates. With `enabled: false` the page says the estimate is not configured.
3. **No data for tomorrow → say so and show today.** `index.html` gets the status line "OMIE ainda não publicou…" with the
   last attempt and, when a later build slot exists today, the next attempt. Build slots (UTC): 13:15, 13:45, 14:30,
   16:00 (`SCHEDULE_UTC` in `build.py`); the next attempt is the first slot later than the build time, and after 16:00
   none is named. `tomorrow.json` becomes `{"available": false, …}`. Test: `python3 -m unittest tests.test_status_text`.
4. **AI disclosure on every page** (footer, PT and EN): "Feito por agentes de IA (agent-team) sem edição humana ·
   responsável: Dmitriy, bymachines.ai · atualizado <UTC>". JSON carries a `disclosure` field.
5. **No tracking:** no JS, no cookies, no external requests from the page, no forms. Click counting = server access log
   on `/go/telegram/`.
6. **Addresses never change:** `/`, `/d/YYYY-MM-DD/`, `/tomorrow.json`, `/today.json`, `/feed.xml`, `/llms.txt`,
   `/legal/`, `/go/telegram/`. Adding pages is fine; renaming or removing is not.
7. **Design:** one `public/style.css`, works at 375 px, honours `prefers-color-scheme`. Chart = inline SVG, green = the
   cheapest 3 consecutive hours, red = the most expensive 3 consecutive hours, dashed = no data.

## Conventions for the developer agent

- One task per branch, commit message starts with the task id. Run `python3 build.py --selftest` and a real build
  before reporting; open the generated `public/index.html` and check the acceptance criteria yourself.
- New fixtures go to `data/fixtures/` with a line in its README saying what they prove.
- Anything configurable is a constant at the top of `build.py` or a key in `config/`. No hard-coded strings in the
  rendering functions that an owner might want to change (channel URL, responsible person, windows length).
- Texts live in the `T` dict (`pt` / `en`); keep the numbers identical in both languages (same variables).
- Logging goes to stderr with timestamps; the cron captures it. Errors in one day's file must not stop the others.
- Do not add dependencies, a JS bundle, analytics, or a form. Do not change the address of an existing page.

License: site content CC BY 4.0; data © OMIE.
