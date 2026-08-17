# USAU Tournament Scraper

Scrapes tournament data from USA Ultimate's website.

## Setup

### Dependencies

- Python 3.9+
- pipenv
- Tor (for proxied requests)
- Chrome/Chromium (for Selenium)

### Installation

```bash
pipenv install --dev
pipenv run pip install -e .
```

### Configuration

1. Copy `.env.example` to `.env` and configure:
```
API_URL=<your api url>
INGEST_TOKEN=<shared secret for POST /v2/ingest>
YOUTUBE_API_KEY=<optional>
VIMEO_CLIENT_ID=<optional>
VIMEO_CLIENT_SECRET=<optional>
VIMEO_ACCESS_TOKEN=<optional>
COMMIT_AND_PUSH=False
POST_TO_API=False
LOAD_CAL_ON_START=False
HOST=0.0.0.0
```

`INGEST_TOKEN` is sent as the `X-Ingest-Token` header by `scrape --post` and
`post-documents`, and must match what the API has configured (docker-compose
passes `INGEST_TOKEN` through to the api service). The API replies **503**
when it has no token at all and **401** on a mismatch.

2. Edit `config.yaml` to customize settings (season IDs, scheduler intervals, divisions).

## Usage

### CLI Commands

```bash
# Scrape all tournaments for a year
scraper scrape year 2026
scraper scrape year 2026 -d -o          # disable cache, overwrite CSVs

# Scrape calendar only
scraper scrape calendar -y 2026

# Scrape a single tournament
scraper scrape tournament <url> -y 2026

# Retry failed tournaments
scraper scrape retry 2026

# Full scrape with commit and post to API
scraper scrape full --commit --post

# Run the Flask server
scraper serve
scraper serve --no-scheduler            # without background jobs
scraper serve -p 8080                   # custom port

# Scrape videos
scraper videos
```

### Flask Server

The server provides a health check endpoint and runs background scrapers on a schedule:

- `/health-check` - Returns counts of ongoing, upcoming, and recently ended tournaments

Background jobs (configurable in `config.yaml`):
- Calendar scrape: every 8 hours
- Ongoing tournaments: every 10 minutes
- Upcoming tournaments: every 12 hours
- Recently ended: every 4 hours
- Videos: every 24 hours

## Development

### Running Tests

```bash
pipenv run pytest tests/ -v
```

### Project Structure

```
├── cli.py          # Click CLI entry point
├── config.py       # Pydantic config models
├── config.yaml     # Application configuration
├── scrape.py       # Scraping logic
├── parse.py        # HTML parsing
├── app.py          # Flask application
├── models.py       # Data models
├── db.py           # Database utilities
├── tor.py          # Tor proxy utilities
├── video/          # Video scraping
└── tests/          # Unit tests
```
