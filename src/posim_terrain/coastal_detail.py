"""Opportunistic OSM shore / Sentinel RGB refinement, with baseline fallback.

Network and global coastline I/O run in a separate, single cache warmer. A
terrain worker reads only complete, hash-verified local atlas cells. A failed
refinement never changes the baseline arrays or waits for a remote service.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

REVISION = 'osm-sentinel-safe-grid-v1'
PIPELINE_REVISION = 'osm-sentinel-supported-islands-v2'
VISUAL_REVISION = 'sentinel-display-v3-land-water'
IMAGERY_REVISION = 'sentinel-recent-v1'
CELL = .05
LAND_URL = 'https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip'
STAC = 'https://earth-search.aws.element84.com/v1/search'


def root():
    from .terrain_sources import cache_root
    p = cache_root()/'coastal-detail'
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic(path, value):
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def keys(lon, lat):
    if np.ptp(lon)>180 or not np.isfinite(lon).all() or not np.isfinite(lat).all():
        raise ValueError('detail_extent_or_dateline')
    west,east = math.floor(float(np.min(lon))/CELL),math.floor(float(np.max(lon))/CELL)
    south,north = math.floor(float(np.min(lat))/CELL),math.floor(float(np.max(lat))/CELL)
    result = [(i,j) for j in range(south,north+1) for i in range(west,east+1)]
    if len(result)>64:raise ValueError('detail_extent_limit')
    return result


def directory(i, j):
    return root()/f'atlas_{i}_{j}'


def imagery_current(manifest, now=None):
    if manifest.get('status')=='no_land':return True
    if manifest.get('imagery_revision')!=IMAGERY_REVISION:return False
    try:
        acquired=datetime.fromisoformat(manifest['sentinel_acquisition'].replace('Z','+00:00'))
        return timedelta(0)<=(now or datetime.now(timezone.utc))-acquired<=timedelta(days=1095)
    except (KeyError,TypeError,ValueError):return False


def imagery_query(bounds, now=None):
    # Globally sorting by cloud cover alone selected a spotless 2020 image
    # over newer observations of reclaimed ports. Prefer recent clear scenes.
    now=now or datetime.now(timezone.utc)
    date=lambda value:value.isoformat().replace('+00:00','Z')
    return dict(collections=['sentinel-2-l2a'],bbox=bounds,limit=60,
                datetime=date(now-timedelta(days=1095))+'/'+date(now),
                query={'eo:cloud_cover':{'lt':30}},
                sortby=[dict(field='properties.datetime',direction='desc')])


def request(cells):
    """Queue absent cells; return immediately even when GIS is unavailable."""
    if os.getenv('WWOS_COASTAL_WARM','1')!='1':return
    pending = False
    for i,j in cells:
        p = directory(i,j);p.mkdir(exist_ok=True)
        if (p/'manifest.json').is_file():
            try:
                if imagery_current(json.loads((p/'manifest.json').read_text())):continue
            except (OSError,ValueError):pass
        error = p/'failure.json'
        try:
            if error.is_file() and json.loads(error.read_text()).get('retry_unix',0)>time.time():continue
        except (OSError,ValueError):pass
        atomic(p/'request.json',dict(i=i,j=j,requested_unix=time.time()))
        pending = True
    if not pending:return
    lock = root()/'warmer.lock'
    if lock.exists():
        try:
            owner=json.loads((lock/'owner.json').read_text())
            os.kill(int(owner['pid']),0)
            return
        except ProcessLookupError:shutil.rmtree(lock,ignore_errors=True)
        except (OSError,ValueError,KeyError):
            if time.time()-lock.stat().st_mtime<60:return
            shutil.rmtree(lock,ignore_errors=True)
    log = (root()/'warmer.log').open('ab')
    try:
        subprocess.Popen([sys.executable,"-m","posim_terrain.coastal_detail",'--warm'],
                         stdout=log,stderr=log,start_new_session=True)
    except OSError:pass
    finally:log.close()


def load(lon, lat):
    cells = keys(lon,lat)
    missing = [c for c in cells if not (directory(*c)/'manifest.json').is_file()]
    if missing:
        request(missing)
        raise ValueError('detail_cache_warming')
    records = []
    for i,j in cells:
        p = directory(i,j)
        try:
            m = json.loads((p/'manifest.json').read_text())
            if m.get('revision')!=REVISION or m.get('cell')!=[i,j]:raise ValueError('detail_cache_revision')
            expected_files={'land.json','satellite.png'} if m.get('status')=='ready' else {'land.json'}
            if m.get('status') not in ('ready','no_land') or set(m['hashes'])!=expected_files:raise ValueError('detail_cache_status')
            for name,expected in m['hashes'].items():
                if digest(p/name)!=expected:raise ValueError('detail_cache_checksum')
        except (OSError,ValueError,KeyError,TypeError):
            (p/'manifest.json').unlink(missing_ok=True);request([(i,j)])
            raise ValueError('detail_cache_rejected')
        records.append((p,m))
    # Keep the last complete atlas usable while its imagery is refreshed.
    # Collision arrays and the running world are never regenerated in place.
    request([(int(m['cell'][0]),int(m['cell'][1])) for _,m in records if not imagery_current(m)])
    return records


def fingerprint():
    """Only future jobs see newly ready data; resident collision stays immutable."""
    ready=[]
    for path in sorted(root().glob('atlas_*/manifest.json')):
        try:
            m=json.loads(path.read_text())
            ready.append((path.parent.name,m.get('revision'),m.get('hashes')))
        except (OSError,ValueError):continue
    return hashlib.sha256(json.dumps([PIPELINE_REVISION,ready],sort_keys=True).encode()).hexdigest()[:16]


def masks(records, lon, lat):
    import shapely
    from shapely.geometry import shape
    geometry = shapely.union_all([shape(json.loads((p/'land.json').read_text())) for p,_ in records])
    if not geometry.is_valid:raise ValueError('detail_invalid_coast')
    return shapely.contains_xy(geometry,lon,lat),geometry


def rgb(records, lon, lat):
    from PIL import Image
    image = np.zeros((*lon.shape,3),np.uint8);covered = np.zeros(lon.shape,bool)
    for p,m in records:
        w,s,e,n = m['bounds']
        use = (lon>=w-1e-10)&(lon<=e+1e-10)&(lat>=s-1e-10)&(lat<=n+1e-10)&~covered
        if m['status']=='no_land':
            image[use]=[31,69,86];covered[use]=True;continue
        with Image.open(p/'satellite.png') as source:pixels = np.asarray(source.convert('RGB'))
        rows = np.clip(((n-lat[use])/(n-s)*pixels.shape[0]).astype(int),0,pixels.shape[0]-1)
        cols = np.clip(((lon[use]-w)/(e-w)*pixels.shape[1]).astype(int),0,pixels.shape[1]-1)
        image[use] = pixels[rows,cols];covered[use] = True
    if not covered.all():raise ValueError('detail_texture_gap')
    return image


def upgrade(spec, xs, ys, heights, provider, water, lon, lat, slon, slat, source, cop_records):
    """Commit the whole candidate or return the untouched numeric baseline."""
    record = dict(revision=PIPELINE_REVISION,status='baseline',fallback=True)
    if os.getenv('WWOS_COASTAL_DETAIL','1')!='1' or spec.get('source_policy','auto')!='auto':
        return heights,provider,dict(record,reason='disabled_or_gebco_only')
    try:
        records = load(lon,lat)
        mapped,geometry = masks(records,lon,lat)
        # Inland lakes/rivers remain governed by the existing numeric sources;
        # an ocean coastline polygon is not a water-level model for those areas.
        mapped &= ~np.isin(water,[2,3])
        # A coarse baseline can miss an entire small island or breakwater.
        # Check the detailed coast before concluding that this tile is ocean;
        # positive vertices still require finite, measured Copernicus heights.
        if not np.any(heights>=0) and not np.any(mapped):
            return heights,provider,dict(record,reason='no_mapped_land')
        from .terrain_sources import raster_samples,cache_root,coastal_bathymetry
        dem = np.full(lon.shape,np.nan)
        for r in cop_records:
            for asset in r.get('assets',[]):
                if not asset['url'].endswith('_DEM.tif'):continue
                values = raster_samples(cache_root()/asset['url'].split('/')[-1],lon,lat)
                valid = np.isfinite(values);dem[valid] = values[valid]
        if not np.isfinite(dem[mapped]).all():raise ValueError('detail_land_dem_gap')
        from .terrain import projection
        _,forward,_ = projection(spec)
        wet = ~mapped & ~np.isin(water,[2,3])
        candidate,corrected = coastal_bathymetry(heights,np.where(wet,1,water),slon,slat,source,lon,lat,forward)
        ids = provider.copy();ids[corrected] = 1
        candidate[mapped] = np.maximum(.2,dem[mapped]);ids[mapped] = 2
        # Never lower unknown positive water to an assumed ocean depth.
        unknown = wet & (candidate>=0)
        if not np.isfinite(candidate).all():raise ValueError('detail_elevation_gap')
        # Preserve the explicit Suez model; OSM ocean polygons cannot fill it.
        from .canal_override import apply
        candidate,ids,canal = apply(candidate,ids,lon,lat)
        # Preflight texture coverage too. Failure of either source aborts both.
        rgb(records,lon,lat)
        record.update(status='applied',fallback=False,atlases=[m for _,m in records],
                      changed_vertices=int(np.count_nonzero(candidate!=heights)),
                      interpolated_marine_vertices=int(corrected.sum()),
                      unknown_water_kept_as_obstacles=int(unknown.sum()),
                      grid_spacing_m=[float(xs[1]-xs[0]),float(ys[1]-ys[0])],
                      collision_and_navigation_same_field=True,canal_model_preserved=bool(canal),
                      attribution='© OpenStreetMap contributors, ODbL; modified Copernicus Sentinel data',
                      geometry_scope='OSM coast sampled on the existing collision lattice; no invented depth')
        return candidate,ids,record
    except Exception as error:
        return heights,provider,dict(record,reason=type(error).__name__+':'+str(error)[:180])


def display_image(pixels):
    """Modest display grading of measured RGB, never a terrain/data change."""
    from PIL import Image, ImageEnhance, ImageFilter
    image = Image.fromarray(pixels)
    # Use a fixed pivot across tiles. A per-image average would give adjacent
    # tiles different brightness and introduce seams in a shared satellite pass.
    image = image.point([max(0,min(255,round(128+(v-128)*1.18))) for v in range(256)]*3)
    image = ImageEnhance.Color(image).enhance(1.20)
    # Restore shadow detail without changing white/black endpoints. Sharpening
    # only counteracts resampling blur; it does not add survey resolution.
    lut = [round(255*(v/255)**.94) for v in range(256)]
    image = image.point(lut*3)
    return image.filter(ImageFilter.UnsharpMask(radius=.8, percent=45, threshold=3))


def surface_height(xs, ys, heights, xx, yy):
    """Sample the exact a-b-d / a-d-c mesh, rather than a bilinear shore."""
    xs,ys,h = np.asarray(xs),np.asarray(ys),np.asarray(heights)
    u=(xx-xs[0])/(xs[1]-xs[0]);v=(yy-ys[0])/(ys[1]-ys[0])
    ix=np.clip(np.floor(u).astype(int),0,len(xs)-2)
    iy=np.clip(np.floor(v).astype(int),0,len(ys)-2)
    fu=np.clip(u-ix,0,1);fv=np.clip(v-iy,0,1)
    a,b,c,d=h[iy,ix],h[iy,ix+1],h[iy+1,ix],h[iy+1,ix+1]
    return np.where(fu>=fv,a+(b-a)*fu+(d-b)*fv,a+(d-c)*fu+(c-a)*fv)


def land_water_color(pixels, elevation):
    """Display fallback for blue imagery on exposed land; never change geometry.

    Imagery and DEM surveys can disagree at tides, reclaimed land and coarse
    shore triangles. Keep roads/buildings/vegetation and underwater imagery;
    recolor only blue/cyan-dominant samples on the actual positive-height mesh.
    A fixed luminance-preserving green avoids per-tile grading seams.
    """
    values=np.asarray(pixels).astype(float)
    r,g,b=values[...,0],values[...,1],values[...,2]
    blue=(b>r*1.12)&(b>g*.97)&(b>35)&(g>r*1.04)
    mask=(np.asarray(elevation)>=0)&blue
    result=values.copy()
    light=np.clip((r*.2126+g*.7152+b*.0722)/110,.30,1.75)
    green=light[...,None]*np.array([82.,119.,61.])
    result[mask]=green[mask]
    return np.clip(result,0,255).astype(np.uint8),int(mask.sum())


def paint(out, spec, xs, ys, heights):
    """A local-only land texture; seabed retains its original metre grid."""
    if os.getenv('WWOS_COASTAL_DETAIL','1')!='1' or not np.any(heights>=0):return None
    try:
        from .terrain import projection
        _,_,inverse = projection(spec)
        xx,yy = np.meshgrid(xs,ys);lon,lat = inverse.transform(xx,yy)
        records = load(lon,lat)
        width,height = float(xs[-1]-xs[0]),float(ys[-1]-ys[0])
        nx,ny = min(2048,max(128,math.ceil(width/10))),min(2048,max(128,math.ceil(height/10)))
        # Texture UVs span pixel edges, not the centres of the first/last pixel.
        # Sample georeferenced pixel centres so imagery and mesh use the same
        # extent (the old endpoint sampling stretched the coast by half a pixel).
        xx,yy = np.meshgrid(xs[0]+(np.arange(nx)+.5)*width/nx,
                           ys[-1]-(np.arange(ny)+.5)*height/ny)
        lon,lat = inverse.transform(xx,yy)
        path = Path(out)/'land-satellite.png'
        temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
        try:
            graded=np.asarray(display_image(rgb(records,lon,lat)))
            pixels,corrected=land_water_color(graded,surface_height(xs,ys,heights,xx,yy))
            from PIL import Image
            Image.fromarray(pixels).save(temporary,format='PNG')
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)
        return dict(file=path.name,bounds=[float(xs[0]),float(ys[0]),float(xs[-1]),float(ys[-1])],
                    sha256=digest(path),size=[nx,ny],source='Sentinel-2 L2A RGB',nominal_spacing_m=10,
                    display_revision=VISUAL_REVISION,
                    display_adjustment=dict(contrast=1.18,saturation=1.20,gamma=.94,sharpen_percent=45),
                    orientation='north-up; west=u0; north=v1 (COLLADA)',
                    water_on_land_corrected_pixels=corrected,
                    collision_changed=False)
    except Exception:return None


def warm_cell(i, j):
    """All remote / large GIS work is outside the terrain safety worker."""
    import zipfile
    import httpx
    import shapely
    from shapely.geometry import box,shape,mapping
    import pyogrio.raw
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.transform import from_bounds
    from rasterio.enums import Resampling
    from PIL import Image
    from .terrain_sources import cached_download
    folder = directory(i,j);folder.mkdir(exist_ok=True)
    bounds = [i*CELL,j*CELL,(i+1)*CELL,(j+1)*CELL]
    archive = Path(os.getenv('WWOS_COAST_OSM_ARCHIVE',''))
    if not archive.is_file():archive,_ = cached_download(LAND_URL,'coastal-land-polygons.zip',1500*1024**2,900)
    with zipfile.ZipFile(archive) as z:member = next(n for n in z.namelist() if n.endswith('.shp'))
    metadata,_,geometries,_ = pyogrio.raw.read('/vsizip/'+str(archive.resolve())+'/'+member,bbox=tuple(bounds),columns=[])
    if metadata['crs']!='EPSG:4326':raise ValueError('coast_crs_mismatch')
    land = shapely.union_all([shapely.from_wkb(g) for g in geometries]).intersection(box(*bounds))
    if not land.is_valid:raise ValueError('coast_invalid_source_geometry')
    temporary = folder/('build-'+uuid.uuid4().hex);temporary.mkdir()
    try:
        (temporary/'land.json').write_text(json.dumps(mapping(land)))
        hashes = {'land.json':digest(temporary/'land.json')}
        if land.is_empty:
            (temporary/'land.json').replace(folder/'land.json')
            atomic(folder/'manifest.json',dict(revision=REVISION,cell=[i,j],bounds=bounds,status='no_land',hashes=hashes,osm_source=LAND_URL))
            return
        response = httpx.post(STAC,json=imagery_query(bounds),timeout=30)
        response.raise_for_status();features = response.json()['features']
        candidates = [f for f in features if shape(f['geometry']).covers(box(*bounds))]
        best = None;pixels = 560;affine = from_bounds(*bounds,pixels,pixels)
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif',GDAL_HTTP_TIMEOUT='15',GDAL_HTTP_MAX_RETRY='1'):
            for item in candidates[:12]:
                visual,quality = item['assets']['visual']['href'],item['assets']['scl']['href']
                if not all(u.startswith('https://sentinel-cogs.s3.') for u in [visual,quality]):raise ValueError('unexpected_sentinel_asset_host')
                with rasterio.open(visual) as src,WarpedVRT(src,crs='EPSG:4326',transform=affine,width=pixels,height=pixels,resampling=Resampling.bilinear) as vrt:values = vrt.read([1,2,3])
                with rasterio.open(quality) as src,WarpedVRT(src,crs='EPSG:4326',transform=affine,width=pixels,height=pixels,resampling=Resampling.nearest) as vrt:scl = vrt.read(1)
                bad = float(np.mean(np.isin(scl,[0,1,3,8,9,10,11])|(np.max(values,axis=0)==0)))
                if best is None or bad<best[0]:best = (bad,item,values)
                if bad<.05:
                    best=(bad,item,values);break  # Newest usable local scene.
        if best is None or best[0]>.05:raise ValueError('sentinel_cloud_or_coverage')
        bad,item,values = best
        Image.fromarray(np.moveaxis(values,0,-1)).save(temporary/'satellite.png')
        hashes['satellite.png'] = digest(temporary/'satellite.png')
        for name in hashes:(temporary/name).replace(folder/name)
        atomic(folder/'manifest.json',dict(revision=REVISION,cell=[i,j],bounds=bounds,status='ready',hashes=hashes,
              osm_source=LAND_URL,sentinel_item=item['id'],sentinel_acquisition=item['properties']['datetime'],
              imagery_revision=IMAGERY_REVISION,
              sentinel_asset=item['assets']['visual']['href'],invalid_fraction=bad,created_unix=time.time(),
              attribution='© OpenStreetMap contributors, ODbL; modified Copernicus Sentinel data'))
        (folder/'failure.json').unlink(missing_ok=True)
    finally:shutil.rmtree(temporary,ignore_errors=True)


def warm():
    lock = root()/'warmer.lock'
    try:lock.mkdir()
    except FileExistsError:return
    try:
        os.nice(10)
        atomic(lock/'owner.json',dict(pid=os.getpid(),started_unix=time.time()))
        deadline = time.monotonic()+900
        while time.monotonic()<deadline:
            queued = sorted(root().glob('atlas_*/request.json'),key=lambda p:p.stat().st_mtime)
            if not queued:break
            p = queued[0];value = json.loads(p.read_text());p.unlink(missing_ok=True)
            try:warm_cell(value['i'],value['j']);print(p.parent.name+' ready',flush=True)
            except Exception as error:
                atomic(p.parent/'failure.json',dict(reason=type(error).__name__+':'+str(error)[:240],retry_unix=time.time()+600))
                print(p.parent.name+' baseline fallback: '+str(error)[:240],flush=True)
    finally:shutil.rmtree(lock,ignore_errors=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warm',action='store_true')
    parser.add_argument('--cell',nargs=2,type=int)
    args = parser.parse_args()
    if args.cell:warm_cell(*args.cell)
    else:warm()
