# DEMO_SCRIPT.md — 2-minute Loom walkthrough

A tight, speakable script to read while screen-recording. Keep the screen on Claude
Desktop for the first half, the README for the second. Times are cues, not limits.

---

**(0:00 — what it is)**
> "This is `readable-mcp` — a Model Context Protocol server that turns any URL into
> clean, LLM-ready Markdown. It's built for AI engineers who need their agents to read
> the web safely, not just scrape it."

**(0:15 — wired into Claude, real extraction)**
> "It's already wired into Claude Desktop. Watch — I'll just ask:"
>
> *Type:* `Extract the main content of https://example.com as markdown.`
>
> "Claude calls `extract_url`, and back comes clean Markdown — title, word count, the
> body text — with all the nav and boilerplate stripped out. No raw HTML, no wasted
> tokens."

**(0:45 — batch + resilience + SSRF)**
> "Now the part that separates this from a weekend demo. I'll run a batch with one
> good URL and one deliberately bad one — a request to `127.0.0.1`."
>
> *Type:* `Extract these two as a batch: https://example.com and http://127.0.0.1/admin`
>
> "One bad URL never fails the batch. The good one succeeds; the localhost one comes
> back as a structured error — `BLOCKED_HOST`, not-retryable. That's the SSRF guard:
> it resolves the host and refuses anything that points into a private network."

**(1:15 — show the production handling in the README)**
> "Why does that work? Flip to the README, the 'Production handling' section. Real
> code, four snippets: the SSRF guard that resolves DNS before it trusts a host; the
> async token-bucket rate limiter; the retry loop with exponential backoff that honors
> `Retry-After`; and the typed-error return. Every outbound request assumes the network
> is hostile."

**(1:40 — get_stats, cache hit)**
> "Last thing — observability. I'll ask for stats after re-fetching that same URL."
>
> *Type:* `Extract https://example.com again, then call get_stats.`
>
> "There's the cache hit — `from_cache` is true, the hit rate ticks up, and you can see
> uptime, requests served, and the live config. It's a service, not a script."

**(1:55 — close)**
> "Rate-limited, retried, cached, SSRF-safe, fully tested. This is the bar I build every
> MCP integration to."
