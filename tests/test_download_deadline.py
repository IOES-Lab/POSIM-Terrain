import hashlib,tempfile,time,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import posim_terrain.terrain_sources as sources

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200)
        if self.path=='/truncated':self.send_header('Content-Length','32768')
        self.end_headers()
        try:
            if self.path=='/drip':
                for _ in range(50):self.wfile.write(b'x'*1024);self.wfile.flush();time.sleep(.04)
            elif self.path=='/stall':time.sleep(1)
            elif self.path=='/tiny':self.wfile.write(b'x')
            elif self.path=='/truncated':self.wfile.write(b'x'*32)
            else:self.wfile.write(b'z'*32768)
        except (BrokenPipeError,ConnectionResetError):pass

class DownloadDeadline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        p=patch.object(sources,'cache_root',return_value=self.root);p.start();self.addCleanup(p.stop)
    def test_trickle_checks_deadline_before_full_block(self):
        start=time.monotonic()
        with self.assertRaisesRegex(ValueError,'source_download_deadline'):sources.cached_download(self.url+'/drip','data',100000,.25)
        self.assertLess(time.monotonic()-start,.9)
        self.assertEqual(list(self.root.iterdir()),[])
    def test_stalled_socket_is_bounded_and_partial_removed(self):
        start=time.monotonic()
        with self.assertRaises(TimeoutError):sources.cached_download(self.url+'/stall','data',100000,.25)
        self.assertLess(time.monotonic()-start,.9);self.assertEqual(list(self.root.iterdir()),[])
    def test_good_bytes_hash_and_cache_reuse(self):
        path,meta=sources.cached_download(self.url+'/good','data',40000,2)
        self.assertEqual(path.read_bytes(),b'z'*32768);self.assertEqual(meta['sha256'],hashlib.sha256(b'z'*32768).hexdigest())
        self.assertEqual(sources.cached_download('http://127.0.0.1:1/no-network','data',40000,2)[1],meta)
    def test_oversized_response_is_not_cached(self):
        with self.assertRaisesRegex(ValueError,'source_download_limit'):sources.cached_download(self.url+'/good','data',100,2)
        self.assertEqual(list(self.root.iterdir()),[])
    def test_tiny_response_is_not_cached(self):
        with self.assertRaisesRegex(ValueError,'empty_source_response'):sources.cached_download(self.url+'/tiny','data',100,2)
        self.assertEqual(list(self.root.iterdir()),[])
    def test_truncated_response_is_not_cached(self):
        with self.assertRaisesRegex(ValueError,'incomplete_source_response'):sources.cached_download(self.url+'/truncated','data',40000,2)
        self.assertEqual(list(self.root.iterdir()),[])
    def test_format_validation_precedes_cache_publish(self):
        def reject(path):raise ValueError('invalid_source_format')
        with self.assertRaisesRegex(ValueError,'invalid_source_format'):sources.cached_download(self.url+'/good','data',40000,2,validator=reject)
        self.assertEqual(list(self.root.iterdir()),[])
    def test_invalid_existing_cache_is_preserved_but_rejected(self):
        path=self.root/'data';path.write_bytes(b'bad cache retained for diagnosis')
        def reject(path):raise ValueError('invalid_source_format')
        with self.assertRaisesRegex(ValueError,'invalid_source_format'):sources.cached_download(self.url+'/good','data',40000,2,validator=reject)
        self.assertEqual(path.read_bytes(),b'bad cache retained for diagnosis')

if __name__=='__main__':unittest.main(verbosity=2)
