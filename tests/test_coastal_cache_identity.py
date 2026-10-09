"""An unrelated atlas download must not invalidate a ready terrain region."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import posim_terrain.coastal_detail as detail
from posim_terrain.bathymetry import config_fingerprint
from posim_terrain.terrain import projection
import numpy as np


class RegionalCache(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        mocked=patch.object(detail,'root',return_value=self.root)
        mocked.start();self.addCleanup(mocked.stop)
        self.spec=dict(latitude=35.07446,longitude=129.08468,width_m=2000,height_m=2000,samples=129,source_policy='auto')
        _,_,inverse=projection(self.spec)
        lon,lat=inverse.transform(*np.meshgrid(np.linspace(-1000,1000,129),np.linspace(-1000,1000,129)))
        self.cells=detail.keys(lon,lat)

    def store(self,cell,image='image-one'):
        folder=detail.directory(*cell);folder.mkdir(exist_ok=True)
        (folder/'manifest.json').write_text(json.dumps(dict(revision=detail.REVISION,status='ready',hashes={'land.json':'coast','satellite.png':image})))

    def test_unrelated_warming_preserves_the_region_identity(self):
        for cell in self.cells:self.store(cell)
        before=config_fingerprint(self.spec)
        self.store((0,0));self.store((0,0),'refreshed-image')
        self.assertEqual(config_fingerprint(self.spec),before)

    def test_relevant_refresh_and_newly_available_data_require_regeneration(self):
        missing=config_fingerprint(self.spec)
        self.store(self.cells[0]);ready=config_fingerprint(self.spec)
        self.assertNotEqual(missing,ready)
        self.store(self.cells[0],'refreshed-image')
        self.assertNotEqual(config_fingerprint(self.spec),ready)
        (detail.directory(*self.cells[0])/'manifest.json').unlink()
        self.assertEqual(config_fingerprint(self.spec),missing)

    def test_disabled_and_gebco_regions_ignore_unused_atlas_data(self):
        for spec,env in ((dict(self.spec,source_policy='gebco'),{}),(self.spec,{'WWOS_COASTAL_DETAIL':'0'})):
            with patch.dict('os.environ',env):
                before=config_fingerprint(spec)
                self.store(self.cells[0],'new-image')
                self.assertEqual(config_fingerprint(spec),before)

    def test_internal_stream_window_uses_its_actual_coordinates(self):
        spec=dict(self.spec,_stream_grid={'xs':[-100,0,100,200,300],'ys':[-100,0,100,200,300]})
        before=config_fingerprint(spec)
        self.store(self.cells[0]);self.assertNotEqual(config_fingerprint(spec),before)


if __name__=='__main__':unittest.main()
