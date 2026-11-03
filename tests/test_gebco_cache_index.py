"""Large subset caches must not re-read every sidecar for each terrain tile."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from posim_terrain.gebco_cache import metadata_candidates
from posim_terrain.terrain import SOURCE, gebco_source
from posim_terrain.terrain_sources import cache_root


def url(window, source=SOURCE):
    a, b, c, d = window
    return source + '.ascii?' + quote(f'elevation[{a}:1:{b}][{c}:1:{d}]', safe=':')


def ascii_grid(window):
    a, b, c, d = window
    rows, cols = b-a+1, d-c+1
    lines = ['fixture', '---------------------------------------------', f'elevation.elevation[{rows}][{cols}]']
    lines += [f'[{i}], ' + ', '.join(str(-100+i*10+j) for j in range(cols)) for i in range(rows)]
    lines += ['', f'elevation.lat[{rows}]', ', '.join(str(-90+(i+.5)/240) for i in range(a, b+1)),
              '', f'elevation.lon[{cols}]', ', '.join(str(-180+(i+.5)/240) for i in range(c, d+1))]
    return ('\n'.join(lines)+'\n').encode()


class CacheIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.assets = Path(temporary.name)
        env = patch.dict(os.environ, WWOS_ASSETS_DIR=str(self.assets), WWOS_DATA_DIR=str(self.assets/'worker'))
        env.start()
        self.addCleanup(env.stop)
        self.root = cache_root()
        self.request = (30001, 30003, 73001, 73003)
        self.cover = (30000, 30004, 73000, 73004)

    def metadata(self, name, window, **extra):
        path = self.root / ('gebco-2026-'+name+'.ascii.json')
        path.write_text(json.dumps({'url': url(window), **extra}))
        return path

    def store(self, window, *, valid=True):
        address = url(window)
        name = hashlib.sha256(address.encode()).hexdigest()[:24]
        data = self.root / ('gebco-2026-'+name+'.ascii')
        raw = ascii_grid(window)
        data.write_bytes(raw)
        self.metadata(name, window, sha256=hashlib.sha256(raw).hexdigest() if valid else '0'*64)
        return data, raw

    def test_warm_lookup_does_not_read_unrelated_sidecars(self):
        _, raw = self.store(self.cover)
        for i in range(200):
            self.metadata('unrelated-'+str(i), (10+i, 12+i, 30, 32))
        with patch('urllib.request.urlopen', side_effect=ConnectionError('offline')) as network:
            self.assertEqual(gebco_source(url(self.request), self.request)[0], raw)
            reads = []
            original = Path.read_text
            def read(path, *args, **kwargs):
                if path.parent == self.root and path.name.endswith('.ascii.json'):
                    reads.append(path.name)
                return original(path, *args, **kwargs)
            with patch.object(Path, 'read_text', read):
                again = gebco_source(url(self.request), self.request)
            self.assertEqual((again[0], again[4]), (raw, url(self.cover)))
            self.assertLessEqual(len(reads), 4, 'A warm tile must not scan every provider sidecar')
            self.assertFalse(any('unrelated' in name for name in reads))
            network.assert_not_called()

    def test_append_change_delete_and_legacy_names_refresh(self):
        old = self.metadata('legacy-name', self.cover)
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [old])
        new = self.metadata('new', self.cover)
        self.assertEqual(set(metadata_candidates(self.root, SOURCE, self.request)), {old, new})
        old.write_text(json.dumps({'url': url((1, 2, 3, 4))}))
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [new])
        new.unlink()
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [])

    def test_other_release_and_partial_coverage_are_not_candidates(self):
        self.metadata('partial', (30000, 30002, 73000, 73002))
        self.metadata('other', self.cover, url=url(self.cover, SOURCE.replace('2026', '2025')))
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [])

    def test_changed_corrupt_sidecar_can_be_repaired(self):
        path = self.metadata('repair', self.cover)
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [path])
        path.write_text('{broken')
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [])
        path.write_text(json.dumps({'url': url(self.cover)}))
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [path])

    def test_corrupt_index_rebuilds_and_source_files_stay_unchanged(self):
        path = self.metadata('safe', self.cover)
        before = path.read_bytes()
        index = self.root/'gebco-cache-index-v1.sqlite'
        index.write_bytes(b'not a SQLite database')
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [path])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(list(self.root.glob(index.name+'.rejected-*'))), 1)
        self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [path])

    def test_unwritable_index_uses_the_original_verified_scan(self):
        path = self.metadata('fallback', self.cover)
        with patch('posim_terrain.gebco_cache.sqlite3.connect', side_effect=PermissionError('read only')):
            self.assertEqual(metadata_candidates(self.root, SOURCE, self.request), [path])

    def test_index_does_not_approve_a_tampered_small_candidate(self):
        self.store(self.request, valid=False)
        _, raw = self.store(self.cover)
        with patch('urllib.request.urlopen', side_effect=ConnectionError('offline')) as network:
            result = gebco_source(url(self.request), self.request)
        self.assertEqual((result[0], result[4]), (raw, url(self.cover)))
        network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
