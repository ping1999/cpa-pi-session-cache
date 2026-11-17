#!/usr/bin/env python3
"""Run an unmodified CPA Docker image with this plugin and a loopback mock.
No production mounts, real upstream tokens, or Google generation requests.
Requires Docker on Linux (host networking); does not build CPA.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get('CPA_TEST_IMAGE', 'eceasy/cli-proxy-api:latest')
MODEL = 'claude-sonnet-4-6'

def expected(s):
    n = int.from_bytes(hashlib.sha256(('pi-cpa-session-cache:v1:'+s).encode()).digest()[:8], 'big') & ((1<<63)-1)
    return '-'+str(n or 1)

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

captures = []
lock = threading.Lock()
class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if 'fetchAvailableModels' in self.path:
            response = {'models':{MODEL:{}, 'gemini-3-flash':{}}}
        else:
            with lock: captures.append(body)
            response = {'response':{'candidates':[{'content':{'role':'model','parts':[{'text':'mock-ok'}]},'finishReason':'STOP'}], 'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':2,'totalTokenCount':12}}}
        stream = 'streamGenerateContent' in self.path
        raw = (('data: '+json.dumps(response)+'\n\n') if stream else json.dumps(response)).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if stream else 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        try: self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError): pass  # server shutting down during a catalog refresh
    def log_message(self, *args): pass

def main():
    if not (ROOT/'dist/pi-cpa-session-cache.so').exists(): raise SystemExit('Run make first')
    port = free_port()
    server = ThreadingHTTPServer(('127.0.0.1',0),Mock)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    name = 'pi-cpa-session-test-'+str(os.getpid())
    with tempfile.TemporaryDirectory(prefix='pi-cpa-integration-') as tmp:
        tmp = Path(tmp)
        (tmp/'auths').mkdir()
        (tmp/'plugins').mkdir()
        (tmp/'plugins/pi-cpa-session-cache.so').write_bytes((ROOT/'dist/pi-cpa-session-cache.so').read_bytes())
        auth = {'type':'antigravity','email':'mock@example.invalid','access_token':'fake-loopback-only', 'expired':'2099-01-01T00:00:00Z','project_id':'mock-project','base_url':f'http://127.0.0.1:{server.server_port}', 'antigravity_capability_hints':{'endpoint':f'http://127.0.0.1:{server.server_port}','project_id':'mock-project','model_ids':[MODEL,'gemini-3-flash']}}
        (tmp/'auths/mock.json').write_text(json.dumps(auth))
        config = f'''host: "127.0.0.1"
port: {port}
auth-dir: "/probe/auths"
api-keys: ["isolated-test"]
remote-management:
  disable-control-panel: true
request-retry: 0
plugins:
  enabled: true
  dir: "/probe/plugins"
  configs:
    pi-cpa-session-cache:
      enabled: true
      priority: 100
'''
        (tmp/'config.yaml').write_text(config)
        base = f'http://127.0.0.1:{port}'
        def request(path, data=None, headers=None):
            h={'Authorization':'Bearer isolated-test','Content-Type':'application/json'}
            h.update(headers or {})
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
            with urllib.request.urlopen(req,timeout=30) as r: return r.read().decode()
        def send(sid=None, stream=False, text='identical first user', model=MODEL):
            headers={'X-Session-ID':sid or 'unmarked-client'}
            if sid is not None: headers['X-Pi-Cache-Session']=sid
            response=request('/v1/chat/completions',{'model':model,'messages':[{'role':'user','content':text}],'stream':stream,'generationConfig':{'temperature':0.4}},headers)
            assert 'mock-ok' in response,response
        try:
            subprocess.run(['docker','run','-d','--name',name,'--network','host','-v',f'{tmp}:/probe','-w','/CLIProxyAPI',IMAGE,'./CLIProxyAPI','-config','/probe/config.yaml','-local-model'],check=True,stdout=subprocess.DEVNULL)
            for _ in range(60):
                try:
                    if MODEL in request('/v1/models'): break
                except (urllib.error.URLError, ConnectionError): pass
                time.sleep(0.5)
            else: raise AssertionError('CPA startup/model discovery timeout')
            logs=subprocess.check_output(['docker','logs',name],stderr=subprocess.STDOUT).decode()
            assert 'plugin registered plugin_id=pi-cpa-session-cache' in logs,logs[-4000:]
            for model in [MODEL,'gemini-3-flash']:
                start=len(captures)
                for sid,stream in [('parent',False),('child',True),('child',False),('parent',True)]: send(sid,stream,model=model)
                rows=captures[start:]
                assert [x['request']['sessionId'] for x in rows]==[expected('parent'),expected('child'),expected('child'),expected('parent')]
                for row in rows:
                    assert row['request']['contents'][0]['parts'][0]['text']=='identical first user'
                    assert row['request']['generationConfig']['temperature']==0.4
                    assert '__pi_cpa_cache_session_v1' not in json.dumps(row)
                print('PASS',model,'fork separation, stream/nonstream stability, unchanged messages/options, no bridge leak')
            start=len(captures)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda i:send('concurrent-'+str(i),bool(i%2)),range(20)))
            rows=captures[start:]
            assert len(rows)==20
            assert {x['request']['sessionId'] for x in rows}=={expected('concurrent-'+str(i)) for i in range(20)}
            print('PASS 20 simultaneous sessions without cross-talk')
            send('child',text='changed first user')
            assert captures[-1]['request']['sessionId']==expected('child')
            print('PASS first-user text does not override opted-in ID')
            start=len(captures)
            send();send(stream=True)
            assert captures[start]['request']['sessionId']==captures[start+1]['request']['sessionId']
            print('PASS unmarked requests retain native behavior')
            before=len(captures)
            try: send('invalid session')
            except urllib.error.HTTPError as e: assert e.code==400
            else: raise AssertionError('Invalid ID was not rejected')
            assert len(captures)==before
            print('PASS invalid opted-in ID rejected before upstream')
            subprocess.run(['docker','stop',name],check=True,stdout=subprocess.DEVNULL)
            (tmp/'config.yaml').write_text(config.replace('plugins:\n  enabled: true','plugins:\n  enabled: false'))
            subprocess.run(['docker','start',name],check=True,stdout=subprocess.DEVNULL)
            for _ in range(60):
                try:
                    if MODEL in request('/v1/models'):break
                except (urllib.error.URLError,ConnectionError):pass
                time.sleep(0.5)
            start=len(captures);send('parent');send('child')
            assert captures[start]['request']['sessionId']==captures[start+1]['request']['sessionId']
            print('PASS control: disabled plugin reproduces identical upstream ID despite different headers')
        finally:
            subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            server.shutdown();server.server_close()
if __name__=='__main__':main()
