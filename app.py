import json
import hashlib
import urllib.request
import urllib.parse
import logging
import sqlite3
import os
from flask import Flask, request, jsonify

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "/data/cache.db"

# Allowed domains
ALLOWED_DOMAINS = [
    "openexchangerates.org",
    "api.twelvedata.com",
]

# Cacheable path patterns: domain/path prefix
CACHEABLE_PATHS = [
    "openexchangerates.org/api/historical",
    "api.twelvedata.com/eod",
]


def init_db():
    """Initialize SQLite database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            requestHash TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")


def get_cached_data(request_hash):
    """Retrieve cached data from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM cache WHERE requestHash = ?", (request_hash,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def cache_data(request_hash, data):
    """Store data in SQLite cache."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO cache (requestHash, data) VALUES (?, ?)",
        (request_hash, data)
    )
    conn.commit()
    conn.close()


def is_allowed_domain(path):
    """Check if the domain is allowed."""
    path_clean = path.lstrip("/")
    return any(path_clean.startswith(domain) for domain in ALLOWED_DOMAINS)


def is_cacheable_path(path):
    """Check if the path should be cached."""
    path_clean = path.lstrip("/")
    return any(path_clean.startswith(pattern) for pattern in CACHEABLE_PATHS)


def should_cache_response(path, status_code, data):
    """Determine if response should be cached based on path-specific rules."""
    if not is_cacheable_path(path):
        logger.info(f"Path not cacheable: {path}")
        return False

    # Only cache HTTP 200 responses
    if status_code != 200:
        logger.info(f"Status code {status_code} not cacheable")
        return False

    path_clean = path.lstrip("/")
    if path_clean.startswith("openexchangerates.org"):
        # For openexchangerates, cache all 200 responses
        logger.info("openexchangerates: caching 200 response")
        return True
    elif path_clean.startswith("api.twelvedata.com"):
        # For twelvedata, check JSON body for error codes
        try:
            json_data = json.loads(data)
            if "code" in json_data:
                code = json_data["code"]
                # Don't cache 429 (rate limit) or 5xx errors
                if code == 429 or (code >= 500 and code < 600):
                    logger.info(f"twelvedata: not caching error code {code}")
                    return False
            logger.info("twelvedata: caching successful response")
            return True
        except (json.JSONDecodeError, KeyError):
            # If we can't parse JSON, cache it anyway
            logger.info("twelvedata: caching non-JSON response")
            return True
    return False


@app.route("/<path:path>")
def proxy(path):
    """Proxy handler for all requests."""
    query_params = request.args.to_dict()

    logger.info(f"Request received: path={path}, params={query_params}")

    # Validate domain is allowed
    if not is_allowed_domain(path):
        logger.warning(f"Domain not allowed: {path}")
        return jsonify({"error": "Domain not allowed"}), 403

    # Build full upstream URL (path includes domain)
    query_string = urllib.parse.urlencode(query_params)
    full_path = f"/{path}?{query_string}" if query_string else f"/{path}"
    upstream_url = f"https:/{full_path}"

    request_hash = None
    if is_cacheable_path(path):
        # Generate hash for cache key
        request_hash = hashlib.sha256(full_path.encode()).hexdigest()
        logger.info(f"Cacheable path detected, hash={request_hash}")

        # Try to get from cache
        try:
            cached_data = get_cached_data(request_hash)
            if cached_data:
                logger.info(f"Cache HIT for hash={request_hash}")
                return cached_data, 200, {
                    "Content-Type": "application/json",
                    "X-Cache": "HIT",
                }
            logger.info(f"Cache MISS for hash={request_hash}")
        except Exception as e:
            logger.error(f"Cache lookup error: {e}")

    # Make upstream request
    try:
        logger.info(f"Making upstream request to: {upstream_url}")
        req = urllib.request.Request(
            upstream_url, headers={"accept": "application/json"}
        )
        with urllib.request.urlopen(req) as response:
            status_code = response.getcode()
            data = response.read().decode("utf-8")
            logger.info(
                f"Upstream response: status={status_code}, data_length={len(data)}"
            )

            # Check if we should cache this response
            should_cache = should_cache_response(path, status_code, data)
            logger.info(f"Should cache response: {should_cache}")

            if should_cache and request_hash:
                try:
                    cache_data(request_hash, data)
                    logger.info(f"Cached response for hash={request_hash}")
                except Exception as e:
                    logger.error(f"Cache write error: {e}")

            return data, status_code, {
                "Content-Type": "application/json",
                "X-Cache": "MISS",
            }
    except urllib.error.HTTPError as e:
        logger.error(f"HTTP error from upstream: {e.code} - {e}")
        return jsonify({"error": str(e)}), e.code
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080)
