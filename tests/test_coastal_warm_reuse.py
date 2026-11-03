"""Regional cache reuse avoids global I/O without accepting incomplete cells."""
import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import posim_terrain.coastal_detail as detail
import posim_terrain.terrain_sources as sources


class WarmReuse(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.cells=[(2581,701),(2582,701)]
        self.enterContext(patch.object(detail,'root',return_value=self.root))
        self.enterContext(patch.object(detail,'keys',return_value=self.cells))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.spec=dict(latitude=35.07446,longitude=129.08468,width_m=2000,
                       height_m=2000,samples=129,source_policy='auto')
        for cell in self.cells:self.store(cell)

    def store(self,cell,status='ready'):
        folder=detail.directory(*cell);folder.mkdir(exist_ok=True)
        files=['land.json','satellite.png'] if status=='ready' else ['land.json']
        for name in files:(folder/name).write_text(name)
        value=dict(revision=detail.REVISION,cell=list(cell),status=status,
                   imagery_revision=detail.IMAGERY_REVISION,
                   sentinel_acquisition=datetime.now(timezone.utc).isoformat(),
                   hashes={name:detail.digest(folder/name) for name in files})
        (folder/'manifest.json').write_text(json.dumps(value))
        return folder,value

    def test_ready_and_no_land_cells_need_no_global_archive(self):
        self.store(self.cells[1],status='no_land')
        with patch.object(sources,'cached_download',side_effect=AssertionError('network')), \
                patch('zipfile.ZipFile',side_effect=AssertionError('global archive I/O')), \
                patch.object(detail,'warm_cell',side_effect=AssertionError('already ready')):
            detail.warm_region(self.spec)

    def test_invalid_cells_still_require_source_preparation(self):
        mutations=[lambda folder,m:(folder/'satellite.png').write_text('damaged'),
                   lambda folder,m:(folder/'land.json').unlink(),
                   lambda folder,m:m.update(revision='old'),
                   lambda folder,m:m.update(cell=[0,0]),
                   lambda folder,m:m.update(status='building'),
                   lambda folder,m:m.update(sentinel_acquisition='2020-01-01T00:00:00Z'),
                   lambda folder,m:m.update(hashes=['land.json','satellite.png']),
                   lambda folder,m:m['hashes'].update(extra='unexpected')]
        for index,mutate in enumerate(mutations):
            with self.subTest(case=index):
                folder,manifest=self.store(self.cells[1]);mutate(folder,manifest)
                (folder/'manifest.json').write_text(json.dumps(manifest))
                with patch.dict('os.environ',{'WWOS_COAST_OSM_ARCHIVE':str(self.root/'absent')}), \
                        patch.object(sources,'cached_download',side_effect=RuntimeError('source preparation')) as download:
                    with self.assertRaisesRegex(RuntimeError,'source preparation'):
                        detail.warm_region(self.spec)
                    self.assertEqual(download.call_count,1)

    def test_only_missing_cell_is_warmed_from_the_archive(self):
        (detail.directory(*self.cells[1])/'manifest.json').unlink()
        archive=self.root/'coast.zip'
        with zipfile.ZipFile(archive,'w') as output:
            for name in ('land.shp','land.shx','land.dbf'):output.writestr('land/'+name,name)
        def warm(i,j,*,coast_dataset):
            self.assertEqual((i,j),self.cells[1])
            self.assertEqual(Path(coast_dataset).read_text(),'land.shp')
            self.store((i,j))
        with patch.dict('os.environ',{'WWOS_COAST_OSM_ARCHIVE':str(archive)}), \
                patch.object(detail,'warm_cell',side_effect=warm) as warmer, \
                patch.object(sources,'cached_download',side_effect=AssertionError('archive exists')):
            detail.warm_region(self.spec)
            self.assertEqual(warmer.call_count,1)
        self.assertTrue(all(detail.cell_is_current(*cell) for cell in self.cells))


if __name__=='__main__':unittest.main()
