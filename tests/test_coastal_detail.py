"""Safety regressions for optional coastal data and immutable streamed seams."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from datetime import datetime,timezone

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from PIL import Image
from shapely.geometry import box,mapping

ROOT=Path(__file__).resolve().parents[1]
import posim_terrain.coastal_detail as detail
from posim_terrain.terrain import projection
from posim_terrain.seabed_visual import decorate_link,prepare,shoreline_pixels,sand_assets


class CoastalSafety(unittest.TestCase):
    def test_surface_color_preserves_complete_mesh_collision_and_high_land(self):
        axis=np.arange(5,dtype=float)*10
        heights=np.tile([-5.,-5.,-.25,.2,8.],(5,1))
        original=heights.copy()
        link=ET.fromstring('<link name="terrain"><collision name="measured"><geometry><mesh><uri>original.dae</uri></mesh></geometry></collision></link>')
        collision=ET.tostring(link.find('collision'))
        satellite=self.out/'land-satellite.png'
        Image.new('RGB',(32,32),(180,160,140)).save(satellite)
        saved=satellite.read_bytes()
        decorate_link(link,self.out,axis,axis,heights,land_texture={'file':satellite.name})
        self.assertEqual(ET.tostring(link.find('collision')),collision)
        np.testing.assert_array_equal(heights,original)
        self.assertEqual(satellite.read_bytes(),saved)
        ns={'c':'http://www.collada.org/2005/11/COLLADASchema'}
        faces=[]
        for visual in link.findall('visual'):
            mesh=ET.parse(visual.findtext('geometry/mesh/uri'))
            indices=list(map(int,mesh.findtext('.//c:triangles/c:p',namespaces=ns).split()))[::3]
            faces.extend(tuple(indices[i:i+3]) for i in range(0,len(indices),3))
        self.assertEqual(len(faces),32)
        self.assertEqual(len(set(faces)),32)
        self.assertEqual({v.get('name') for v in link.findall('visual')},{'seabed','shallow','land'})
        self.assertFalse((self.out/'seabed-shallow-grid-v1.png').exists())
        albedo=Path(link.findtext("visual[@name='land']/material/pbr/metal/albedo_map"))
        self.assertNotEqual(albedo.name,satellite.name)
        shallow_albedo=Path(link.findtext("visual[@name='shallow']/material/pbr/metal/albedo_map"))
        self.assertEqual(albedo.read_bytes(),(self.out/'land-shoreline.png').read_bytes())
        self.assertEqual(shallow_albedo.read_bytes(),(self.out/'seabed-shoreline.png').read_bytes())
        self.assertNotEqual(albedo,shallow_albedo)

    def test_shoreline_tint_is_dark_blue_grey_and_fades_at_both_height_limits(self):
        heights=np.array([[-1.5,-1.,-.999,-.75,-.5,-.06,0.,.06,.25,.499,.5,1.,8.]])
        pixels=np.tile([164,145,104],heights.shape+(1,)).astype(np.uint8)
        original=pixels.copy();saved=heights.copy()
        result=shoreline_pixels(pixels,heights)
        np.testing.assert_array_equal(result[heights<=-1.],pixels[heights<=-1.])
        np.testing.assert_array_equal(result[heights>=.5],pixels[heights>=.5])
        sea=result[heights==0.][0]
        self.assertGreater(int(sea[2]),int(sea[0]))
        self.assertLess(float(np.mean(sea)),130.)
        self.assertTrue(np.any(result[heights==-.75]!=pixels[heights==-.75]))
        self.assertTrue(np.any(result[heights==-.5]!=pixels[heights==-.5]))
        np.testing.assert_array_equal(result[0,5],result[0,7])
        # Both ends ease into their existing colors, with no sharp new edge.
        for index in (2,9):
            self.assertLess(np.max(abs(result[0,index].astype(int)-pixels[0,index])),2)
        np.testing.assert_array_equal(pixels,original);np.testing.assert_array_equal(heights,saved)

    def test_shoreline_band_uses_tinted_texture_above_and_below_with_or_without_imagery(self):
        axis=np.arange(5,dtype=float)*10
        for height in (-.75,-.25,0.,.25):
            for imagery in (False,True):
                with self.subTest(height=height,imagery=imagery):
                    out=self.out/f'{height}-{imagery}';out.mkdir()
                    Image.new('RGB',(32,32),(180,160,140)).save(out/'land-satellite.png')
                    atlas={'file':'land-satellite.png'} if imagery else None
                    link=ET.Element('link');decorate_link(link,out,axis,axis,np.full((5,5),height),land_texture=atlas)
                    visual=link.find('visual');land=imagery and height>=0
                    self.assertEqual(visual.get('name'),'land' if land else 'shallow')
                    albedo=Path(visual.findtext('material/pbr/metal/albedo_map'))
                    self.assertEqual(albedo.read_bytes(),(out/('land-shoreline.png' if land else 'seabed-shoreline.png')).read_bytes())
                    self.assertNotEqual(albedo.read_bytes(),(out/('land-satellite.png' if land else 'seabed-grid.png')).read_bytes())

    def test_shoreline_limits_and_higher_land_keep_original_texture(self):
        axis=np.arange(5,dtype=float)*10
        for height in (-1.25,-1.,.5,.75):
            for imagery in (False,True):
                with self.subTest(height=height,imagery=imagery):
                    out=self.out/f'{height}-{imagery}';out.mkdir()
                    Image.new('RGB',(32,32),(180,160,140)).save(out/'land-satellite.png')
                    atlas={'file':'land-satellite.png'} if imagery else None
                    link=ET.Element('link');decorate_link(link,out,axis,axis,np.full((5,5),height),land_texture=atlas)
                    visual=link.find('visual');land=imagery and height>=0
                    self.assertEqual(visual.get('name'),'land' if land else 'seabed')
                    albedo=Path(visual.findtext('material/pbr/metal/albedo_map'))
                    self.assertEqual(albedo.read_bytes(),(out/'land-satellite.png' if land else sand_assets(out)['diff']).read_bytes())
                    self.assertFalse((out/'land-shoreline.png').exists())
                    self.assertFalse((out/'seabed-shoreline.png').exists())

    def test_deeper_submerged_bank_has_no_new_ocean_material_with_or_without_imagery(self):
        axis=np.arange(5,dtype=float)*10
        heights=np.tile([-5.,-2.,-1.5,-1.2,-1.],(5,1))
        for atlas in (None,{'file':'land-satellite.png'}):
            Image.new('RGB',(32,32),(180,160,140)).save(self.out/'land-satellite.png')
            link=ET.Element('link')
            decorate_link(link,self.out,axis,axis,heights,land_texture=atlas)
            self.assertNotIn('shallow',{v.get('name') for v in link.findall('visual')})
            self.assertFalse((self.out/'land-shoreline.png').exists())
            self.assertFalse((self.out/'seabed-shoreline.png').exists())

    def test_port_imagery_prefers_recent_observations_not_oldest_cloud_free_scene(self):
        now=datetime(2026,10,8,tzinfo=timezone.utc)
        query=detail.imagery_query([120.5,24.25,120.55,24.3],now)
        self.assertTrue(query['datetime'].startswith('2023-10'))
        self.assertEqual(query['sortby'],[dict(field='properties.datetime',direction='desc')])
        current=dict(status='ready',imagery_revision=detail.IMAGERY_REVISION,sentinel_acquisition='2026-09-01T00:00:00Z')
        self.assertTrue(detail.imagery_current(current,now))
        self.assertFalse(detail.imagery_current(dict(current,sentinel_acquisition='2020-01-21T00:00:00Z'),now))
        self.assertFalse(detail.imagery_current(dict(current,sentinel_acquisition='2027-01-01T00:00:00Z'),now))
        self.assertTrue(detail.imagery_current(dict(status='no_land'),now))

    def test_display_fallback_keeps_water_roads_and_collision_unchanged(self):
        pixels=np.array([[[30,90,120],[30,90,120],[150,150,150],[90,140,60]]],dtype=np.uint8)
        elevation=np.array([[4.,-4.,4.,4.]])
        saved=elevation.copy()
        result,count=detail.land_water_color(pixels,elevation)
        self.assertEqual(count,1)
        self.assertGreater(result[0,0,1],result[0,0,2])
        np.testing.assert_array_equal(result[0,1:],pixels[0,1:])
        np.testing.assert_array_equal(elevation,saved)

    def test_texture_samples_mesh_triangles_and_north_up_orientation(self):
        axis=np.array([0.,10.]);h=np.array([[0.,0.],[0.,10.]])
        xx,yy=np.meshgrid([0.,5.,10.],[10.,5.,0.])
        result=detail.surface_height(axis,axis,h,xx,yy)
        np.testing.assert_array_equal(result,[[0,5,10],[0,5,5],[0,0,0]])
        # A bilinear sampler gives 2.5 at the middle, crossing the wrong shore.
        self.assertEqual(result[1,1],5)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)
        env=patch.dict(os.environ,WWOS_ASSETS_DIR=str(self.out),WWOS_COASTAL_WARM='0',WWOS_COASTAL_DETAIL='1')
        env.start();self.addCleanup(env.stop)
        self.spec=dict(latitude=35.07446,longitude=129.08468,width_m=1000,height_m=1000,samples=17,source_policy='auto')
        _,_,inverse=projection(self.spec)
        self.axis=np.linspace(-500,500,17);xx,yy=np.meshgrid(self.axis,self.axis)
        self.lon,self.lat=inverse.transform(xx,yy)
        self.h=np.where(xx<200,10.,-20.);self.ids=np.where(self.h>=0,2,0).astype(np.uint8)
        self.water=np.ones(self.h.shape,dtype=np.int8)
        self.slon=np.linspace(self.lon.min()-.01,self.lon.max()+.01,9)
        self.slat=np.linspace(self.lat.min()-.01,self.lat.max()+.01,9)
        self.source=np.full((9,9),-20.)
        self.records=[]

    def atlas(self):
        for i,j in detail.keys(self.lon,self.lat):
            folder=detail.directory(i,j);folder.mkdir()
            bounds=[i*detail.CELL,j*detail.CELL,(i+1)*detail.CELL,(j+1)*detail.CELL]
            land=box(bounds[0],bounds[1],self.spec['longitude'],bounds[3])
            (folder/'land.json').write_text(json.dumps(mapping(land)))
            Image.new('RGB',(64,64),(100,180,90)).save(folder/'satellite.png')
            detail.atomic(folder/'manifest.json',dict(revision=detail.REVISION,cell=[i,j],bounds=bounds,status='ready',
                hashes={name:detail.digest(folder/name) for name in ('land.json','satellite.png')}))
        from posim_terrain.terrain_sources import cache_root
        path=cache_root()/'fixture_DEM.tif'
        with rasterio.open(path,'w',driver='GTiff',width=64,height=64,count=1,dtype='float32',crs='EPSG:4326',
                           transform=from_bounds(self.lon.min()-.01,self.lat.min()-.01,self.lon.max()+.01,self.lat.max()+.01,64,64)) as dst:
            dst.write(np.full((64,64),10,np.float32),1)
        self.records=[dict(assets=[dict(url='https://example.invalid/fixture_DEM.tif')])]

    def upgrade(self):
        return detail.upgrade(self.spec,self.axis,self.axis,self.h,self.ids,self.water,self.lon,self.lat,self.slon,self.slat,self.source,self.records)

    def test_missing_source_returns_identical_baseline_without_network_wait(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('inline network')):
            h,ids,record=self.upgrade()
        self.assertIs(h,self.h);self.assertIs(ids,self.ids);self.assertTrue(record['fallback'])
        self.assertIn('cache_warming',record['reason'])

    def test_saved_baseline_gets_ready_imagery_without_changing_collision(self):
        self.atlas()
        world=self.out/'world.sdf'
        world.write_text('<sdf version="1.10"><world name="test"><model name="gebco_terrain"><static>true</static><link name="terrain"><collision name="collision"><geometry><mesh><uri>terrain.dae</uri></mesh></geometry></collision></link></model></world></sdf>')
        manifest=dict(spec=self.spec,source_policy='auto',source='GEBCO_2026',visual_aid={},
                      coastal_detail=dict(status='baseline',fallback=True))
        detail.atomic(self.out/'manifest.json',manifest)
        detail.atomic(self.out/'terrain.json',dict(xs=self.axis.tolist(),ys=self.axis.tolist(),heights=self.h.tolist(),land_texture=None))
        np.savez(self.out/'projected.npz',xs=self.axis,ys=self.axis,elevation=self.h,provider=self.ids)
        before=(self.out/'projected.npz').read_bytes()
        target=prepare(world)
        self.assertEqual((self.out/'projected.npz').read_bytes(),before)
        self.assertEqual(json.loads((self.out/'terrain.json').read_text())['heights'],self.h.tolist())
        updated=json.loads((self.out/'manifest.json').read_text())
        self.assertTrue(updated['coastal_detail']['fallback'])
        self.assertFalse(updated['visual_aid']['collision_changed'])
        self.assertIsNotNone(updated['visual_aid']['land_texture'])
        self.assertEqual(ET.parse(target).findtext('.//collision/geometry/mesh/uri'),'terrain.dae')
        albedo=Path(ET.parse(target).findtext(".//visual[@name='land']/material/pbr/metal/albedo_map"))
        self.assertEqual(albedo.name,'land-satellite-'+detail.digest(albedo)+'.png')
        self.assertEqual(detail.digest(self.out/'land-satellite.png'),updated['visual_aid']['land_texture']['sha256'])

    def test_missing_imagery_at_launch_keeps_saved_baseline(self):
        world=self.out/'world.sdf'
        world.write_text('<sdf version="1.10"><world name="test"><model name="gebco_terrain"><link name="terrain"/></model></world></sdf>')
        detail.atomic(self.out/'manifest.json',dict(spec=self.spec,source_policy='auto',source='GEBCO_2026',visual_aid={}))
        np.savez(self.out/'projected.npz',xs=self.axis,ys=self.axis,elevation=self.h)
        before=(self.out/'projected.npz').read_bytes()
        prepare(world)
        self.assertEqual((self.out/'projected.npz').read_bytes(),before)
        self.assertFalse((self.out/'land-satellite.png').exists())

    def test_ready_sources_refine_coast_using_only_supported_marine_samples(self):
        self.atlas();h,ids,record=self.upgrade()
        self.assertFalse(record['fallback']);self.assertTrue(np.array_equal(self.h,np.where(np.meshgrid(self.axis,self.axis)[0]<200,10.,-20.)))
        self.assertTrue((h[self.lon<self.spec['longitude']]>0).all())
        water=self.lon>self.spec['longitude']
        self.assertTrue((h[water]<0).all());np.testing.assert_allclose(h[water],-20,atol=1e-8)
        self.assertGreater(record['changed_vertices'],0)
        texture=detail.paint(self.out,self.spec,self.axis,self.axis,h)
        self.assertIsNotNone(texture);self.assertEqual(texture['sha256'],detail.digest(self.out/texture['file']))

    def test_display_grading_preserves_source_and_numeric_terrain(self):
        self.atlas()
        before=self.h.copy()
        paths=list(detail.root().glob('atlas_*/satellite.png'))
        original={p:detail.digest(p) for p in paths}
        texture=detail.paint(self.out,self.spec,self.axis,self.axis,self.h)
        self.assertEqual(texture['display_revision'],detail.VISUAL_REVISION)
        self.assertEqual(texture['nominal_spacing_m'],10)
        self.assertFalse(texture['collision_changed'])
        np.testing.assert_array_equal(self.h,before)
        self.assertEqual(original,{p:detail.digest(p) for p in paths})
        pixels=np.array([[[65,95,45],[160,135,80],[210,215,210]],
                         [[65,95,45],[160,135,80],[210,215,210]]],np.uint8)
        adjusted=np.asarray(detail.display_image(pixels))
        self.assertEqual(adjusted.shape,pixels.shape)
        self.assertGreater(float(np.std(adjusted)),float(np.std(pixels)))
        self.assertGreater(int(adjusted[0,0,1])-int(adjusted[0,0,0]),30)

    def test_saved_display_can_refresh_without_replacing_collision(self):
        self.atlas()
        world=self.out/'world.sdf'
        world.write_text('<sdf version="1.10"><world name="test"><model name="gebco_terrain"><link name="terrain"><collision name="collision"><geometry><mesh><uri>unchanged.dae</uri></mesh></geometry></collision></link></model></world></sdf>')
        old=detail.paint(self.out,self.spec,self.axis,self.axis,self.h)
        old.pop('display_revision')
        detail.atomic(self.out/'manifest.json',dict(spec=self.spec,source_policy='auto',source='GEBCO',visual_aid={'land_texture':old}))
        np.savez(self.out/'projected.npz',xs=self.axis,ys=self.axis,elevation=self.h)
        before=(self.out/'projected.npz').read_bytes()
        result=prepare(world)
        self.assertEqual((self.out/'projected.npz').read_bytes(),before)
        self.assertEqual(ET.parse(result).findtext('.//collision/geometry/mesh/uri'),'unchanged.dae')
        self.assertEqual(json.loads((self.out/'manifest.json').read_text())['visual_aid']['land_texture']['display_revision'],detail.VISUAL_REVISION)

    def test_no_marine_support_cannot_invent_depth(self):
        self.atlas();self.source=np.empty((0,0));self.slat=np.array([]);self.slon=np.array([])
        h,_,record=self.upgrade()
        unknown=(self.lon>self.spec['longitude'])&(self.h>=0)
        self.assertTrue((h[unknown]>=0).all());self.assertGreater(record['unknown_water_kept_as_obstacles'],0)

    def test_coarse_ocean_baseline_does_not_hide_measured_osm_island(self):
        self.atlas();self.h[:]=-20.;self.ids[:]=0
        original=self.h.copy()
        h,ids,record=self.upgrade()
        self.assertEqual(record['status'],'applied')
        np.testing.assert_array_equal(self.h,original)
        land=self.lon<self.spec['longitude']
        np.testing.assert_allclose(h[land],10,atol=1e-8);self.assertTrue((ids[land]==2).all())
        self.assertTrue((h[~land]==-20).all())
        self.records=[]
        fallback,_,record=self.upgrade()
        self.assertIs(fallback,self.h);self.assertIn('dem_gap',record['reason'])

    def test_corrupt_image_invalid_geometry_and_dem_gap_each_restore_baseline(self):
        self.atlas();folder=detail.directory(*detail.keys(self.lon,self.lat)[0])
        (folder/'satellite.png').write_bytes(b'corrupt')
        h,ids,record=self.upgrade();self.assertIs(h,self.h);self.assertIs(ids,self.ids);self.assertTrue(record['fallback'])
        self.atlas_repair(folder)
        (folder/'land.json').write_text(json.dumps({'type':'Polygon','coordinates':[[[129.08,35.07],[129.09,35.08],[129.08,35.08],[129.09,35.07],[129.08,35.07]]]}))
        manifest=json.loads((folder/'manifest.json').read_text());manifest['hashes']['land.json']=detail.digest(folder/'land.json');detail.atomic(folder/'manifest.json',manifest)
        h,ids,record=self.upgrade();self.assertIs(h,self.h);self.assertIs(ids,self.ids)
        self.atlas_repair(folder);self.records=[]
        h,ids,record=self.upgrade();self.assertIs(h,self.h);self.assertIs(ids,self.ids);self.assertIn('dem_gap',record['reason'])

    def atlas_repair(self,folder):
        import shutil
        shutil.rmtree(folder);self.atlas()

    def test_land_and_seabed_visuals_partition_the_exact_collision_faces(self):
        self.atlas();h,_,_=self.upgrade();atlas=detail.paint(self.out,self.spec,self.axis,self.axis,h)
        link=ET.Element('link');decorate_link(link,self.out,self.axis,self.axis,h,land_texture=atlas)
        faces=[];ns={'c':'http://www.collada.org/2005/11/COLLADASchema'}
        for visual in link.findall('visual'):
            mesh=ET.parse(visual.findtext('geometry/mesh/uri')).find('.//c:triangles',ns)
            indices=np.array([int(v) for v in mesh.findtext('c:p',namespaces=ns).split()]).reshape(-1,9)[:,::3]
            faces.extend(map(tuple,indices))
        n=len(self.axis);expected=[]
        for j in range(n-1):
            for i in range(n-1):
                a=j*n+i;expected.extend([(a,a+1,a+n+1),(a,a+n+1,a+n)])
        self.assertEqual(set(faces),set(expected));self.assertEqual(len(faces),len(expected))

    def test_distinct_tiles_do_not_share_ogre_texture_basename(self):
        from posim_terrain.seabed_visual import renderer_albedo
        left=self.out/'left';right=self.out/'right';left.mkdir();right.mkdir()
        Image.new('RGB',(8,8),(240,20,20)).save(left/'land-satellite.png')
        Image.new('RGB',(8,8),(20,20,240)).save(right/'land-satellite.png')
        before={p:p.read_bytes() for p in (left/'land-satellite.png',right/'land-satellite.png')}
        a=renderer_albedo(left/'land-satellite.png');b=renderer_albedo(right/'land-satellite.png')
        self.assertNotEqual(a.name,b.name)
        self.assertEqual(a.read_bytes(),before[left/'land-satellite.png'])
        self.assertEqual(b.read_bytes(),before[right/'land-satellite.png'])
        # Re-entry keeps the previous resource immutable even after regrading.
        self.assertEqual(renderer_albedo(left/'land-satellite.png'),a)
        Image.new('RGB',(8,8),(20,240,20)).save(left/'land-satellite.png')
        self.assertNotEqual(renderer_albedo(left/'land-satellite.png'),a)
        self.assertEqual(a.read_bytes(),before[left/'land-satellite.png'])




if __name__=='__main__':unittest.main()
