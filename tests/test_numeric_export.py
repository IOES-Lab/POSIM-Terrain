"""Numeric exports preserve terrain and provenance without constructing worlds."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from posim_terrain.terrain import build

class NumericExport(unittest.TestCase):
    def test_same_numeric_grid_without_mesh_or_sdf(self):
        spec=dict(latitude=22.,longitude=114.,width_m=2000.,height_m=2000.,samples=33,source_policy='gebco')
        def source(lon,lat,out):
            return (np.full(lon.shape,-10.),np.array([21.,23.]),np.array([113.,115.]),
                    np.full((2,2),-10.),[dict(url='fixture',requested_url='fixture')],[b'fixture'])
        with tempfile.TemporaryDirectory() as temp,patch('posim_terrain.terrain.gebco_grid',side_effect=source),patch('posim_terrain.terrain.write_mesh') as mesh:
            folder=Path(temp)
            meta=build(spec,folder,engine_world=False,validate_sdf=False,numeric_only=True)
            mesh.assert_not_called()
            self.assertFalse((folder/'world.sdf').exists())
            with np.load(folder/'projected.npz') as grid:self.assertTrue((grid['elevation']==-10.).all())
            self.assertEqual(meta['source_counts']['GEBCO_2026'],33**2)
            self.assertFalse(json.loads((folder/'manifest.json').read_text())['engine_world'])
    def test_numeric_export_cannot_claim_engine_validation(self):
        with self.assertRaisesRegex(ValueError,'numeric_only_requires'):
            build({},Path('/unused'),numeric_only=True)

if __name__=='__main__':unittest.main()
