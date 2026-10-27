# Data sources

**Build-environment fact:** outbound access to nseindia.com and Yahoo Finance was refused by the
environment's network policy, so **no real market data was used anywhere in this repository**. All
measured results are on the clearly-labelled synthetic generator (`synthetic_market`, source string
`SYNTHETIC(...)`, reports carry a banner).

## Candidate sources (assessment from author knowledge — verify before use)
| Source | Coverage | Corporate actions | Delisted names | Licence / terms | Cost | Suitability |
|---|---|---|---|---|---|---|
| NSE bhavcopy archives (daily EOD files; legacy CSV and the newer UDiFF layout) | All NSE-listed securities per day, incl. ones later delisted | Not adjusted; NSE publishes corporate-action announcements separately | Yes (files are point-in-time) | NSE website terms; automated scraping may be restricted — read terms | Free | **Best free foundation**: survivorship-free by construction; requires building the action table |
| NSE corporate-action / announcement data | Splits, bonuses, dividends | Source of truth | – | NSE terms | Free | Needed to adjust bhavcopy |
| Broker historical-data APIs | Typically adjusted candles for current listings | Vendor-adjusted | Usually not | Broker terms; may need paid API plan | Varies (possibly monthly fee) | Convenient, but survivorship-biased and possibly costly |
| Yahoo Finance via `yfinance` (unofficial) | Many NSE tickers (`.NS`) | Adjusted close provided; quality varies | No | Unofficial; ToS restrictions | Free | **Research convenience only**, never production; known gaps/errors |
| Licensed vendors (e.g. exchange data vendors) | Full, curated | Yes | Yes | Paid licence | ₹₹ | Correct answer if research ever justifies spending |
| Nifty indices total-return series (NSE Indices) | Index TRI | n/a | n/a | Terms apply | Free download | Benchmark (`benchmark.csv`) |

## Ingestion path implemented
* `read_bhavcopy(path)` — parses legacy (`SYMBOL, SERIES, OPEN, … TOTTRDQTY, TIMESTAMP`) and UDiFF
  (`TckrSymb, SctySrs, OpnPric, … TtlTradgVol, TradDt`) layouts, keeps `EQ` series by default.
  Column names are UNVERIFIED against a fresh file (A-D3); an unknown layout raises.
* `read_bars_csv`, `read_actions_csv`, `load_directory(dir)` — canonical long CSVs:
  `bars.csv` (date,symbol,open,high,low,close,volume), `actions.csv` (symbol,ex_date,kind,value with
  kind ∈ {split, dividend}; split value = share multiplier, e.g. 1:1 bonus → 2.0), optional
  `benchmark.csv` (date,close).
* `validate()` — see checks below. Run `python -m omega validate --data DIR`; exit code 2 on errors.

## Canonical model
`MarketData` = RAW `PriceFrame` + ADJUSTED `PriceFrame` + actions + optional benchmark + source.
Missing (date, symbol) = NaN = not tradable. `version()` is a SHA-256 over canonicalised values
(ISO dates, values rounded to 1e-6), stable across CSV/Parquet (`test_csv_round_trip_preserves_dataset_version`).
Every backtest result and registry record stores it.

## Validation checks (`omega/data/validation.py`)
ERROR: schema, duplicate bars (exact vs conflicting), non-monotonic dates, future-dated bars,
partial bars, OHLC inconsistency, non-positive prices, negative volume, |raw return| > 35% without a
corporate action (likely unrecorded split), invalid actions, gaps > 10 sessions inside a listing.
WARN: weekend sessions (NSE holds special sessions), stale flat zero-volume runs, short gaps,
series ending early (possible delisting/suspension), actions for unknown symbols.

## Downloader (`omega/data/nse_archive.py`)
`python -m omega fetch-nse --start 2010-01-01 --cache data_store/bhavcopy` then
`python -m omega build-bars --cache data_store/bhavcopy --out data_store/nse`.
Immutable per-day cache, ≥2 s between requests (default), weekday 404s remembered as probable holidays,
non-zip responses (e.g. a block page) raise. URL patterns for the legacy and UDiFF archives are
**UNVERIFIED** (A-D7) and the module was only tested with a fake fetcher. Read NSE's terms of use first.

## Symbol continuity (ISIN)
`build-bars` gives each ISIN the latest ticker it traded under, so a rename is not mistaken for a
delisting plus a new listing. If a ticker was later reused by a different ISIN, the older security is
suffixed `~<ISIN>`; two securities are never merged. A change of ISIN (some mergers/schemes) is not bridged.

## Corporate actions
`python -m omega parse-actions --input <NSE corporate-actions CSV> --out data_store/nse/actions.csv`
parses the PURPOSE text: dividends (summed when several), bonus a:b → multiplier (a+b)/b, face-value
split/sub-division F1→F2 → F1/F2, consolidation → multiplier < 1. Rights issues, demergers, schemes,
buy-backs and unrecognised text are **written to `actions_unparsed.csv` for manual review, never
guessed**. Column names and phrasings are UNVERIFIED (A-D8). `validate` then cross-checks: a recorded
split whose ratio is not visible in raw prices raises `split_not_visible`; a large raw move without a
recorded action raises `unexplained_jump`.

## Known gaps / next steps
* Rights issues and demergers are not modelled (positions holding them will show unexplained moves).
* Exchange holiday calendar is currently inferred from the union of bar dates.
