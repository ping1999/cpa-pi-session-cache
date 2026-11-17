import base64
import concurrent.futures
import ctypes as C
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = '__pi_cpa_cache_session_v1'

class Buffer(C.Structure):
    _fields_ = [('ptr', C.c_void_p), ('len', C.c_size_t)]
CALL = C.CFUNCTYPE(C.c_int, C.c_char_p, C.c_void_p, C.c_size_t, C.POINTER(Buffer))
FREE = C.CFUNCTYPE(None, C.c_void_p, C.c_size_t)
SHUTDOWN = C.CFUNCTYPE(None)
class Host(C.Structure):
    _fields_ = [('abi_version', C.c_uint32), ('ctx', C.c_void_p), ('call', C.c_void_p), ('free', C.c_void_p)]
class Plugin(C.Structure):
    _fields_ = [('abi_version', C.c_uint32), ('call', CALL), ('free', FREE), ('shutdown', SHUTDOWN)]

class Harness:
    def __init__(self):
        self.lib = C.CDLL(str(ROOT/'dist/pi-cpa-session-cache.so'))
        self.host = Host(1, None, None, None)
        self.plugin = Plugin()
        self.lib.cliproxy_plugin_init.argtypes = [C.POINTER(Host), C.POINTER(Plugin)]
        assert self.lib.cliproxy_plugin_init(C.byref(self.host), C.byref(self.plugin)) == 0
    def call(self, method, req=None):
        raw = json.dumps(req or {}).encode()
        data = C.create_string_buffer(raw)
        out = Buffer()
        rc = self.plugin.call(method.encode(), data, len(raw), C.byref(out))
        assert rc == 0, rc
        try:
            return json.loads(C.string_at(out.ptr, out.len))
        finally:
            self.plugin.free(out.ptr, out.len)
    def body_call(self, method, obj, **kwargs):
        return self.call(method, dict(Body=base64.b64encode(json.dumps(obj).encode()).decode(), **kwargs))
    def after(self, sid='child-123', obj=None, **kwargs):
        args = dict(SourceFormat='openai', ToFormat='antigravity', Headers={'X-Pi-Cache-Session':[sid]})
        args.update(kwargs)
        return self.body_call('request.intercept_after', obj or {'messages':[{'role':'user','content':'hello'}]}, **args)
    def normalize(self, obj, **kwargs):
        args = dict(FromFormat='openai', ToFormat='antigravity')
        args.update(kwargs)
        return self.body_call('request.normalize', obj, **args)

def body(result):
    return json.loads(base64.b64decode(result['result']['Body']))
def expected(sid):
    d = hashlib.sha256(('pi-cpa-session-cache:v1:'+sid).encode()).digest()
    n = int.from_bytes(d[:8], 'big') & ((1<<63)-1)
    return '-'+str(n or 1)

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.h = Harness()
    def test_register(self):
        r=self.h.call('plugin.register')['result']
        self.assertEqual(r['metadata']['Version'],'0.1.0')
        self.assertTrue(r['capabilities']['request_normalizer'])
    def test_hash_vectors_and_boundaries(self):
        for sid in ['a','abc','x'*30,'x'*31,'x'*32,'x'*128,'ABC:._-123']:
            r=body(self.h.after(sid))
            self.assertEqual(r['generationConfig'][BRIDGE],expected(sid))
    def test_messages_and_options_preserved(self):
        obj={'messages':[{'role':'user','content':'\u4e2d\u6587\nhello'}], 'temperature':0.7,'generationConfig':{'topK':12}}
        r=body(self.h.after(obj=obj))
        self.assertEqual(r['messages'],obj['messages'])
        self.assertEqual(r['generationConfig']['topK'],12)
        translated={'request':{'generationConfig':r['generationConfig'],'contents':[{'role':'user','parts':[{'text':'hello'}]}],'sessionId':'old'}}
        final=body(self.h.normalize(translated))
        self.assertEqual(final['request']['sessionId'],expected('child-123'))
        self.assertEqual(final['request']['generationConfig'],{'topK':12})
        self.assertEqual(final['request']['contents'],translated['request']['contents'])
        self.assertNotIn(BRIDGE,json.dumps(final))
    def test_snake_case_generation_config(self):
        obj={'messages':[], 'generation_config':{'topK':7}}
        r=body(self.h.after(obj=obj))
        self.assertNotIn('generationConfig',r)
        self.assertEqual(r['generation_config']['topK'],7)
        self.assertIn(BRIDGE,r['generation_config'])
    def test_no_optin_no_change(self):
        self.assertEqual(self.h.after(Headers={})['result'],{})
        self.assertEqual(self.h.normalize({'request':{'contents':[]}})['result'],{})
    def test_wrong_target_no_change(self):
        self.assertEqual(self.h.after(ToFormat='codex')['result'],{})
        self.assertEqual(self.h.normalize({'request':{}},ToFormat='codex')['result'],{})
    def test_case_insensitive_header(self):
        r=body(self.h.after(Headers={'x-pi-cache-session':['child-123']}))
        self.assertEqual(r['generationConfig'][BRIDGE],expected('child-123'))
    def test_bad_ids_rejected(self):
        for sid in ['', 'x'*129, '\u4e2d\u6587', 'a\nb','a b','a/b']:
            self.assertTrue(self.h.after(sid)['result']['Terminate'])
    def test_duplicate_headers_rejected(self):
        for headers in [{'X-Pi-Cache-Session':['a','b']}, {'X-Pi-Cache-Session':['a'],'x-pi-cache-session':['b']}]:
            self.assertTrue(self.h.after(Headers=headers)['result']['Terminate'])
    def test_wrong_source_rejected_when_opted_in(self):
        self.assertTrue(self.h.after(SourceFormat='claude')['result']['Terminate'])
    def test_invalid_config_and_reserved_key_rejected(self):
        for gc in [None, [], 'bad', {BRIDGE:'fake'}]:
            self.assertTrue(self.h.after(obj={'generationConfig':gc})['result']['Terminate'])
    def test_malformed_rpc_and_base64(self):
        for value in ['!!!!','YWJj=', 'AA==']:
            r=self.h.call('request.intercept_after',{'SourceFormat':'openai','ToFormat':'antigravity','Headers':{'X-Pi-Cache-Session':['a']},'Body':value})
            self.assertTrue(r['result']['Terminate'])
    def test_parallel_no_shared_state(self):
        def run(i):
            sid=f'session-{i}'
            for _ in range(10):
                marked=body(self.h.after(sid))
                result=body(self.h.normalize({'request':{'generationConfig':marked['generationConfig']}}))
                self.assertEqual(result['request']['sessionId'],expected(sid))
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(run,range(100)))

if __name__ == '__main__': unittest.main()
