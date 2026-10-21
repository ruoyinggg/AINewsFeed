#!/usr/bin/env python3
"""AI news reader. Python 3.12+, standard library only."""
import concurrent.futures
from contextlib import contextmanager
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from database import PostgresConnection
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('DATABASE_PATH', str(ROOT / 'data' / 'news.sqlite3')))
DATABASE_URL = os.environ.get('DATABASE_URL', '')
REFRESH_SECONDS = 24 * 60 * 60
SOURCES = [
    {'name': 'TechCrunch', 'url': 'https://techcrunch.com/category/artificial-intelligence/feed/', 'kind': 'news'},
    {'name': 'The Guardian', 'url': 'https://www.theguardian.com/technology/artificialintelligenceai/rss', 'kind': 'news'},
    {'name': 'MIT Technology Review', 'url': 'https://www.technologyreview.com/topic/artificial-intelligence/feed', 'kind': 'deep'},
    {'name': 'AI Snake Oil', 'url': 'https://www.normaltech.ai/feed', 'kind': 'deep'},
]
log = logging.getLogger('signal')
refresh_lock = threading.Lock()


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        if tag in ('p', 'br', 'div'): self.parts.append(' ')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
        if tag in ('p', 'div'): self.parts.append(' ')
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def plain(value, limit=500):
    parser = TextOnly()
    parser.feed(value or '')
    return re.sub(r'\s+', ' ', html.unescape(''.join(parser.parts))).strip()[:limit]


