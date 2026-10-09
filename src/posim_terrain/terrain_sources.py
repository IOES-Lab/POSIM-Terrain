"""Optional real regional sources, bounded downloads, explicit coverage and fallback."""
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid
import numpy as np

COP_URL='https://copernicus-dem-30m.s3.amazonaws.com/'
COP_ATTR='© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; source Copernicus DEM GLO-30, modified by regional sampling.'


def cache_root():
    p=Path(os.environ.get('WWOS_ASSETS_DIR',os.environ.get('WWOS_DATA_DIR',os.environ.get('POSIM_TERRAIN_DATA_DIR',str(Path.home()/'.cache/posim-terrain')))))/'providers'
    p.mkdir(parents=True,exist_ok=True)
    return p


def cached_download(url,name,limit,deadline_seconds=100):
    root=cache_root();path=root/name
    if not path.exists():
        temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
        try:
            start=time.monotonic();size=0
            request=urllib.request.Request(url,headers={'User-Agent':'WorldwideOceanSimulator/0.1'})
            with urllib.request.urlopen(request,timeout=20) as response, temp.open('wb') as out:
                while chunk:=response.read(256*1024):
                    size+=len(chunk)
                    if size>limit or time.monotonic()-start>deadline_seconds:raise ValueError('source_download_limit')
                    out.write(chunk)
            if size<16:raise ValueError('empty_source_response')
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    if path.stat().st_size>limit:raise ValueError('cached_source_limit')
    meta_path=path.with_suffix(path.suffix+'.json')
    signature=[path.stat().st_size,path.stat().st_mtime_ns]
    try:meta=json.loads(meta_path.read_text()) if meta_path.exists() else {}
    except (ValueError,OSError):meta={}
    if meta.get('signature')!=signature:
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            while chunk:=stream.read(1024*1024):digest.update(chunk)
        meta={'url':url,'sha256':digest.hexdigest(),'bytes':signature[0],'signature':signature}
        temporary=meta_path.with_name(meta_path.name+'.'+uuid.uuid4().hex+'.part')
        try:temporary.write_text(json.dumps(meta));temporary.replace(meta_path)
        finally:temporary.unlink(missing_ok=True)
    path.with_name(path.name+'.access').touch()
    return path,{k:v for k,v in meta.items() if k!='signature'}


def error_reason(error):
    if isinstance(error,urllib.error.HTTPError):return 'http_'+str(error.code)
    return type(error).__name__+': '+str(error)[:160]


def raster_samples(path,lon,lat,method='linear',assume_wgs84=False):
    import rasterio
    from rasterio.windows import from_bounds,Window
    from scipy.interpolate import RegularGridInterpolator
    with rasterio.open(path) as src:
        if not ((src.crs and src.crs.to_epsg()==4326) or (src.crs is None and assume_wgs84)):
            raise ValueError('source_requires_wgs84_geographic_crs')
        window=from_bounds(float(lon.min())-.001,float(lat.min())-.001,float(lon.max())+.001,float(lat.max())+.001,src.transform)
        c0=max(0,math.floor(window.col_off));r0=max(0,math.floor(window.row_off))
        c1=min(src.width,math.ceil(window.col_off+window.width));r1=min(src.height,math.ceil(window.row_off+window.height))
        if c1-c0<2 or r1-r0<2:return np.full(lon.shape,np.nan)
        data=src.read(1,window=Window(c0,r0,c1-c0,r1-r0),masked=True).astype(float).filled(np.nan)
        transform=src.transform
        xs=transform.c+(np.arange(c0,c1)+.5)*transform.a
        ys=transform.f+(np.arange(r0,r1)+.5)*transform.e
        interp=RegularGridInterpolator((ys[::-1],xs),data[::-1],method=method,bounds_error=False,fill_value=np.nan)
        return interp(np.stack((lat,lon),axis=-1))


