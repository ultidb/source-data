# USAU golden HTML fixtures

`usau/tournament/` and `usau/calendar/` hold real, saved USAU pages plus the
JSON each is expected to parse to, per MULTI-SOURCE-REDESIGN.md's testing
strategy item 5 ("golden-fixture harness"). Consumed by
`tests/test_usau_fixtures.py`.

## What's here

- `usau/tournament/tournament.html` + `usau/tournament/teams/*.html` — the
  2026 Commonwealth Cup Weekend 1 College Women tournament (one pool stage,
  one bracket stage, 9 real teams with rosters). Captured from
  `cache/2026/Commonwealth-Cup-2026-Weekend-1CollegeWomen/` during Phase 3.
  `golden_document.json` is `tournament_to_document(...)` output for these
  pages with a fixed `scrapedAt`.
- `usau/calendar/college_schedule.html` + `club_schedule.html` — the
  "new-schedule" shape `parseNewSchedule` reads (the one `UsauSource.discover`
  actually uses; see `sources/usau/source.py`). `golden_calendar.json` is the
  combined `parseNewSchedule` output for both.

All pages are trimmed: `<script>`, `<style>`, HTML comments, and everything
outside `<body>` are stripped, since the parsers here never touch any of
that. This cut total fixture size roughly in half. Trimming was verified
byte-for-byte behavior-preserving before checking these files in (identical
`Document`/page-link output between the original and trimmed HTML, modulo
the non-deterministic `scrapedAt` timestamp) — do the same for any new
fixture: parse both the original and the trimmed version and diff the
output before deleting the untrimmed copy.

## Known gaps

- **No clusters-stage tournament.** No cached example exists, and fetching
  one requires the live Selenium+Tor stack (`core.fetch.SeleniumTorTransport`
  -> `scrape.py`'s singleton Chrome driver), which needs `chromedriver`
  installed -- not set up in the environment this was built in. To fill this
  in: install chromedriver, then `cli.py scrape tournament <url> -y <year>`
  against a historical clusters-stage tournament (many exist across
  `csv/2014/` .. `csv/2025/` — search for `^clusters,` in those CSVs to find
  one), then run the same trim + golden-JSON generation procedure below.
- **No legacy SeasonId-calendar fixture** (the shape `parseTournamentCalendar`
  reads, e.g. `play.usaultimate.org/events/tournament/?...SeasonId=...`).
  Same live-fetch blocker as above. Note this function has no caller
  anywhere in the current codebase (`scrape.py`'s `scrapeYear`, which uses
  it, is itself unused) — lower priority than clusters.

## Refresh procedure

If `sources/usau/parse.py` changes in a way that should change these
fixtures' golden output (or the golden output starts drifting because USAU
changed their page markup), regenerate with a small script:

1. Load the (possibly re-scraped) HTML.
2. Trim it the same way (strip `<script>`/`<style>`/comments, keep only
   `<body>` contents) — see `trim()` in the procedure used to build the
   current fixtures, reproducible from `sources.usau.parse.parseTournament` /
   `parseNewSchedule` plus `bs4.BeautifulSoup`.
3. Parse the trimmed HTML through the same path `UsauSource.parse_event` /
   `discover` uses, serialize via `core.serialize.tournament_to_document`
   (tournament case) or leave as the raw page-link list (calendar case).
4. Write the trimmed HTML and the JSON output over the existing fixture
   files, using a **fixed** `scraped_at` for the tournament case (the current
   fixtures use `datetime(2026, 2, 22, 12, 0, 0, tzinfo=timezone.utc)`) so the
   golden JSON is reproducible.
5. Hand-review the diff before committing — these are reviewed the same way
   `data/*.json` documents are (MULTI-SOURCE-REDESIGN.md's "Repo layout").
