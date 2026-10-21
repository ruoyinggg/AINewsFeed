# Signal · AI News Feed

A small, responsive AI news reader: **up to 10 recent AI stories**, **one recommended analysis article**, and **thumbs up/down and public comments on every item**.

## Run

Requires Python **3.12 or newer**. No packages, API keys, build step, or external database are required.

```sh
cd /workspace/AINewsFeed
python app.py
```

The server listens on port 8000 on the local machine. Open it using your development environment's supported browser or port-forwarding feature. For access outside the machine, explicitly set `HOST=0.0.0.0` and use an appropriate reverse proxy. This repository includes a development server, not a hosted deployment.

```sh
python -m unittest discover -s tests -v
```

The test suite uses a temporary database and mocked publisher responses. It tests RSS/Atom ingestion, partial outages and caching, ranking, duplicate removal, voting across separate browser sessions, comments, rate limits, input validation, and static route protections. It does not contact publishers.

## How it works

- **Collection:** a background worker fetches public RSS feeds at startup and every 15 minutes. Requests have a 15-second timeout and 2 MB response limit. Failed publishers do not discard cached articles. TLS verification remains enabled.
- **News:** TechCrunch and The Guardian's dedicated AI feeds supply the shortlist. Stories need a usable publication date within the past seven days and a practical signal for AI product work. The strict order is **all three disciplines → PMs → developers → designers**. Explicit keyword rules in `audience_profile` estimate audiences from publisher titles/excerpts: product strategy/adoption/pricing for PMs, APIs/SDKs/inference for developers, and interfaces/research/prototyping for designers. Practical cross-functional topics (such as evaluating multimodal assistants) or evidence for all three disciplines get first priority. Two-discipline stories join the earliest applicable group. These are transparent heuristics, not an LLM judgment, and may misclassify stories.
- **Within each audience group:** practical relevance, freshness, and publisher diversity determine order. The score is three times the matched relevance signals minus age in hours minus six hours per already-selected story from that publisher. Recency never moves a lower-priority audience ahead of a higher-priority one. Duplicate URLs and normalized identical titles are removed. We show fewer than ten stories rather than backfill with generic funding or political coverage without practical signals.
- **Deep read:** MIT Technology Review and AI Snake Oil (now hosted at Normal Tech) supply the analysis pool. One article from the past 30 days is selected using the same audience priority, then relevance, recency, and excerpt length. No relevant analysis means no recommendation. This heuristic is a starting point, not a claim of objective quality or an LLM assessment.
- **Attribution:** titles and excerpts come from the publishers. Every story links to its original article. Full articles are not copied; a publisher may require a subscription.
- **Feedback:** a random HttpOnly, SameSite cookie identifies a browser. A reader can upvote or downvote each story, change their choice, or click again to remove it. Counts and comments are shared across browsers and survive server restarts. Names and comments are public. The name field is remembered in browser local storage.
- **Storage:** SQLite with WAL mode at `data/news.sqlite3`, ignored by Git. Back up the database (using SQLite's backup API or after stopping the server) to preserve feedback. The app does not delete old feedback.
- **Failure states:** source health and stale data are visible. If there are no recent stories, an empty state offers an opt-in **sample briefing**. Sample headlines are illustrative, links go to publisher homepages, and sample feedback is only in memory. Sample items are never inserted into the database or presented as live reporting.
- **Refresh:** the frontend checks the server every 30 seconds while visible. “Refresh” reloads the current shortlist; it does not trigger extra requests to publishers. Audience filters operate on the selected top ten: “For all three” shows shared stories only, while a discipline filter includes shared stories and any other stories tagged for that discipline. Every story displays its primary audience and a rule-based “Why it matters” note.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Listen interface |
| `PORT` | `8000` | Listen port |
| `DATABASE_PATH` | `data/news.sqlite3` next to `app.py` | Persistent SQLite path |
| `COOKIE_SECURE` | unset | Set to `1` behind HTTPS so identity cookies are secure |
| `DISABLE_FEEDS` | unset | Set to `1` to stop background fetching for isolated tests |

The cloud environment needs HTTPS access to these feed domains:

- `techcrunch.com`
- `www.theguardian.com`
- `www.technologyreview.com`
- `www.normaltech.ai`

`www.aisnakeoil.com` redirects to `www.normaltech.ai`; the application uses the latter directly. Publisher domains may change or redirect again. The source status panel reports unavailable feeds, and the server log records diagnostic failures. No publisher credentials are required.

## API

All responses are JSON. Write requests require `Content-Type: application/json`; cross-origin browser writes are rejected. Visit the app first to establish a browser cookie before making repeated API requests.

| Method | Route | Body / response |
| --- | --- | --- |
| GET | `/api/health` | Server liveness (`ok`); not publisher readiness |
| GET | `/api/feed` | Ranked articles, featured analysis, source health, timestamps, and reader votes |
| POST | `/api/articles/{id}/vote` | `{"value":1}`, `{"value":-1}`, or `{"value":0}`; updated counts |
| GET | `/api/articles/{id}/comments` | Latest 100 comments, newest first |
| POST | `/api/articles/{id}/comments` | `{"name":"Reader","body":"My perspective"}` |

## Before public production use

This is a functional first version with anonymous participation. It includes text escaping, parameterized SQL, size limits, same-origin checks, security headers, and a five-comments-per-minute limit per browser. Browser identities can be reset; there are no accounts, human moderation, reporting tools, or robust abuse prevention yet. A public launch should add those capabilities, a production web server/reverse proxy, operational monitoring, backups, a privacy policy, and a review of publisher feed terms. No deployment has been performed.
