"""Protect periodic GEBCO joins and bounded high-resolution provider requests."""
import tempfile,sys,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from posim_terrain.terrain import gebco_grid,longitude_groups

def source(url,window):
 a,b,c,d=window;lat=-90+(np.arange(a,b+1)+.5)/240;lon=-180+(np.arange(c,d+1)+.5)/240
 unwrapped=np.where(lon<0,lon+360,lon)
 heights=-100+(unwrapped[None,:]-180)*100+lat[:,None]
 return b'actual-test-pixel-window',lat,lon,heights,url

class DatelineTests(unittest.TestCase):
 def test_real_neighbour_pixels_join_across_180(self):
  lon=np.array([[179.999,-180.,-179.999],[179.999,-180.,-179.999]])
  lat=np.array([[20.,20.,20.],[20.001,20.001,20.001]])
  with tempfile.TemporaryDirectory() as out,patch('posim_terrain.terrain.gebco_source',side_effect=source) as loader:
   h,ys,xs,z,records,raws=gebco_grid(lon,lat,Path(out))
   np.testing.assert_allclose(h,-100+(np.where(lon<0,lon+360,lon)-180)*100+lat,atol=1e-8)
   self.assertEqual(loader.call_count,2);self.assertTrue(np.all(np.diff(xs)>0))
   self.assertTrue(all(call.args[1][3]-call.args[1][2]<10 for call in loader.call_args_list))
   self.assertEqual(len(records),2)
 def test_one_side_still_uses_opposite_edge_pixel(self):
  lon=np.full((2,2),179.9999);lat=np.array([[10.,10.],[10.001,10.001]])
  with tempfile.TemporaryDirectory() as out,patch('posim_terrain.terrain.gebco_source',side_effect=source):
   h,*_=gebco_grid(lon,lat,Path(out));np.testing.assert_allclose(h,-100+(lon-180)*100+lat)
 def test_providers_never_receive_a_global_width_seam_window(self):
  lon=np.array([[179.99,179.999,-179.999,-179.99]])
  groups=longitude_groups(lon);self.assertEqual(len(groups),2)
  self.assertTrue(all(np.ptp(lon[group])<.02 for group in groups))
if __name__=='__main__':unittest.main()
