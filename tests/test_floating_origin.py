"""Metric, orientation and seam invariants for a live origin change."""
import sys,math,unittest,tempfile,time
from pathlib import Path
import numpy as np
from posim_terrain.floating_origin import translation,prepare_frame,GEO
from posim_terrain.terrain import projection

class FloatingOriginTests(unittest.TestCase):
 def test_translation_preserves_wave_phase_and_grid_indices(self):
  for width in (500,1000,1024):
   for position in ([17000,17000],[-17000,15000],[4200,-4200]):
    shift=translation(position,width,width)
    for d in shift:
     self.assertEqual(d%256,0);self.assertEqual(d%width,0)
  self.assertEqual(translation([16009,0],1000,1000),[0,0])
  self.assertEqual(translation([16100,0],1000,1000),[32000,0])
 def test_rigid_frame_preserves_location_and_neighbourhood(self):
  for latitude in (-55,0,35,60):
   for longitude in (-179,0,129,179):
    spec=dict(latitude=latitude,longitude=longitude,width_m=2000,height_m=2000,samples=129,source_policy='auto')
    p=np.array([16300.,3000.]);shift=np.array([32000.,0.])
    points=np.array([p+[x,y] for x in (-2500,0,2500) for y in (-2500,0,2500)])
    new,error=prepare_frame(spec,p,shift,points)
    old=projection(spec)[2];inverse=projection(new)[2];forward=projection(new)[1]
    a,b=old.transform(*p);c,d=inverse.transform(*(p-shift))
    self.assertLess(GEO.inv(a,b,c,d)[2],1e-5)
    self.assertLessEqual(error,.25)
    x,y=forward.transform(a,b);np.testing.assert_allclose([x,y],p-shift,atol=1e-6)
    matrix=np.asarray(new['_native_frame']['matrix']);self.assertAlmostEqual(np.linalg.det(matrix),1,places=8)
 def test_repeated_origins_do_not_rewind_geographic_position(self):
  spec=dict(latitude=35.,longitude=129.,width_m=2048,height_m=2048,samples=129,source_policy='auto')
  p=np.array([4200.,600.])
  for epoch in range(50):
   shift=np.array(translation(p,1024,1024));points=np.array([p+[x,y] for x in (-2000,2000) for y in (-2000,2000)])
   old=projection(spec)[2].transform(*p)
   spec,error=prepare_frame(spec,p,shift,points);p-=shift
   new=projection(spec)[2].transform(*p);self.assertLess(GEO.inv(*old,*new)[2],1e-5)
   p+=np.array([8200.,700.])
 def test_distant_distortion_is_rejected(self):
  spec=dict(latitude=60.,longitude=10.,width_m=2000,height_m=2000)
  with self.assertRaisesRegex(ValueError,'projection_error'):
   prepare_frame(spec,[16000,0],[32000,0],[[500000,500000]])

if __name__=='__main__':unittest.main()
