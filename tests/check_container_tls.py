"""Optional Docker TLS regression check; uses disposable containers and a local test CA.

Run after: docker build -t signal-news:deployment-test .
Requires Docker and the openssl command. Does not contact Neon or use real credentials.
"""
import json,os,pathlib,subprocess,tempfile,time
from urllib.request import urlopen
ENV=dict(os.environ)
def docker(*args):
 result=subprocess.run(['docker',*args],env=ENV,check=True,capture_output=True,text=True)
 return (result.stdout+ (result.stderr if args[0]=='logs' else '')).strip()
pg='signal-tls-postgres-test';web='signal-tls-app-test'
try:
 with tempfile.TemporaryDirectory(prefix='signal-tls-') as tmp:
  cert=str(pathlib.Path(tmp)/'server.crt');key=str(pathlib.Path(tmp)/'server.key')
  subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],check=True,capture_output=True)
  os.chmod(cert,0o644)
  docker('create','--name',pg,'--network','host','-e','POSTGRES_PASSWORD=local-test-only','-e','POSTGRES_DB=signal_test','-e','PGPORT=15432','postgres:17-alpine','sh','-c','chown postgres:postgres /tmp/server.key /tmp/server.crt && chmod 600 /tmp/server.key && exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key')
  docker('cp',cert,pg+':/tmp/server.crt');docker('cp',key,pg+':/tmp/server.key');docker('start',pg)
  for i in range(40):
   result=subprocess.run(['docker','exec',pg,'pg_isready','-h','127.0.0.1','-p','15432'],env=ENV,capture_output=True)
   if result.returncode==0:break
   time.sleep(.25)
  else:raise RuntimeError('TLS PostgreSQL did not become ready')
  url='postgresql://postgres:local-test-only@localhost:15432/signal_test?sslmode=verify-full&sslrootcert=/tmp/test-ca.crt'
  docker('create','--name',web,'--network','host','-e','DATABASE_URL='+url,'-e','PORT=18000','-e','DISABLE_FEEDS=1','-e','HOME=/root','signal-news:deployment-test')
  docker('cp',cert,web+':/tmp/test-ca.crt');docker('start',web)
  for i in range(40):
   try:
    with urlopen('http://127.0.0.1:18000/api/health',timeout=2) as response:assert json.load(response)['ok']
    break
   except OSError:time.sleep(.25)
  else:raise RuntimeError('App startup failed: '+docker('logs',web))
  result=docker('exec','--user','10001','-e','HOME=/home/signal',web,'python','-c',"import os,app; assert os.environ['HOME']=='/home/signal'; c=app.PostgresConnection(app.DATABASE_URL); assert c.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]; c.close(); print('TLS verified; unprivileged HOME correct')")
  with urlopen('http://127.0.0.1:18000/api/feed',timeout=5) as response:assert 'articles' in json.load(response)
  print('PASS: Docker app boots and serves the feed API over certificate-verified PostgreSQL TLS, with no client certificate needed. '+result)
finally:
 for name in [web,pg]:subprocess.run(['docker','rm','--force',name],env=ENV,capture_output=True)
