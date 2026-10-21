import concurrent.futures
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPCookieProcessor, Request

import app


def rss(title='A new AI SDK release', url='https://example.com/story', date=None):
    date = date or time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())
    return f'''<rss version="2.0"><channel><item><title>{title}</title><link>{url}</link><description>&lt;p&gt;Independent &amp;amp; useful reporting.&lt;/p&gt;</description><pubDate>{date}</pubDate></item></channel></rss>'''.encode()


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_path = app.DB_PATH
        app.DB_PATH = Path(cls.temp.name)/'test.sqlite3'
        app.init_db()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        app.DB_PATH = cls.old_path
        cls.temp.cleanup()

    def setUp(self):
        with app.connect() as conn:
            for table in ['votes', 'comments', 'articles', 'source_status']:
                conn.execute(f'DELETE FROM {table}')
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.other = build_opener(HTTPCookieProcessor(CookieJar()))

    def ingest(self, source=None, data=None):
        source = source or app.SOURCES[0]
        rows = app.parse_feed(data or rss(), source)
        with app.connect() as conn:
            for r in rows:
                conn.execute('INSERT OR IGNORE INTO articles VALUES (:id,:title,:url,:description,:source,:kind,:category,:published,:fetched)', r)
        return rows

    def request(self, path, data=None, client=None, headers=None):
        request = Request(self.base+path, data=json.dumps(data).encode() if data is not None else None,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with (client or self.client).open(request) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def test_live_ingestion_refresh_and_partial_failure_preserves_cache(self):
        def fetch(source):
            return app.parse_feed(rss(title=source['name']+' AI SDK release',url='https://example.com/'+source['name'].replace(' ', '-')), source)
        with patch.object(app, 'fetch_source', side_effect=fetch): app.refresh_feeds()
        status, feed = self.request('/api/feed')
        self.assertEqual(status, 200)
        self.assertEqual(len(feed['articles']), 2)
        self.assertIsNotNone(feed['deep_read'])
        self.assertTrue(all(s['success'] and not s['error'] for s in feed['sources']))
        with patch.object(app, 'fetch_source', side_effect=OSError('Network blocked')): app.refresh_feeds()
        _, cached = self.request('/api/feed')
        self.assertEqual(len(cached['articles']), 2)
        self.assertEqual(len([s for s in cached['sources'] if s['error']]), 4)
        self.assertEqual(cached['last_updated'], feed['last_updated'])

    def test_top_ten_dedup_recency_diversity_and_deep_read(self):
        now=time.time()
        for i in range(15):
            self.ingest(app.SOURCES[i % 2], rss(title=f'AI SDK release story {i}',url=f'https://example.com/{i}',
                         date=time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now-i*3600))))
        self.ingest(data=rss(title='Too old',url='https://example.com/old',date='Mon, 01 Jan 2001 00:00:00 GMT'))
        self.ingest(app.SOURCES[2],rss(title='Thoughtful analysis of AI interface prototyping',url='https://example.com/deep'))
        self.ingest(app.SOURCES[1],rss(title='AI SDK release story 0',url='https://example.com/duplicate'))
        _, feed=self.request('/api/feed')
        self.assertEqual(len(feed['articles']),10)
        self.assertEqual(len({a['title'] for a in feed['articles']}),10)
        self.assertNotIn('Too old',[a['title'] for a in feed['articles']])
        self.assertEqual(feed['deep_read']['title'],'Thoughtful analysis of AI interface prototyping')
        self.assertEqual(len({a['source'] for a in feed['articles'][:2]}),2)

    def test_audience_priority_overrides_recency_and_publisher_diversity(self):
        titles = ['New AI interface prototyping techniques', 'An AI SDK for developers',
                  'Pricing and user adoption for an AI product',
                  'How to build and evaluate multimodal AI assistants']
        now = time.time()
        for i, title in enumerate(titles):
            # The all-three item is oldest; the designer item is newest.
            self.ingest(app.SOURCES[i % 2], rss(title=title, url=f'https://example.com/role-{i}',
                        date=time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime(now-i*86400))))
        self.ingest(data=rss(title='AI startup raises billions ahead of IPO', url='https://example.com/funding'))
        self.ingest(data=rss(title='Election politics and the future of AI', url='https://example.com/politics'))
        _, feed = self.request('/api/feed')
        self.assertEqual([a['audience'] for a in feed['articles']],
                         ['All three', 'PMs', 'Developers', 'Designers'])
        self.assertEqual(feed['articles'][0]['audiences'], ['PMs', 'Developers', 'Designers'])
        self.assertTrue(all(a['why_it_matters'] for a in feed['articles']))
        self.assertEqual(len(feed['articles']), 4)  # No backfill with generic coverage.

    def test_two_disciplines_do_not_become_all_three(self):
        self.ingest(data=rss(title='AI API pricing for developers', url='https://example.com/two'))
        _, feed = self.request('/api/feed')
        article = feed['articles'][0]
        self.assertEqual(article['audience'], 'PMs')
        self.assertEqual(article['audiences'], ['PMs', 'Developers'])

    def test_all_three_requires_shared_practical_evidence(self):
        self.ingest(data=rss(title='AI agents funding round and investors', url='https://example.com/hype'))
        self.ingest(app.SOURCES[2], rss(title='AI investment market outlook', url='https://example.com/deep-generic'))
        _, feed = self.request('/api/feed')
        self.assertEqual(feed['articles'], [])
        self.assertIsNone(feed['deep_read'])

    def test_votes_switch_remove_and_remember_per_browser(self):
        article=self.ingest()[0]
        endpoint=f"/api/articles/{article['id']}/vote"
        self.request('/api/feed')  # Establish the browser cookie before voting.
        self.other.open(self.base+'/api/feed').close()
        _, vote=self.request(endpoint,{'value':1})
        self.assertEqual((vote['up'],vote['down'],vote['my_vote']),(1,0,1))
        _, vote=self.request(endpoint,{'value':1})
        self.assertEqual(vote['up'],1)
        self.request(endpoint,{'value':1},client=self.other)
        _, vote=self.request(endpoint,{'value':-1})
        self.assertEqual((vote['up'],vote['down'],vote['my_vote']),(1,1,-1))
        _, feed=self.request('/api/feed')
        self.assertEqual(feed['articles'][0]['my_vote'],-1)
        _, vote=self.request(endpoint,{'value':0})
        self.assertEqual((vote['up'],vote['down'],vote['my_vote']),(1,0,0))

    def test_comments_shared_persisted_and_counted(self):
        article=self.ingest()[0]
        endpoint=f"/api/articles/{article['id']}/comments"
        text='<script>alert("not executable")</script> A perspective.'
        status, posted=self.request(endpoint,{'name':'Reader','body':text})
        self.assertEqual(status,201)
        _, comments=self.request(endpoint,client=self.other)
        self.assertEqual(comments['comments'][0]['body'],text)
        self.assertNotIn('visitor',comments['comments'][0])
        _, feed=self.request('/api/feed')
        self.assertEqual(feed['articles'][0]['comments'],1)
        with app.connect() as conn:
            self.assertEqual(conn.execute('SELECT body FROM comments WHERE id=?',(posted['id'],)).fetchone()[0],text)

    def test_invalid_input_cross_origin_and_missing_article(self):
        article=self.ingest()[0]
        base=f"/api/articles/{article['id']}"
        for value in [True,'1',2,None]:
            self.assertEqual(self.request(base+'/vote',{'value':value})[0],400)
        for data in [{'name':'','body':'hi'},{'name':'Me','body':'a'*2001},{'name':[], 'body':'hi'}]:
            self.assertEqual(self.request(base+'/comments',data)[0],400)
        self.assertEqual(self.request(base+'/vote',{'value':1},headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/articles/'+'0'*24+'/vote',{'value':1})[0],404)
        self.assertEqual(self.request(base+'/vote',{'value':1},headers={'Content-Type':'text/plain'})[0],415)

    def test_comment_rate_limit(self):
        article=self.ingest()[0]
        endpoint=f"/api/articles/{article['id']}/comments"
        self.request('/api/feed')
        for _ in range(5): self.assertEqual(self.request(endpoint,{'name':'Reader','body':'A thought'})[0],201)
        self.assertEqual(self.request(endpoint,{'name':'Reader','body':'One too many'})[0],429)

    def test_empty_feed_reports_unavailable_not_fake_news(self):
        _, feed=self.request('/api/feed')
        self.assertEqual(feed['articles'],[])
        self.assertIsNone(feed['deep_read'])
        self.assertIsNone(feed['last_updated'])
        self.assertTrue(feed['stale'])

    def test_atom_and_html_cleaning(self):
        data=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>AI &amp; society</title><link rel="self" href="https://example.com/api"/><link href="https://example.com/read"/><summary>&lt;script&gt;bad()&lt;/script&gt;&lt;p&gt;Useful analysis&lt;/p&gt;</summary><updated>2026-10-20T10:00:00Z</updated></entry></feed>'''
        article=app.parse_feed(data,app.SOURCES[0])[0]
        self.assertEqual(article['url'],'https://example.com/read')
        self.assertEqual(article['description'],'Useful analysis')
        self.assertEqual(article['title'],'AI & society')

    def test_invalid_or_unsafe_feed_entries(self):
        self.assertEqual(app.parse_feed(rss(url='javascript:alert(1)'),app.SOURCES[0]),[])
        self.assertEqual(app.parse_feed(rss(date='no date'),app.SOURCES[0]),[])
        with self.assertRaises(ValueError): app.parse_feed(b'<html/>',app.SOURCES[0])
        with self.assertRaises(ValueError): app.parse_feed(b'<!DOCTYPE rss><rss/>',app.SOURCES[0])

    def test_static_page_security_headers_and_no_path_traversal(self):
        with self.client.open(self.base+'/') as response:
            self.assertIn('Less noise.',response.read().decode())
            self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
            self.assertIn('HttpOnly',response.headers['Set-Cookie'])
        self.assertEqual(self.request('/../app.py')[0],404)


if __name__=='__main__': unittest.main()