def copernicus(lon,lat):
    land=np.full(lon.shape,np.nan);water=np.full(lon.shape,-1,dtype=np.int8)
    record={'provider':'Copernicus_GLO30','native_spacing_arc_seconds':1,'nominal_spacing_m':30,
      'vertical_datum':'EGM2008 orthometric height','vertical_alignment':'MSL approximation; local geoid/tidal offset not applied',
      'attribution':COP_ATTR,'assets':[],'tiles':[]}
    pairs=[(a,b) for a in range(math.floor(float(lat.min())),math.floor(float(lat.max()))+1)
                   for b in range(math.floor(float(lon.min())),math.floor(float(lon.max()))+1)]
    if len(pairs)>9:return land,water,dict(record,status='extent_limit')
    for a,b in pairs:
        north=('N' if a>=0 else 'S')+f'{abs(a):02d}_00'
        east=('E' if b>=0 else 'W')+f'{abs(b):03d}_00'
        stem=f'Copernicus_DSM_COG_10_{north}_{east}'
        base=COP_URL+stem+'_DEM/'
        tile={'tile':stem}
        try:
            dem,asset=cached_download(base+stem+'_DEM.tif',stem+'_DEM.tif',80*1024**2)
            wbm,mask_asset=cached_download(base+'AUXFILES/'+stem+'_WBM.tif',stem+'_WBM.tif',8*1024**2)
            heights=raster_samples(dem,lon,lat);mask=raster_samples(wbm,lon,lat,'nearest')
            valid=np.isfinite(heights)&np.isfinite(mask)&(heights>-12000)&(heights<10000)&np.isin(mask,[0,1,2,3])
            water[valid]=mask[valid].astype(np.int8)
            replace=valid&(mask!=1);land[replace]=heights[replace]
            tile.update(status='available',land_vertices=int(replace.sum()),ocean_vertices=int((valid&(mask==1)).sum()))
            record['assets'].extend([asset,mask_asset])
        except Exception as error:tile.update(status='unavailable',reason=error_reason(error))
        record['tiles'].append(tile)
    record.update(status='available' if (water>=0).any() else 'no_coverage',land_vertices=int(np.isfinite(land).sum()))
    return land,water,record


def coastal_bathymetry(gebco,water,slon,slat,source,lon,lat,forward):
    # Land elevations in the coarse GEBCO interpolation must not turn known
    # Copernicus ocean pixels into land. Regrid nearby real GEBCO ocean samples,
    # never invent a fixed depth. A 1.5 km support limit keeps gaps explicit.
    from scipy.interpolate import LinearNDInterpolator
    from scipy.spatial import cKDTree
    result=gebco.copy();corrected=np.zeros(gebco.shape,dtype=bool)
    needed=(water==1)&(gebco>=0)
    if not needed.any():return result,corrected
    sx,sy=np.meshgrid(slon,slat);marine=source<0
    if marine.sum()<3:return result,corrected
    x,y=forward.transform(sx[marine],sy[marine]);points=np.column_stack((x,y));depths=source[marine]
    qx,qy=forward.transform(lon[needed],lat[needed]);queries=np.column_stack((qx,qy))
    distance,index=cKDTree(points).query(queries)
    estimated=np.asarray(LinearNDInterpolator(points,depths)(queries))
    estimated=np.where(np.isfinite(estimated),estimated,depths[index])
    valid=(distance<=1500)&np.isfinite(estimated)&(estimated<0)
    rows,cols=np.where(needed);result[rows[valid],cols[valid]]=estimated[valid]
    corrected[rows[valid],cols[valid]]=True
    return result,corrected


def combine(gebco,land,water):
    """Per-vertex provenance: 0 GEBCO, 2 Copernicus land (ID 1 reserved)."""
    heights=gebco.copy();provider=np.zeros(gebco.shape,dtype=np.uint8)
    select=np.isfinite(land)&(water!=1)
    heights[select]=land[select];provider[select]=2
    return heights,provider
