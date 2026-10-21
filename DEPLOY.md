# Deploy Signal using free plans

Use **Render Free** for the web service and **Neon Free** for PostgreSQL. No persistent Render disk or paid Render database is required. Free tiers have usage limits and can change; choose only the Free plan in both dashboards.

## 1. Create the free database

1. Sign in at https://console.neon.tech and create a **Free** project. Choose a region near Oregon / US West if available.
2. Open **Connect** and copy the PostgreSQL connection string. Prefer the pooled hostname for a hosted app. It starts with `postgresql://` and includes the database username and password.
3. Keep this string private. Enter it only in Render's secret environment-variable field, not in Git, a screenshot, or chat. The app verifies TLS certificates for hosted database connections and prefers IPv4 transport for hosting environments without IPv6 routing; the Neon hostname is retained for certificate verification.

## 2. Create the free web service

Open:

https://render.com/deploy?repo=https://github.com/ruoyinggg/AINewsFeed

Sign in, grant Render access to this GitHub repository if prompted, and create the Blueprint. The repository's `render.yaml` selects the **Free** web-service plan, uses the Dockerfile, configures secure cookies, and sets a health check.

When Render asks for **DATABASE_URL**, paste the private Neon connection string. Do not add quotation marks or the surrounding `psql` command. Confirm the web service plan is **Free**, then deploy. The app creates its tables on first startup.

If Blueprint deployment is unavailable, create **New → Web Service**, connect `ruoyinggg/AINewsFeed`, select branch `main`, runtime **Docker**, and instance type **Free**. Set:

- `DATABASE_URL`: your private Neon connection string
- `HOST`: `0.0.0.0`
- `COOKIE_SECURE`: `1`
- Health check path: `/api/health`

Render supplies `PORT` and HTTPS. No build/start override, persistent disk, API keys for news, or paid add-on is needed.

## 3. Verify the public deployment

Once Render reports **Live**, open the HTTPS URL shown on your service dashboard. Allow a short time for the initial publisher fetch.

- `/api/health` should return `{"ok": true}`.
- Open **Our sources** to verify publisher connectivity. News should show the audience labels and the priority **all three → PMs → developers → designers**.
- Upvote an article and add a comment. Reload to confirm they remain.
- Use Render's manual redeploy action, then check the same article and comment again. PostgreSQL preserves feedback independently of Render's ephemeral filesystem.

Do not set `DISABLE_FEEDS=1` in the hosted service. This is only for isolated tests.

## Free-plan limitations

Render's free web service sleeps after inactivity and can take a while to wake. News is fetched at startup and every 24 hours **while the service is running**, not continuously while it sleeps. The browser reloads current results every 30 seconds while visible. Neon may also suspend idle database compute and wake on demand. Availability and capacity are subject to each provider's free-tier limits.

Votes and comments are stored in Neon when `DATABASE_URL` is configured. Without it, the app falls back to SQLite; local SQLite data on a free Render service will be lost on redeploy or replacement. Existing feedback in your computer's SQLite file is not automatically transferred to Neon; the local file is preserved.

This version supports anonymous feedback and basic rate limits. It does not include accounts or comment moderation tools. Keep an eye on public comments during a small beta.

## Other deployment options

For a machine you control with Docker Compose:

```sh
docker compose up --build -d
```

This uses a persistent named SQLite volume and binds port 8000 to the host's loopback interface. Put an HTTPS reverse proxy in front of it for external access and set `COOKIE_SECURE=1` for HTTPS. Do not delete the `signal-data` volume if you need to preserve feedback.

## Local PostgreSQL tests

Local SQLite tests require no extra packages. To run the same test suite against PostgreSQL, install `requirements.txt` and set `TEST_DATABASE_URL` to a **dedicated disposable test database**:

```sh
python -m pip install -r requirements.txt
TEST_DATABASE_URL='postgresql://test-user:test-password@localhost:5432/signal_test' python -m unittest discover -s tests -v
```

The test suite deletes application table contents in that test database. Never point `TEST_DATABASE_URL` at a live database. `DATABASE_URL` is not used by the test suite unless explicitly supplied as `TEST_DATABASE_URL`.

## Container TLS regression check

To exercise startup as the unprivileged container user against a real TLS-enabled PostgreSQL server:

```sh
docker build -t signal-news:deployment-test .
python tests/check_container_tls.py
```

This requires Docker, OpenSSL, and available local ports 15432 and 18000. It creates disposable test containers and a temporary test CA, deliberately supplies `HOME=/root` at launch, and verifies that startup resets the application home and connects with certificate verification. It cleans up its containers afterwards and never uses a live database.
