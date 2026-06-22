# readable-mcp

**Turn any URL into clean, LLM-ready Markdown. A production MCP server: rate-limited, retried, cached, and SSRF-safe.**

LLMs choke on raw HTML — nav bars, ad markup, and tracking scripts burn tokens and bury the actual content. Worse, a naïve "just fetch the URL" tool is a server-side request forgery (SSRF) hole waiting to be pointed at `169.254.169.254` or your internal network. `readable-mcp` solves both: it extracts the real content as Markdown/text/cleaned-HTML, and it does the fetching the way a production service would — validated, rate-limited, retried with backoff, cached, and observable. This is not a weekend demo; every outbound request assumes the network is hostile.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![MCP](https://img.shields.io/badge/protocol-MCP-purple)
![FastMCP](https://img.shields.io/badge/built%20with-FastMCP%203.x-orange)

---

## Features

Production qualities first — these are what make it safe to point at the open web:

- **SSRF-safe fetching** — resolves the hostname and refuses any address in private, loopback, link-local, or reserved space, so `localhost`, raw private IPs, *and* public hostnames that resolve inward are all blocked.
- **Token-bucket rate limiting** — a single shared limiter (default 5 req/s, burst 10) gates every outbound request, including inside batch calls.
- **Exponential-backoff retries** — retries timeouts, connection errors, `429`, and `5xx` with jittered backoff, and **honors `Retry-After`** on `429`. Never retries other 4xx (those are caller errors).
- **Explicit timeouts** — separate connect/read and a hard total ceiling; a request can never hang forever.
- **TTL caching** — in-memory cache keyed by normalized URL + format (default 900s); cache hits are flagged `from_cache=true`.
- **Structured typed errors** — tools never raise to the client; failures come back as a typed `{code, message, retryable}`.
- **First-class partial-success batch** — one bad URL never fails the batch; each URL gets its own success/error slot.
- **JSON observability logs** — one structured line per call (request id, tool, host, latency, cache hit/miss, outcome) — never response bodies or secrets.

And the capability itself:

- **Clean content extraction** — `trafilatura` strips boilerplate and pulls title, author, and publish date; output as Markdown, plain text, or cleaned HTML.

---

## Architecture

```
 ┌────────────┐   extract_url / extract_batch / get_stats
 │ MCP client │──────────────────────────────────────────────┐
 │ (Claude,   │                                               │
 │  Cursor…)  │                                               ▼
 └────────────┘                                     ┌───────────────────┐
                                                     │  server.py (tools)│  thin orchestration
                                                     └─────────┬─────────┘
                                                               │
        ┌──────────────┬───────────────┬────────────┬─────────┴────────┐
        ▼              ▼               ▼            ▼                  ▼
 ┌─────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────┐ ┌──────────────┐
 │ validation  │ │ rate_limiter│ │  cache   │ │ http_client  │ │  extractor   │
 │  + SSRF     │→│ token bucket│→│ TTL (hit │→│ retry+timeout│→│ trafilatura  │
 │  guard      │ │  acquire()  │ │  / miss) │ │  httpx       │ │  → Markdown  │
 └─────────────┘ └────────────┘ └──────────┘ └──────────────┘ └──────┬───────┘
                                                                      ▼
                                                          ┌───────────────────────┐
                                                          │ typed ExtractionResult │
                                                          │   or ExtractionError   │
                                                          └───────────────────────┘
```

Validation runs **before** anything touches a socket; the rate limiter and cache sit in front of the network; every failure is funneled into a typed result.

---

## Production handling, not a demo

Four patterns pulled straight from the source — the reason this is portfolio-grade.

### 1. SSRF guard that resolves before it trusts

A string check on the hostname is not enough: an attacker can register a public domain whose DNS points at `169.254.169.254`. We resolve first, then check every resolved IP against the non-public ranges.

```python
# validation.py
host = parts.hostname
try:
    literal = ipaddress.ip_address(host)      # raw-IP host?
    candidates = [str(literal)]
except ValueError:
    candidates = _resolve_addresses(host)     # otherwise resolve DNS

for ip in candidates:
    if not _is_public_ip(ip):                 # private/loopback/link-local/reserved
        raise ValidationError(
            ErrorCode.BLOCKED_HOST,
            f"Refusing to fetch {host!r}: resolves to non-public address {ip}.",
        )
```

*Why it matters:* this is the difference between a fetch tool and an internal-network proxy for whoever controls the input. It's the first thing a security-minded reviewer checks.

### 2. Async token-bucket rate limiter

One shared bucket throttles every outbound request — including the concurrent fetches inside `extract_batch` — so the server stays a polite citizen under load.

```python
# rate_limiter.py
async def acquire(self, tokens: float = 1.0) -> None:
    while True:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            self._refill(now)
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            wait = (tokens - self._tokens) / self._rate
        await asyncio.sleep(wait)   # sleep outside the lock
```

*Why it matters:* tokens refill continuously and the wait happens outside the lock, so many coroutines can share one limiter without deadlocking or over-drawing the bucket.

### 3. Retry with backoff that honors `Retry-After`

Transient failures (`429`, `5xx`, timeouts, connection errors) are retried with jittered exponential backoff; a `429` with a `Retry-After` header is obeyed exactly. Other 4xx are returned immediately — retrying a `404` is just wasted requests.

```python
# http_client.py
if status in _RETRYABLE_STATUS:
    if not is_last:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        delay = retry_after if retry_after is not None else _backoff_delay(
            attempt, self._settings.retry_base_delay
        )
        await self._sleep(delay)
        continue
    raise last_error
if status >= 400:
    raise FetchError(ErrorCode.HTTP_ERROR, ..., retryable=False)  # caller error, no retry
```

*Why it matters:* backoff with jitter prevents thundering-herd retries, and honoring `Retry-After` is what keeps you from getting hard-blocked by the upstream.

### 4. Structured errors — the client never sees a stack trace

Every failure path converges on one typed shape. Tools return it instead of raising, so a bad URL degrades gracefully instead of crashing the tool call.

```python
# server.py
def _error(url: str, code: ErrorCode, message: str, *, retryable: bool) -> ExtractionError:
    return ExtractionError(
        url=url, error=ErrorDetail(code=code, message=message, retryable=retryable)
    )
```

*Why it matters:* the calling LLM can branch on `error.code` and `error.retryable` programmatically, and in a batch one bad URL simply occupies its own error slot.

---

## Quickstart

**With [uv](https://docs.astral.sh/uv/) (recommended):**

```bash
git clone https://github.com/tommypj/readable-mcp.git
cd readable-mcp
uv sync                 # install
uv run readable-mcp     # run the server over stdio
```

**With pip:**

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
readable-mcp
```

The server speaks the MCP stdio transport, so it's normally launched by an MCP client (below) rather than run by hand.

---

## Use it in Claude Desktop / Claude Code

Add this to your `claude_desktop_config.json` (mirrors [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)):

```json
{
  "mcpServers": {
    "readable": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/readable-mcp", "run", "readable-mcp"]
    }
  }
}
```

Config file locations:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Claude Code:** `claude mcp add readable -- uv --directory /absolute/path/to/readable-mcp run readable-mcp`
- **Cursor:** add the same `mcpServers` block under *Settings → MCP*.

Restart the client, then ask: *"Extract the main content of https://example.com as markdown."*

---

## Tools reference

### `extract_url(url, output_format="markdown")`

Fetch one URL and return its main content plus metadata.

- **`url`** — an `http`/`https` URL. Private/loopback/link-local hosts are refused.
- **`output_format`** — `"markdown"` (default), `"text"`, or `"html"` (cleaned).
- **Returns** `ExtractionResult` *or* `ExtractionError`.
- **Error codes:** `INVALID_URL`, `BLOCKED_HOST`, `FETCH_TIMEOUT`, `HTTP_ERROR`, `EXTRACTION_FAILED`, `RATE_LIMITED`.

```jsonc
// example result (trimmed)
{
  "url": "https://example.com/",
  "final_url": "https://example.com/",
  "title": "Example Domain",
  "author": null,
  "published": null,
  "word_count": 17,
  "output_format": "markdown",
  "content": "This domain is for use in documentation examples...",
  "from_cache": false,
  "fetched_at": "2026-06-23T10:01:22.481+00:00"
}
```

### `extract_batch(urls, output_format="markdown")`

Extract up to **10** URLs concurrently; partial success is first-class.

- **`urls`** — list of URLs (max 10 processed; the rest return `TOO_MANY_URLS`).
- **Returns** `BatchResult` with `requested` / `succeeded` / `failed` counts and a per-URL `results` list (each a result or a typed error).

```jsonc
{ "requested": 2, "succeeded": 1, "failed": 1,
  "results": [
    { "url": "https://example.com/", "title": "Example Domain", "word_count": 17, ... },
    { "url": "http://127.0.0.1/", "error": { "code": "BLOCKED_HOST", "message": "...", "retryable": false } }
  ] }
```

### `get_stats()`

Return a lightweight operational snapshot — uptime, requests served, cache hit/miss + hit rate, and current config. No sensitive data.

```jsonc
{ "version": "0.1.0", "uptime_seconds": 124.3, "requests_served": 6,
  "cache_hits": 2, "cache_misses": 1, "cache_hit_rate": 0.6667,
  "rate_limit_rps": 5.0, "cache_ttl_seconds": 900 }
```

---

## Configuration

All settings are environment variables prefixed `READABLE_MCP_` (or a `.env` file — see [`.env.example`](.env.example)). No secrets are required.

| Variable | Default | Description |
|---|---|---|
| `READABLE_MCP_RATE_LIMIT_RPS` | `5` | Sustained outbound requests per second |
| `READABLE_MCP_RATE_LIMIT_BURST` | `10` | Token-bucket capacity (max burst) |
| `READABLE_MCP_MAX_RETRIES` | `3` | Total attempts per fetch (incl. the first) |
| `READABLE_MCP_RETRY_BASE_DELAY` | `0.5` | Base backoff delay (seconds) |
| `READABLE_MCP_CONNECT_TIMEOUT` | `5` | Connection-establishment timeout (s) |
| `READABLE_MCP_READ_TIMEOUT` | `15` | Socket read timeout (s) |
| `READABLE_MCP_TOTAL_TIMEOUT` | `20` | Hard ceiling for one request (s) |
| `READABLE_MCP_CACHE_TTL` | `900` | Cache entry lifetime (s) |
| `READABLE_MCP_CACHE_MAXSIZE` | `512` | Max cached entries |
| `READABLE_MCP_MAX_BATCH_URLS` | `10` | Max URLs per `extract_batch` call |
| `READABLE_MCP_USER_AGENT` | `readable-mcp/0.1 (+…)` | Outbound `User-Agent` |
| `READABLE_MCP_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## Testing

```bash
uv run pytest          # 49 tests, fully offline (network mocked with respx)
uv run ruff check .    # lint
uv run ruff format .   # format
```

Tests cover the production paths directly: SSRF/validation cases, token-bucket timing under concurrency, cache hit/miss/expiry, retry-on-`429`/`503` + `Retry-After` + give-up + no-retry-on-`404`, extraction against saved HTML fixtures, and the tool happy/error/partial-success paths.

---

## Design decisions

- **`trafilatura` for extraction** — best-in-class boilerplate removal with built-in title/author/date detection and native Markdown output; `markdownify` is the fallback when no main body is detected, so the caller still gets usable content rather than an error.
- **Resolve-then-check SSRF** — checking the literal host is insufficient; we resolve DNS and validate every returned IP so a public hostname can't tunnel to private space. Literal-IP hosts skip DNS and are checked directly.
- **In-memory TTL cache (not Redis)** — an MCP server is a single local process per client; an in-process `TTLCache` under an `asyncio.Lock` gives the hit-rate win with zero external dependencies. Swappable behind the `cache.py` boundary if a shared cache is ever needed.
- **Errors as values, not exceptions** — tools return typed `ExtractionError`s so the model can branch on `code`/`retryable` and a batch can carry mixed success/failure. The server is designed to never crash on bad input.
- **Shared rate limiter + concurrency cap for batches** — the token bucket bounds throughput while a semaphore bounds simultaneous sockets, so a 10-URL batch is both polite and bounded.
- **`max_retries` = total attempts** — named for the common env-var convention, but semantically the attempt ceiling (default 3 ⇒ 2 retries). Documented here to avoid the off-by-one ambiguity.
- **Unknown `output_format`** — returned as a typed `EXTRACTION_FAILED` error with a clear message rather than silently coercing, so callers learn about the mistake.

---

## License

MIT © Dan Tomescu. See [LICENSE](LICENSE).
