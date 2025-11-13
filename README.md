# Currency Rates Cache Proxy - Docker

A self-contained Docker service that proxies and caches currency rate API requests using SQLite.

## Quick Start

Build and run:

```bash
docker build -t currency-cache .
docker run -d -p 8080:8080 -v currency-data:/data --name currency-cache currency-cache
```

## Testing

Run tests locally:

```bash
pip install -r requirements.txt
python -m pytest test_app.py -v
```

Or run tests in Docker:

```bash
docker build -t currency-cache .
docker run --rm currency-cache python -m pytest test_app.py -v
```

## Usage

The service runs on port 8080 and proxies requests to allowed domains:

```bash
# Example: OpenExchangeRates
curl "http://localhost:8080/openexchangerates.org/api/historical/2024-01-01.json?app_id=YOUR_APP_ID&base=USD&symbols=AUD"

# Example: TwelveData
curl "http://localhost:8080/api.twelvedata.com/eod?symbol=AMZN&apikey=YOUR_API_KEY&date=2024-01-01"
```

## Data Persistence

SQLite database is stored in `/data/cache.db` inside the container. Use a volume to persist data across container restarts.