@contextmanager
def connect():
    if DATABASE_URL:
        conn = PostgresConnection(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    if not DATABASE_URL:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS articles (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT UNIQUE NOT NULL,
          description TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL,
          category TEXT NOT NULL, published REAL NOT NULL, fetched REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS votes (
          article_id TEXT REFERENCES articles(id), visitor TEXT NOT NULL,
          value INTEGER NOT NULL CHECK(value IN (-1,1)), updated REAL NOT NULL,
          PRIMARY KEY(article_id, visitor));
        CREATE TABLE IF NOT EXISTS comments (
          id INTEGER PRIMARY KEY, article_id TEXT REFERENCES articles(id),
          visitor TEXT NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS comments_article ON comments(article_id,created);
        CREATE TABLE IF NOT EXISTS source_status (
          name TEXT PRIMARY KEY, checked REAL NOT NULL, success REAL, error TEXT, count INTEGER NOT NULL);
        ''')


def category(title, description):
    text = (title + ' ' + description).lower()
    for label, words in [('Policy & society', ['regulat', 'govern', 'copyright', 'safety', 'ethic', 'election']),
                         ('Research', ['research', 'paper', 'scientist', 'benchmark', 'study']),
                         ('Industry', ['funding', 'billion', 'acqui', 'startup', 'invest'])]:
        if any(w in text for w in words): return label
    return 'Models & tools'


# These are explicit editorial rules over publisher titles/excerpts, not an LLM.
AUDIENCE_RULES = {
    'PMs': [r'product management|product managers?|product strategy|product roadmap',
            r'pricing|monetization|user adoption|retention|customer feedback|product.market fit',
            r'case stud(?:y|ies)|product launch|go.to.market|a/b test|product metrics'],
    'Developers': [r'\bapi[s]?\b|\bsdk[s]?\b|\bide[s]?\b|coding|programming|developer',
                   r'open.source|inference|fine.tun|latency|\brag\b|embeddings?|context window',
                   r'debug|deployment|code review|software engineer|function calling|tool calling'],
    'Designers': [r'\bux\b|\bui\b|designers?|interaction design|interface|prototyp',
                  r'user research|usability|accessibility|design system|figma',
                  r'human.computer interaction|conversational design|user experience'],
}
SHARED_TOPICS = r'ai eval(?:uation)?s?|human.in.the.loop|agent workflows?|multimodal|ai assistants?|ai agents?'
PRACTICAL_ACTION = r'launch|releas|introduc|build|design|workflow|evaluat|how to|guide|integrat|test|improv'


def audience_profile(title, description):
    text = (title + ' ' + description).lower()
    scores = {role: sum(bool(re.search(rule, text)) for rule in rules)
              for role, rules in AUDIENCE_RULES.items()}
    shared = bool(re.search(SHARED_TOPICS, text) and re.search(PRACTICAL_ACTION, text))
    # A practical cross-functional topic, or evidence for every discipline, goes first.
    if shared or all(scores.values()):
        return {'audience': 'All three', 'audiences': list(scores), 'audience_rank': 0,
                'relevance': max(1, sum(scores.values())),
                'why_it_matters': 'A shared product capability or workflow for PMs, developers, and designers to evaluate together.'}
    relevant = [role for role, score in scores.items() if score]
    if not relevant:
        return {'audience': None, 'audiences': [], 'audience_rank': 4, 'relevance': 0,
                'why_it_matters': ''}
    # Mixed two-discipline items join the earliest applicable group in the requested order.
    primary = relevant[0]
    reasons = {
        'PMs': 'Useful for product decisions: strategy, adoption, pricing, or measuring customer value.',
        'Developers': 'Useful for building AI: implementation tools, APIs, model performance, or delivery.',
        'Designers': 'Useful for shaping AI experiences: interaction patterns, research, prototyping, or accessibility.',
    }
    return {'audience': primary, 'audiences': relevant,
            'audience_rank': list(scores).index(primary) + 1,
            'relevance': sum(scores.values()), 'why_it_matters': reasons[primary]}


def parse_feed(data, source, now=None):
    now = now or time.time()
    if len(data) > 2_000_000: raise ValueError('Feed exceeds size limit')
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Feed contains unsupported XML declarations')
    root = ET.fromstring(data)
    entries = root.findall('.//item') or root.findall('{http://www.w3.org/2005/Atom}entry')
    if root.tag.split('}')[-1] not in ('rss', 'feed', 'RDF'):
        raise ValueError('Response is not an RSS or Atom feed')
    result = []
    for entry in entries[:100]:
        def value(*names):
            for name in names:
                node = entry.find(name)
                if node is not None:
                    return ''.join(node.itertext())
            return ''
        title = plain(value('title', '{http://www.w3.org/2005/Atom}title'), 300)
        url = value('link')
        if not url:
            for link in entry.findall('{http://www.w3.org/2005/Atom}link'):
                if link.get('rel', 'alternate') == 'alternate':
                    url = link.get('href', ''); break
        url = url.strip()
        if not title or urlsplit(url).scheme not in ('http', 'https') or not urlsplit(url).hostname: continue
        description = plain(value('description', '{http://www.w3.org/2005/Atom}summary',
                                  '{http://purl.org/rss/1.0/modules/content/}encoded',
                                  '{http://www.w3.org/2005/Atom}content'))
        raw_date = value('pubDate', '{http://www.w3.org/2005/Atom}published',
                         '{http://www.w3.org/2005/Atom}updated', '{http://purl.org/dc/elements/1.1/}date')
        try:
            try: dt = parsedate_to_datetime(raw_date)
            except (ValueError, TypeError): dt = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
            if not dt.tzinfo: dt = dt.replace(tzinfo=timezone.utc)
            published = min(dt.timestamp(), now)
        except (ValueError, TypeError, OverflowError):
            # Unknown publication times must not masquerade as fresh news.
            continue
        result.append({'id': hashlib.sha256(url.encode()).hexdigest()[:24], 'title': title,
                       'url': url, 'description': description, 'source': source['name'],
                       'kind': source['kind'], 'category': category(title, description),
                       'published': published, 'fetched': now})
    return result


def fetch_source(source):
    request = Request(source['url'], headers={'User-Agent': 'SignalAI/1.0 (RSS reader)',
                                              'Accept': 'application/rss+xml, application/atom+xml, application/xml'})
    with urlopen(request, timeout=15) as response:
        return parse_feed(response.read(2_000_001), source)


def refresh_feeds():
    if not refresh_lock.acquire(blocking=False): return
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(fetch_source, source): source for source in SOURCES}
            for future in concurrent.futures.as_completed(pending):
                source = pending[future]
                now = time.time()
                try:
                    articles = future.result()
                    if not articles: raise ValueError('Feed contained no dated, usable articles')
                    with connect() as conn:
                        for article in articles:
                            conn.execute('''INSERT INTO articles VALUES (:id,:title,:url,:description,:source,:kind,:category,:published,:fetched)
                                ON CONFLICT(url) DO UPDATE SET title=excluded.title, description=excluded.description,
                                published=excluded.published, fetched=excluded.fetched''', article)
                        conn.execute('''INSERT INTO source_status VALUES (?,?,?,NULL,?)
                            ON CONFLICT(name) DO UPDATE SET checked=excluded.checked,success=excluded.success,error=NULL,count=excluded.count''',
                                     (source['name'], now, now, len(articles)))
                except Exception as exc:
                    log.warning('Feed %s: %s', source['name'], exc)
                    with connect() as conn:
                        conn.execute('''INSERT INTO source_status VALUES (?,?,NULL,?,0)
                            ON CONFLICT(name) DO UPDATE SET checked=excluded.checked,error=excluded.error''',
                                     (source['name'], now, 'Publisher unavailable. Check network access or try later.'))
    finally: refresh_lock.release()


def feed_worker():
    while True:
        refresh_feeds()
        time.sleep(REFRESH_SECONDS)


def serialize_article(conn, article, visitor):
    result = {**dict(article), **audience_profile(article['title'], article['description'])}
    counts = conn.execute('SELECT SUM(CASE WHEN value=1 THEN 1 ELSE 0 END) AS up, SUM(CASE WHEN value=-1 THEN 1 ELSE 0 END) AS down FROM votes WHERE article_id=?', (article['id'],)).fetchone()
    vote = conn.execute('SELECT value FROM votes WHERE article_id=? AND visitor=?', (article['id'], visitor)).fetchone()
    result.update(up=counts['up'] or 0, down=counts['down'] or 0, my_vote=vote['value'] if vote else 0,
                  comments=conn.execute('SELECT COUNT(*) FROM comments WHERE article_id=?', (article['id'],)).fetchone()[0])
    return result


def get_feed(visitor):
    now = time.time()
    with connect() as conn:
        rows = conn.execute('SELECT * FROM articles WHERE published>? ORDER BY published DESC', (now - 7*86400,)).fetchall()
        # Audience priority is strict; freshness/diversity only rank within a group.
        candidates = [{**dict(r), **audience_profile(r['title'], r['description'])} for r in rows]
        remaining = [r for r in candidates if r['kind'] == 'news' and r['relevance']]
        selected, sources, titles = [], {}, set()
        while remaining and len(selected) < 10:
            best = max(remaining, key=lambda r: (-r['audience_rank'], 3*r['relevance'] - (now-r['published'])/3600 - 6*sources.get(r['source'], 0)))
            remaining.remove(best)
            normalized = re.sub(r'\W+', '', best['title'].lower())
            if normalized in titles: continue
            titles.add(normalized)
            selected.append(best)
            sources[best['source']] = sources.get(best['source'], 0) + 1
        deep_rows = conn.execute("SELECT * FROM articles WHERE kind='deep' AND published>? ORDER BY published DESC", (now-30*86400,)).fetchall()
        deep_candidates = [{**dict(r), **audience_profile(r['title'], r['description'])} for r in deep_rows]
        deep = max((r for r in deep_candidates if r['relevance']),
                   key=lambda r: (-r['audience_rank'], r['relevance'] + min(len(r['description']), 400)/100 - (now-r['published'])/86400), default=None)
        status = [dict(row) for row in conn.execute('SELECT * FROM source_status ORDER BY name')]
        success = [s['success'] for s in status if s['success']]
        return {'articles': [serialize_article(conn, r, visitor) for r in selected],
                'deep_read': serialize_article(conn, deep, visitor) if deep else None,
                'sources': [{**s, 'kind': next(x['kind'] for x in SOURCES if x['name']==s['name'])} for s in status],
                'last_updated': max(success) if success else None,
                'refreshing': refresh_lock.locked(), 'refresh_seconds': REFRESH_SECONDS,
                'stale': not success or min(success) < now-REFRESH_SECONDS*2}


class Handler(BaseHTTPRequestHandler):
    server_version = 'SignalAI'

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def visitor(self):
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie', ''))
        except Exception: pass
        visitor = cookie.get('signal_visitor')
        value = visitor.value if visitor else ''
        if not re.fullmatch(r'[a-f0-9]{64}', value):
            value = secrets.token_hex(32)
            self.new_cookie = value
        return value

    def respond(self, status, body, content_type='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header('Cache-Control', 'no-store' if 'json' in content_type else 'no-cache')
        if getattr(self, 'new_cookie', None):
            secure = '; Secure' if os.environ.get('COOKIE_SECURE') == '1' else ''
            self.send_header('Set-Cookie', f'signal_visitor={self.new_cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000{secure}')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        visitor = self.visitor()
        if path == '/api/feed': return self.respond(200, get_feed(visitor))
        if path == '/api/health': return self.respond(200, {'ok': True})
        match = re.fullmatch(r'/api/articles/([a-f0-9]{24})/comments', path)
        if match:
            with connect() as conn:
                if not conn.execute('SELECT 1 FROM articles WHERE id=?', (match[1],)).fetchone():
                    return self.respond(404, {'error': 'Article not found.'})
                rows = conn.execute('SELECT id,name,body,created FROM comments WHERE article_id=? ORDER BY created DESC LIMIT 100', (match[1],)).fetchall()
                return self.respond(200, {'comments': [dict(r) for r in rows]})
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
        if path in files:
            name, mime = files[path]
            return self.respond(200, (ROOT/'static'/name).read_bytes(), mime)
        self.respond(404, {'error': 'Not found.'})

    def do_POST(self):
        # JSON + same-origin validation prevents cross-site writes with ambient cookies.
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            return self.respond(403, {'error': 'Cross-origin requests are not allowed.'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.respond(415, {'error': 'Send application/json.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > 8192: return self.respond(413, {'error': 'Request is too large or empty.'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict): raise ValueError()
        except (ValueError, UnicodeDecodeError): return self.respond(400, {'error': 'Invalid JSON.'})
        visitor = self.visitor()
        match = re.fullmatch(r'/api/articles/([a-f0-9]{24})/(vote|comments)', urlsplit(self.path).path)
        if not match: return self.respond(404, {'error': 'Not found.'})
        article_id, action = match.groups()
        with connect() as conn:
            article = conn.execute('SELECT * FROM articles WHERE id=?', (article_id,)).fetchone()
            if not article: return self.respond(404, {'error': 'Article not found.'})
            if action == 'vote':
                vote = data.get('value')
                if type(vote) is not int or vote not in (-1, 0, 1):
                    return self.respond(400, {'error': 'Vote must be -1, 0, or 1.'})
                if vote == 0: conn.execute('DELETE FROM votes WHERE article_id=? AND visitor=?', (article_id, visitor))
                else: conn.execute('''INSERT INTO votes VALUES (?,?,?,?) ON CONFLICT(article_id,visitor)
                                      DO UPDATE SET value=excluded.value,updated=excluded.updated''', (article_id, visitor, vote, time.time()))
                conn.commit()
                return self.respond(200, serialize_article(conn, article, visitor))
            name, body = data.get('name', ''), data.get('body', '')
            if not isinstance(name, str) or not isinstance(body, str):
                return self.respond(400, {'error': 'Name and comment must be text.'})
            name, body = name.strip(), body.strip()
            if not 1 <= len(name) <= 40 or not 1 <= len(body) <= 2000:
                return self.respond(400, {'error': 'Use a name of 1–40 characters and a comment of 1–2,000 characters.'})
            recent = conn.execute('SELECT COUNT(*) FROM comments WHERE visitor=? AND created>?', (visitor, time.time()-60)).fetchone()[0]
            if recent >= 5: return self.respond(429, {'error': 'Please wait a minute before commenting again.'})
            created = time.time()
            cursor = conn.execute('INSERT INTO comments(article_id,visitor,name,body,created) VALUES (?,?,?,?,?) RETURNING id', (article_id, visitor, name, body, created))
            comment_id = cursor.fetchone()[0]
            conn.commit()
            self.respond(201, {'id': comment_id, 'name': name, 'body': body, 'created': created})

    def log_message(self, fmt, *args): log.info(fmt, *args)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    init_db()
    if os.environ.get('DISABLE_FEEDS') != '1':
        threading.Thread(target=feed_worker, daemon=True).start()
    host, port = os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8000'))
    server = ThreadingHTTPServer((host, port), Handler)
    log.info('Signal is listening on %s:%s', host, port)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
