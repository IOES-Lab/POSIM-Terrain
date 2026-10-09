"""Explicit Suez simulation geometry: mapped water at an assumed 10 m depth.

The override is not a survey. It is spatially bounded, versioned and applied
before writing the same numeric field used by collision, sensors and routing.
No Shapely / GIS wheel is required by the packaged runtime.
"""
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np

DATA=Path(__file__).with_name('data')/'suez-simulation.geojson.gz'
WATER_ID=7
BANK_ID=8
SOURCE_IDS={WATER_ID:'Suez_assumed_water_10m',BANK_ID:'Suez_OSM_modelled_bank'}

@lru_cache(maxsize=1)
def configuration():
    raw=gzip.decompress(DATA.read_bytes());value=json.loads(raw)
    if value.get('assumed_depth_m')!=10 or value.get('measured_bathymetry') is not False:
        raise ValueError('invalid_suez_simulation_configuration')
    return value,hashlib.sha256(raw).hexdigest()

def fingerprint():return configuration()[1]

def in_region(position,margin=0.):
    bounds=configuration()[0]['bbox'];lon,lat=position
    return bounds[0]-margin<=lon<=bounds[2]+margin and bounds[1]-margin<=lat<=bounds[3]+margin

def _ring(lon,lat,coordinates):
    vertices=np.asarray(coordinates,dtype=float);result=np.zeros(lon.shape,dtype=bool)
    for a,b in zip(vertices[:-1],vertices[1:]):
        if a[1]==b[1] or max(a[1],b[1])<lat.min() or min(a[1],b[1])>lat.max():continue
        cross=(a[1]>lat)!=(b[1]>lat)
        indexes=np.flatnonzero(cross)
        if indexes.size:
            boundary=a[0]+(lat[indexes]-a[1])*(b[0]-a[0])/(b[1]-a[1])
            result[indexes]^=lon[indexes]<boundary
    return result

def masks(lon,lat):
    value,_=configuration();bounds=value['bbox']
    region=(lon>bounds[0])&(lon<bounds[2])&(lat>bounds[1])&(lat<bounds[3])
    water=np.zeros(lon.shape,dtype=bool)
    if not region.any():return water,region
    x,y=np.asarray(lon)[region],np.asarray(lat)[region];selected=np.zeros(x.shape,dtype=bool)
    for feature in value['features']:
        geometry=feature['geometry']
        polygons=[geometry['coordinates']] if geometry['type']=='Polygon' else geometry['coordinates']
        for rings in polygons:
            inside=_ring(x,y,rings[0])
            for hole in rings[1:]:inside&=~_ring(x,y,hole)
            selected|=inside
    water[region]=selected
    return water,region&~water

def apply(heights,providers,lon,lat):
    water,land=masks(lon,lat)
    if not (water|land).any():return heights,providers,None
    value,digest=configuration();result=np.array(heights,copy=True);ids=np.array(providers,copy=True)
    corrected_water=water&(~np.isfinite(result)|(result>=0))
    result[water]=-value['assumed_depth_m'];ids[water]=WATER_ID
    # The mapped banks must remain collision obstacles even if a coarse DEM
    # interpolates water over them. Existing higher measured land is retained.
    banks=land&(~np.isfinite(result)|(result<1.))
    result[banks]=1.;ids[banks]=BANK_ID
    record=dict(provider='Suez_simulation_override',status='applied',version=value['version'],
        assumed_depth_m=10,measured_bathymetry=False,water_vertices=int(water.sum()),
        corrected_land_to_water_vertices=int(corrected_water.sum()),modelled_bank_vertices=int(banks.sum()),
        geometry_sha256=digest,attribution=value['attribution'],sources=value['sources'],
        scope='Mapped Suez water only; 10 m is a user-requested simulation assumption. Bank correction is a model, not surveyed elevation.')
    return result,ids,record
