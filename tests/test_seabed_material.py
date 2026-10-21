"""Offline texture identity, physical UV scale and unchanged collision data."""
from pathlib import Path
import hashlib,json,tempfile,unittest,xml.etree.ElementTree as ET
import numpy as np
from PIL import Image
from posim_terrain.seabed_visual import SEABED_ASSETS,SEABED_SCALE_M,decorate_link,texture,sand_assets
class SeabedMaterialTests(unittest.TestCase):
 def test_packaged_maps_match_attribution_and_hashes(self):
  manifest=json.loads((SEABED_ASSETS/'manifest.json').read_text())
  self.assertEqual(manifest['license'],'CC0-1.0');self.assertEqual(manifest['scale_metres'],SEABED_SCALE_M)
  for asset in manifest['files'].values():
   path=SEABED_ASSETS/asset['file'];self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),asset['sha256'])
   with Image.open(path) as image:self.assertEqual(image.size,(2048,2048))
 def test_sand_has_no_baked_grid_and_material_keeps_collision(self):
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp);texture(out/'sand.png')
   with Image.open(out/'sand.png') as actual,Image.open(SEABED_ASSETS/'aerial_beach_01_diff_2k.jpg') as expected:np.testing.assert_array_equal(actual,expected)
   link=ET.fromstring('<link name="terrain"><collision name="survey"><geometry><mesh><uri>survey.dae</uri></mesh></geometry></collision></link>')
   before=ET.tostring(link.find('collision'));axis=np.array([0.,30.,60.]);h=np.full((3,3),-10.)
   decorate_link(link,out,axis,axis,h)
   self.assertEqual(ET.tostring(link.find('collision')),before);np.testing.assert_array_equal(h,-10.)
   metal=link.find('visual/material/pbr/metal');assets=sand_assets(out)
   for field,key in [('albedo_map','diff'),('normal_map','nor_gl'),('roughness_map','rough')]:self.assertEqual(Path(metal.findtext(field)),assets[key].resolve())
   ns={'c':'http://www.collada.org/2005/11/COLLADASchema'};mesh=ET.parse(link.findtext('visual/geometry/mesh/uri'))
   arrays=mesh.findall('.//c:source',ns);uv=next(s for s in arrays if 'uv' in s.get('id','').lower() or 'texcoord' in s.get('id','').lower())
   values=np.fromstring(uv.findtext('c:float_array',namespaces=ns),sep=' ').reshape(-1,2)
   self.assertAlmostEqual(float(np.ptp(values[:,0])),2.)
if __name__=='__main__':unittest.main()
