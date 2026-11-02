# Fixtures — real OMIE files, downloaded 2026-11-02 16:51 UTC

Source: `https://www.omie.es/es/file-download?parents=marginalpdbcpt&filename=marginalpdbcpt_YYYYMMDD.1`

| File | Why it is here |
|---|---|
| `marginalpdbcpt_20261029.1` | 96 quarter-hours; **market splitting day** — columns 5 and 6 differ in 24 rows (PT ≠ ES). Used to verify column order: col 5 = Portugal, col 6 = Spain (checked against the "Precio marginal portugués" series on omie.es market results, 2026-10-29). |
| `marginalpdbcpt_20261025.1` | **100 rows** — clock change (DST end), 25-hour day. Parser must not assume 96. |
| `marginalpdbcpt_20261103.1` | Ordinary day (published 2026-11-02 13:52 Madrid time per the OMIE listing). |

Format (verbatim first and last lines of `marginalpdbcpt_20261103.1`):

```
MARGINALPDBCPT;
2026;11;03;1;221.16;221.16;
...
2026;11;03;96;239;239;
*
```

Columns: year; month; day; period (1-based, 15 min, in CET/CEST); price Portugal €/MWh; price Spain €/MWh; trailing `;`. Terminator line `*`.
Run the parser on them: `python3 build.py --selftest`.
