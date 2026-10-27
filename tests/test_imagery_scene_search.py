"""A locally clear scene beyond the first twelve remains eligible."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from shapely.geometry import box, mapping
import shapely
import posim_terrain.coastal_detail as detail


class SceneSearch(unittest.TestCase):
    def search(self, clear_at, count=65):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            folder=Path(directory)
            items=[dict(id=str(i),geometry=mapping(box(0,0,1,1)),
                        properties={'datetime':'2026-03-10T00:00:00Z'},
                        assets={key:{'href':f'https://sentinel-cogs.s3.us-west-2.amazonaws.com/{i}/{key}.tif'}
                                for key in ('visual','scl')}) for i in range(count)]
            class Reply:
                def raise_for_status(self):pass
                def json(self):return dict(features=items)
            visited=[]
            class Raster:
                def __init__(self,url):self.url=url
                def __enter__(self):return self
                def __exit__(self,*args):pass
            class VRT(Raster):
                def __init__(self,source,**kwargs):self.url=source.url
                def read(self,bands):
                    index=int(self.url.split('/')[-2])
                    if isinstance(bands,list):
                        visited.append(index)
                        return np.full((3,4,4),120,dtype=np.uint8)
                    return np.full((4,4),4 if index==clear_at else 9,dtype=np.uint8)
            stack.enter_context(patch.object(detail,'directory',return_value=folder))
            stack.enter_context(patch('pyogrio.raw.read',return_value=({'crs':'EPSG:4326'},None,[shapely.to_wkb(box(0,0,1,1))],None)))
            stack.enter_context(patch('httpx.post',return_value=Reply()))
            stack.enter_context(patch('rasterio.open',side_effect=Raster))
            stack.enter_context(patch('rasterio.vrt.WarpedVRT',side_effect=VRT))
            try:
                detail.warm_cell(0,0,coast_dataset='fixture')
                result=json.loads((folder/'manifest.json').read_text())
            except ValueError as error:result=str(error)
            return result,visited

    def test_thirteenth_clear_scene_is_used_without_lowering_quality(self):
        result,visited=self.search(12)
        self.assertEqual(result['sentinel_item'],'12')
        self.assertEqual(result['invalid_fraction'],0)
        self.assertEqual(visited,list(range(13)))

    def test_search_is_bounded_and_does_not_publish_cloudy_fallback(self):
        result,visited=self.search(60)
        self.assertEqual(result,'sentinel_cloud_or_coverage')
        self.assertEqual(visited,list(range(60)))


if __name__=='__main__':unittest.main()
