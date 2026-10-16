# 27 - US macro statistics

A curated set of 45 official US macroeconomic series (Treasury yields and spreads, inflation, labor,
credit and financial conditions, policy rates and liquidity, growth, housing, the dollar, money
supply) ingested nightly into Parquet and served by four public tools and four REST routes. The
upstream is the St. Louis Fed FRED web service; **no user-facing text names it** (tool
descriptions, caveats, OpenAPI schema names all say "US macro"). Terms and attribution
requirements of the upstream and of third-party series republished by it were deliberately set
aside by the product owner for now (the two corporate-bond spread series are flagged
`copyrighted_third_party` in the catalog so the decision stays visible).

Code map: `schemas/fred_catalog.py` (the catalog), `schemas/capabilities/fred.py` (row models),
`sources/fred.py` (adapter), `dataaccess/fred.py` (gateway), `ingest/jobs/fred.py` (the job),
`ingest/fred_store.py` (dataset names + read side), `tools/us_macro_tools.py` (tools),
`api/routers/market_rest/macro_news.py` (routes).

## 1. Source adapter (`sources/fred.py`)

Ingest-only; the request path never reaches it (same non-router carve-out as
`sources/financecalendar.py`). Every method returns a `SourceResult` and never raises for an
upstream problem.

| Concern | Behaviour |
|---|---|
| Key | `FRED_API_KEY` (`SecretStr`, read with `reveal`), sent as the `api_key` query parameter. Missing key => `HealthStatus.UNAVAILABLE`, detail `FRED API key not configured`, no request made. Never logged; scrubbed from upstream error text. |
| Base URL | `FRED_BASE_URL`, default `https://api.stlouisfed.org/fred`. |
| Rate limit | One process-wide token bucket, 1 request/s sustained (60/min, half the service's 120/min), burst 10. |
| Retry | 429 / 5xx / transport errors: up to 4 attempts, exponential backoff + jitter, honouring `Retry-After`. 4xx other than 429 is not retried. `FredApiError.retryable` carries the decision; degraded results are `UNREACHABLE` (retryable) or `UNAVAILABLE`. |
| Missing values | `"."` (and blanks / non-finite) parse to `None`. |
| Caveat / timing | Every result carries the non-empty `US_MACRO_CAVEAT` (daily series trail a business day, monthly series 2-6 weeks, quarterly 1-3 months, values can be revised) and `TimingClass.EVENT_DRIVEN`. |

Methods: `series_observations` (+ `fetch_all_observations`, paging by `limit`/`offset`),
`series_meta`, `series_release`, `series_metas` (concurrent, best-effort), `release_dates`
(`releases/dates` with `include_release_dates_with_no_data=true`, i.e. future dates),
`release_dates_for`, `release_info`, `series_updates` (+ `recent_series_updates`),
`vintage_dates`, and `bulk_release_observations`.

**Unverified.** Nothing was exercised against the live service (no key was available when this was
written); tests replay recorded-shape payloads using the field names from the v1 documentation
pages. `bulk_release_observations` (v2 `release/observations`, cursor paged) is written from the v2
page alone, whose authentication header is not named there: it sends the key as the `api_key`
query parameter and, if `_V2_KEY_HEADER` is set, also in that header. Nothing on the default path
uses it. `series_updates`' `start_time`/`end_time` time zone is not stated in the docs read, so the
job relies on the default ordering instead. Whether `releases/dates`' `realtime_start/end` bound the
release dates themselves was assumed; the job also filters client-side to its window.

## 2. Ingest job (`job_fred_macro`)

`SCHEDULED_JOBS`: `fred_macro`, **daily 07:40 Taipei, `market=US`** (after `us_price_eod` 06:00,
`us_fundamentals` 07:00, the 07:30 scoring batches), so it runs for the last completed US session
and is gated on the US calendar. Also called once by the dev startup gap-fill (`_us_gap_fill`).
Leader-gated, journalled (one `JobRun` per dataset logged, one summary returned), idempotent.
Without a key it journals `SKIPPED` with `FRED API key not configured` and makes no call.

Datasets (market `US`, Hive-partitioned by run date):

| Dataset | Content | Written |
|---|---|---|
| `fred_observations` | **Full snapshot** of every catalog series under every stored `units` transform: `series_id, units, observation_date, value, realtime_start, realtime_end`. Missing observations are not stored. | Only when some series was re-read (or none existed yet). Reader takes the newest partition: no merge. Older snapshots stay as the revision trail. |
| `fred_series_meta` | One row per series: title, units, frequency, seasonal adjustment, `last_updated`, release id/name, `stored_observations`, `full_refresh_date`, `latest_vintage_date`. | **Every** run, so the newest partition date is "last checked". |
| `fred_release_dates` | Release calendar (45 days back, 150 ahead) for the releases the catalog belongs to: `release_id, release_name, release_date`. | Every run. |
| `fred_vintages` | For revision-tracked series, every value each recent observation held and when (`realtime_start/end`, levels only). | When something changed. |

Refresh policy: the first run (and a full refresh every 7 days) reads the five-year window; later a
series is re-read only if its metadata `last_updated` differs from the stored copy, the
`series/updates` feed lists a different stamp, or it has no stored data, and then only over a
recent window that can still be revised (45 days daily, 90 weekly, 400 monthly, 800 quarterly)
merged into the stored rows. A failed series keeps its stored rows and makes the run `PARTIAL`;
an empty answer never replaces stored rows (`ParquetStore.has_rows`); if every metadata lookup
fails nothing is written and the run is `FAILED`.

## 3. Catalog (`schemas/fred_catalog.py`)

`SERIES_CATALOG` is the only list the job fetches and the tools serve. Per entry: id, group,
`label`, `unit_kind` (percent / level / index / count), `default_units`, `stored_units`,
`panel_units`, `frequency`, `higher_means` key (neutral sentence in `HIGHER_MEANS_TEXT`: "A higher
value means ..."; no good/bad, no red/green, no trading language), `copyrighted_third_party`,
`track_revisions`. CPI / core CPI / PCE / core PCE default to year-over-year (`pc1`) and also show
month-over-month (`pch`); payrolls default to the monthly change (`chg`).

## 4. Tools and routes

All four are `DataClass.PUBLIC`, deferred, read only the Parquet store, and degrade to
`data_available=false` + `unavailable_reason` (never an error) while the store is empty.

| Tool | Route | Input | Output highlights |
|---|---|---|---|
| `get_us_macro_series` | `GET /api/v1/macro/us/series` | `series_id`, `units?`, `frequency?` (`w/m/q/a`, level only, coarser than native), `start_date?`, `end_date?`, `limit` (1-5000, default 500) | `points[{date,value}]`, `metadata`, `total_observations`, `observation_range`, `as_of` |
| `get_us_macro_dashboard` | `GET /api/v1/macro/us/dashboard` | none | `panels[{group,label,series[{latest,prior,change,last_observation_date,next_release_date,sparkline(<=60)}]}]`, `yield_curve[{state,inverted_since,days_inverted}]`, `recession_bands[{start,end,ongoing}]` |
| `get_us_economic_releases` | `GET /api/v1/macro/us/releases` | `from_date?`, `to_date?` (default -14d .. +30d, max 366 days) | `releases[{release_id,release_name,release_date,status,series[{latest,previous,change}]}]`; no consensus - merge with `/market/macro-calendar` by `release_name` |
| `get_series_revisions` | `GET /api/v1/macro/us/revisions` | `series_id` (revision-tracked only) | `rows[{observation_date,first_release_date/value,latest_value,latest_vintage_date,revision,revision_count,vintages}]` |

## 5. Operations

`FRED_API_KEY` blank is a valid configuration (nothing is ingested, tools report "not loaded").
The ingest daemon needs the key; the API process does not. Tests: `tests/sources/test_fred.py`,
`tests/ingest/test_fred_job.py`, `tests/tools/test_us_macro_tools.py`,
`tests/api/test_us_macro_routes.py`, `tests/schemas/test_fred_catalog.py` (fake service in
`tests/fred_support.py`).
