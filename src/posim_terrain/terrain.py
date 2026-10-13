"""Bounded GEBCO 2026 OPeNDAP extraction and reproducible ENU terrain artifacts."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

import numpy as np
from pyproj import CRS, Transformer
from scipy.interpolate import RegularGridInterpolator

SOURCE = 'https://dap.ceda.ac.uk/thredds/dodsC/bodc/gebco/global/gebco_2026/ice_surface_elevation/netcdf/GEBCO_2026.nc'
DOI = '10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa'
from .engine_identity import revision
POSIM = revision()
VERSION = 8

def validate(spec):
    for k in ('latitude', 'longitude', 'width_m', 'height_m'):
        if not isinstance(spec.get(k), (int, float)) or not math.isfinite(spec[k]): raise ValueError('invalid_' + k)
    if not -75 <= spec['latitude'] <= 75: raise ValueError('latitude_limit_75_degrees')
    if not -180 < spec['longitude'] < 180: raise ValueError('invalid_longitude')
    if not all(1000 <= spec[k] <= 20000 for k in ('width_m', 'height_m')): raise ValueError('region_limit_1_to_20_km')
    if spec.get('samples', 129) not in (33, 65, 129, 257): raise ValueError('invalid_samples')
    if spec.get('source_policy','auto') not in ('auto','gebco'): raise ValueError('invalid_source_policy')
    return dict(spec, samples=spec.get('samples',129), source_policy=spec.get('source_policy','auto'))

def projection(spec):
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={spec['latitude']} +lon_0={spec['longitude']} +datum=WGS84 +units=m")
    forward=Transformer.from_crs('EPSG:4326',local,always_xy=True)
    inverse=Transformer.from_crs(local,'EPSG:4326',always_xy=True)
    if '_native_frame' in spec:
        from .floating_origin import FramedTransform
        forward=FramedTransform(forward,spec['_native_frame'],False)
        inverse=FramedTransform(inverse,spec['_native_frame'],True)
    return local,forward,inverse

def parse_ascii(text, rows, cols):
    sections = text.split('---------------------------------------------',1)
    if len(sections)!=2: raise ValueError('invalid_gebco_response')
    body=sections[1]
    grid = re.search(r'elevation\.elevation\[\d+\]\[\d+\]\n(.*?)\n\nelevation.lat',body,re.S)
    if not grid: raise ValueError('missing_elevation')
    data=[]
    for line in grid.group(1).strip().splitlines():
        if not re.match(r'^\[\d+\],',line): raise ValueError('invalid_grid_row')
        data.append([float(v) for v in line.split(',',1)[1].split(',')])
    def axis(name):
        match=re.search(r'elevation\.'+name+r'\[\d+\]\n([^\n]+)',body)
        if not match: raise ValueError('missing_'+name)
        return np.array([float(v) for v in match.group(1).split(',')])
    values=np.array(data); lat=axis('lat'); lon=axis('lon')
    if values.shape!=(rows,cols) or len(lat)!=rows or len(lon)!=cols: raise ValueError('source_shape_mismatch')
    if not np.isfinite(values).all() or np.min(values)<-12000 or np.max(values)>10000: raise ValueError('invalid_elevation_values')
    if not (np.diff(lat)>0).all() or not (np.diff(lon)>0).all(): raise ValueError('source_axis_order')
    return lat,lon,values


def gebco_source(requested_url, window):
    """Reuse a checksum-verified covering subset of this same GEBCO release.

    Keep the original URL, raw samples and hash as provenance. Wider cached
    grids can cover a new small region during a provider outage without making
    up missing depths or mislabelling an old response as a different query.
    """
    from .terrain_sources import cached_download,cache_root
    root=cache_root();a,b,c,d=window
    candidates=[]
    def candidate(url,path,digest):
        if not isinstance(url,str) or not url.startswith(SOURCE+'.ascii?'):return
        query=urllib.parse.unquote(urllib.parse.urlsplit(url).query)
        match=re.fullmatch(r'elevation\[(\d+):1:(\d+)\]\[(\d+):1:(\d+)\]',query)
        if not match or not path.is_file() or path.stat().st_size>4_000_000:return
        x,y,u,v=map(int,match.groups())
        if 0<=x<=a<=b<=y<43200 and 0<=u<=c<=d<=v<86400:
            candidates.append(((y-x+1)*(v-u+1),url,path,digest,(x,y,u,v)))
    assets=Path(os.environ.get('WWOS_ASSETS_DIR',os.environ.get('WWOS_DATA_DIR','/home/docker/wwos-data')))
    for prior in (assets/'terrain').glob('*/manifest.json'):
        try:
            metadata=json.loads(prior.read_text())
            candidate(metadata.get('subset_url'),prior.with_name('source.ascii'),metadata.get('source_sha256'))
        except (ValueError,OSError):continue
    for metadata_path in root.glob('gebco-2026-*.ascii.json'):
        try:
            metadata=json.loads(metadata_path.read_text())
            candidate(metadata.get('url'),metadata_path.with_suffix(''),metadata.get('sha256'))
        except (ValueError,OSError):continue
    for _,url,path,digest,(x,y,u,v) in sorted(candidates,key=lambda value:value[0]):
        try:
            raw=path.read_bytes()
            if not digest or hashlib.sha256(raw).hexdigest()!=digest:continue
            lat,lon,values=parse_ascii(raw.decode('ascii'),y-x+1,v-u+1)
            if not np.allclose(lat,-90+(np.arange(x,y+1)+.5)/240,rtol=0,atol=1e-5):continue
            if not np.allclose(lon,-180+(np.arange(u,v+1)+.5)/240,rtol=0,atol=1e-5):continue
            cache=root/('gebco-2026-'+hashlib.sha256(url.encode()).hexdigest()[:24]+'.ascii')
            if not cache.exists() or hashlib.sha256(cache.read_bytes()).hexdigest()!=digest:
                import uuid
                temporary=cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.part')
                try:temporary.write_bytes(raw);temporary.replace(cache)
                finally:temporary.unlink(missing_ok=True)
            cached_download(url,cache.name,4_000_000,60)
            return raw,lat,lon,values,url
        except (ValueError,OSError,UnicodeError):continue
    name='gebco-2026-'+hashlib.sha256(requested_url.encode()).hexdigest()[:24]+'.ascii'
    try:path,_=cached_download(requested_url,name,4_000_000,60)
    except (urllib.error.URLError,TimeoutError,ConnectionError) as error:
        if isinstance(error,urllib.error.HTTPError) and error.code not in (429,500,502,503,504):raise
        # Retry once with a bounded surrounding subset of the SAME release.
        # Its verified raw samples also supply subsequent neighbouring tiles,
        # instead of repeatedly hitting a failing tiny-subset request. Never
        # extrapolate outside the returned grid or invent missing depths.
        x,y,u,v=max(0,a-64),min(43199,b+64),max(0,c-64),min(86399,d+64)
        if (y-x+1)*(v-u+1)>50000:raise
        wider=SOURCE+'.ascii?'+urllib.parse.quote(f'elevation[{x}:1:{y}][{u}:1:{v}]',safe=':')
        broader_name='gebco-2026-'+hashlib.sha256(wider.encode()).hexdigest()[:24]+'.ascii'
        path,_=cached_download(wider,broader_name,4_000_000,60)
        raw=path.read_bytes();lat,lon,values=parse_ascii(raw.decode('ascii'),y-x+1,v-u+1)
        return raw,lat,lon,values,wider
    raw=path.read_bytes();lat,lon,values=parse_ascii(raw.decode('ascii'),b-a+1,d-c+1)
    return raw,lat,lon,values,requested_url

def sample_surface(xs,ys,elevation,x,y):
    """Height on the actual a-b-d / a-d-c triangles written to the SDF mesh."""
    i=int(np.clip(np.searchsorted(xs,x)-1,0,len(xs)-2));j=int(np.clip(np.searchsorted(ys,y)-1,0,len(ys)-2))
    tx=(x-xs[i])/(xs[i+1]-xs[i]);ty=(y-ys[j])/(ys[j+1]-ys[j])
    a=elevation[j,i];b=elevation[j,i+1];c=elevation[j+1,i];d=elevation[j+1,i+1]
    return float(a*(1-tx)+b*(tx-ty)+d*ty if tx>=ty else a*(1-ty)+d*tx+c*(ty-tx))


def write_mesh(path, xs, ys, heights, normals=None, *, uv_scale=4, uv_offset=(0,0), face_mask=None):
    n=len(xs)
    vertices=' '.join(f'{x:.5f} {y:.5f} {heights[j,i]:.5f}' for j,y in enumerate(ys) for i,x in enumerate(xs))
    if normals is None:
        dy,dx=np.gradient(heights,ys,xs)
        normals=np.stack((-dx,-dy,np.ones_like(heights)),axis=-1)
        normals/=np.linalg.norm(normals,axis=-1,keepdims=True)
    normal_values=' '.join(f'{v:.8f}' for v in normals.ravel())
    # Absolute metre coordinates keep the synthetic 4 m texture tile continuous.
    scale=np.broadcast_to(uv_scale,(2,))
    uv=' '.join(f'{(x+uv_offset[0])/scale[0]:.8f} {(y+uv_offset[1])/scale[1]:.8f}' for y in ys for x in xs)
    triangles=[]
    for j in range(n-1):
        for i in range(n-1):
            a=j*n+i;b=a+1;c=a+n;d=c+1
            triangles.extend((a,b,d,a,d,c))
    faces=np.asarray(triangles).reshape(-1,3)
    if face_mask is not None:faces=faces[np.asarray(face_mask,dtype=bool)]
    triangles=faces.ravel().tolist()
    path.write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><created>2026-10-02T00:00:00Z</created><modified>2026-10-02T00:00:00Z</modified><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>
<library_geometries><geometry id="terrain" name="GEBCO"><mesh>
<source id="positions"><float_array id="positions-array" count="{n*n*3}">{vertices}</float_array><technique_common><accessor source="#positions-array" count="{n*n}" stride="3"><param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>
<source id="normals"><float_array id="normals-array" count="{n*n*3}">{normal_values}</float_array><technique_common><accessor source="#normals-array" count="{n*n}" stride="3"><param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>
<source id="uv"><float_array id="uv-array" count="{n*n*2}">{uv}</float_array><technique_common><accessor source="#uv-array" count="{n*n}" stride="2"><param name="S" type="float"/><param name="T" type="float"/></accessor></technique_common></source>
<vertices id="vertices"><input semantic="POSITION" source="#positions"/></vertices>
<triangles count="{len(faces)}"><input semantic="VERTEX" source="#vertices" offset="0"/><input semantic="NORMAL" source="#normals" offset="1"/><input semantic="TEXCOORD" source="#uv" offset="2" set="0"/><p>{' '.join(str(index) for index in triangles for _ in range(3))}</p></triangles>
</mesh></geometry></library_geometries><library_visual_scenes><visual_scene id="Scene"><node id="terrain-node"><instance_geometry url="#terrain"/></node></visual_scene></library_visual_scenes><scene><instance_visual_scene url="#Scene"/></scene></COLLADA>''')

def longitude_groups(lon):
    """Bound provider windows on either side of the international date line."""
    if np.ptp(lon)>180:return [lon>=0,lon<0]
    return [np.ones(lon.shape,dtype=bool)]

def gebco_grid(lon,lat,out):
    """Join real edge pixels periodically; never clamp or invent seam depths."""
    a=max(0,math.floor((lat.min()+90)*240-.5)-1);b=min(43199,math.ceil((lat.max()+90)*240-.5)+1)
    seam=np.ptp(lon)>180 or lon.min()<-179.9979 or lon.max()>179.9979
    windows=[]
    if seam:
        east=lon[lon>=0];west=lon[lon<0]
        c=max(0,math.floor((east.min()+180)*240-.5)-1) if east.size else 86398
        d=min(86399,math.ceil((west.max()+180)*240-.5)+1) if west.size else 1
        windows=[(c,86399),(0,d)]
    else:windows=[(max(0,math.floor((lon.min()+180)*240-.5)-1),min(86399,math.ceil((lon.max()+180)*240-.5)+1))]
    if sum((b-a+1)*(d-c+1) for c,d in windows)>50000:raise ValueError('source_subset_too_large')
    records=[];grids=[];axes=[];raws=[]
    for i,(c,d) in enumerate(windows):
        query=f'elevation[{a}:1:{b}][{c}:1:{d}]';requested=SOURCE+'.ascii?'+urllib.parse.quote(query,safe=':')
        raw,slat,slon,source,url=gebco_source(requested,(a,b,c,d))
        name='source.ascii' if not seam else f'source-{i}.ascii';(out/name).write_bytes(raw)
        records.append(dict(url=url,requested_url=requested,file=name,sha256=hashlib.sha256(raw).hexdigest()))
        grids.append(source);axes.append(slon+(360 if seam and i else 0));raws.append(raw)
    slon=np.concatenate(axes);source=np.concatenate(grids,axis=1)
    queries=np.where(lon<0,lon+360,lon) if seam else lon
    interp=RegularGridInterpolator((slat,slon),source,bounds_error=True)
    heights=interp(np.stack((lat,queries),axis=-1))
    return heights,slat,slon,source,records,raws

def world_template():
    """Use the installed engine's package names across the POSIM rename."""
    from ament_index_python.packages import get_package_share_directory,PackageNotFoundError
    try:
        share=get_package_share_directory('posim_worlds');name='posim_ocean_waves.world'
    except PackageNotFoundError:
        share=get_package_share_directory('dave_worlds');name='dave_ocean_waves.world'
    return Path(share)/'worlds'/name

def build(spec, out, *, engine_world=True, validate_sdf=True, numeric_only=False):
    if numeric_only and (engine_world or validate_sdf):
        raise ValueError('numeric_only_requires_no_engine_or_sdf_validation')
    spec=validate(spec);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    local, forward, inverse=projection(spec)
    n=spec['samples'];xs=np.linspace(-spec['width_m']/2,spec['width_m']/2,n);ys=np.linspace(-spec['height_m']/2,spec['height_m']/2,n)
    # Internal worker-only canonical lattice window. Never accepted by the API.
    if '_stream_grid' in spec:
        window=spec['_stream_grid'];xs=np.array(window['xs']);ys=np.array(window['ys']);n=len(xs)
        if n!=len(ys) or not 5<=n<=257 or not (np.diff(xs)>0).all() or not (np.diff(ys)>0).all():raise ValueError('invalid_stream_lattice')
    xx,yy=np.meshgrid(xs,ys);lon,lat=inverse.transform(xx,yy)
    # GEBCO is a pixel-centred grid: first centre is -90/-180 + 1/480 degrees.
    a=max(0,math.floor((lat.min()+90)*240-.5)-1); b=min(43199,math.ceil((lat.max()+90)*240-.5)+1)
    c=max(0,math.floor((lon.min()+180)*240-.5)-1);d=min(86399,math.ceil((lon.max()+180)*240-.5)+1)
    query=f'elevation[{a}:1:{b}][{c}:1:{d}]';url=SOURCE+'.ascii?'+urllib.parse.quote(query,safe=':')
    requested_url=url
    baseline_status={'provider':'GEBCO_2026','status':'available'}
    raw=None;slat=np.array([]);slon=np.array([]);source=np.empty((0,0))
    try:
        heights,slat,slon,source,subsets,raws=gebco_grid(lon,lat,out)
        url=subsets[0]['url'] if len(subsets)==1 else None
        requested_url=subsets[0]['requested_url'] if len(subsets)==1 else None
        raw=raws[0] if len(raws)==1 else None
        baseline_status.update(covering_subset_reused=any(s['url']!=s['requested_url'] for s in subsets),subsets=subsets,date_line_joined=len(subsets)>1)
    except Exception as error:
        if spec['source_policy']=='gebco':raise
        from .terrain_sources import error_reason
        baseline_status.update(status='unavailable',reason=error_reason(error))
        heights=np.full(lon.shape,np.nan)
    providers=[];provider=np.zeros(heights.shape,dtype=np.uint8);water_mask=np.full(heights.shape,-1,dtype=np.int8)
    detail={'status':'baseline','fallback':True,'reason':'gebco_only'}
    land_texture=None
    if spec['source_policy']=='auto':
        from .terrain_sources import copernicus,combine,coastal_bathymetry
        land=np.full(lon.shape,np.nan);cop_records=[]
        groups=longitude_groups(lon)
        for group in groups:
            values,mask,record=copernicus(lon[group],lat[group]);land[group]=values;water_mask[group]=mask;cop_records.append(record)
        baseline,coastal=coastal_bathymetry(heights,water_mask,slon,slat,source,lon,lat,forward)
        heights,provider=combine(baseline,land,water_mask)
        provider[coastal]=1
        providers=cop_records
        from .bathymetry import resolve
        for group in groups:
            values,ids,seabed_records=resolve(heights[group],provider[group],water_mask[group],lon[group],lat[group])
            heights[group]=values;provider[group]=ids;providers.extend(seabed_records)
        from .canal_override import apply
        heights,provider,canal_record=apply(heights,provider,lon,lat)
        if canal_record:providers.append(canal_record)
        from .coastal_detail import upgrade,paint
        original_heights,original_provider=heights,provider
        heights,provider,detail=upgrade(spec,xs,ys,heights,provider,water_mask,lon,lat,slon,slat,source,cop_records)
        if detail['status']=='applied':
            land_texture=paint(out,spec,xs,ys,heights)
            if land_texture is None:
                heights,provider=original_heights,original_provider
                detail={'status':'baseline','fallback':True,'reason':'land_texture_generation_failed'}
        # Imagery is a visual layer, independent of whether the coastline
        # candidate passed its stricter elevation/collision preflight.
        if land_texture is None:
            land_texture=paint(out,spec,xs,ys,heights)
    if not np.isfinite(heights).all():
        (out/'source-providers.json').write_text(json.dumps({'providers':providers,'baseline_status':baseline_status,'missing_vertices':int((~np.isfinite(heights)).sum())},indent=2))
        raise ValueError('terrain_source_gaps_without_baseline_'+str(int((~np.isfinite(heights)).sum())))
    centre=float(RegularGridInterpolator((ys,xs),heights)([[(ys[0]+ys[-1])/2,(xs[0]+xs[-1])/2]])[0])
    counts={'GEBCO_2026':int((provider<2).sum()),'Copernicus_GLO30':int((provider==2).sum())}
    from .bathymetry import SOURCE_IDS,config_fingerprint
    counts.update({name:int((provider==identifier).sum()) for identifier,name in SOURCE_IDS.items()})
    active=[name for name,count in counts.items() if count]
    if detail['status']=='applied':active.append('OSM coastline')
    if land_texture:active.append('Sentinel-2 RGB')

    meta={'schema':VERSION,'source':' + '.join(active),'source_policy':spec['source_policy'],'source_config':config_fingerprint(spec),'source_counts':counts,'providers':providers,'baseline_status':baseline_status,'selection_policy':'Cesium permitted MSL export first; remaining valid seabed by native spacing; GEBCO gaps; Copernicus GLO30 land','coastal_regridded_vertices':int((provider==1).sum()),'coastal_support_limit_m':1500,'source_ids':{'0':'GEBCO_2026','1':'GEBCO_2026 marine interpolation guided by Copernicus ocean mask','2':'Copernicus_GLO30',**{str(k):v for k,v in SOURCE_IDS.items()}},'baseline_source':'GEBCO_2026','source_url':SOURCE,'subset_url':url,'doi':DOI,
        'requested_subset_url':requested_url,'attribution':'GEBCO Bathymetric Compilation Group 2026 (2026). The GEBCO_2026 Grid.',
        'source_sha256':hashlib.sha256(raw).hexdigest() if raw is not None else None,'posim_commit':POSIM,'spec':spec,
        'projection_wkt':local.to_wkt(),'axes':'x east, y north, z up; metres','sea_level_m':0,
        'source_interval_arc_seconds':15,'source_shape':list(source.shape),'mesh_shape':[n,n],
        'minimum_m':float(heights.min()),'maximum_m':float(heights.max()),'centre_elevation_m':centre,
        'land_vertices':int((heights>=0).sum()),'ocean_vertices':int((heights<0).sum()),
        'spacing_m':[float(xs[1]-xs[0]),float(ys[1]-ys[0])],
        'limitations':['Interpolation does not increase source accuracy.','GEBCO assumes mean sea level; local vertical datum differences exist.','Not for navigation.','Small local AEQD region; not an exact global ENU frame.','Copernicus GLO30 is land DSM, not bathymetry; EGM2008 heights use an explicit MSL approximation.','Water mask and surveys differ in date; shoreline seams and coarse mesh sampling remain.','Coastal ocean pixels with positive GEBCO elevation use interpolated negative GEBCO samples within 1.5 km; not a new bathymetric survey.','Cesium streamed terrain is not exported; a permitted numeric MSL export is required.','Marine SDB samples of quality 3/4 or unknown are rejected; NoData falls back per vertex.','Output mesh spacing limits represented detail even when sources are finer.'],
        'robot_spawn_valid':centre<-2,'suggested_robot_z':-1 if centre<-2 else None}
    (out/'source-providers.json').write_text(json.dumps({'providers':providers,'source_counts':counts},indent=2))
    (out/'manifest.json').write_text(json.dumps(meta,indent=2))
    meta['coastal_detail']=detail
    np.savez_compressed(out/'projected.npz',xs=xs,ys=ys,elevation=heights,latitude=lat,longitude=lon,provider=provider,water_mask=water_mask)
    if numeric_only:
        meta.update(engine_world=False,sdf_validation='Numeric terrain only; no SDF exported')
        (out/'manifest.json').write_text(json.dumps(meta,indent=2))
        return meta
    write_mesh(out/'terrain.dae',xs,ys,heights)
    if engine_world:
        tree=ET.parse(world_template())
    else:
        tree=ET.ElementTree(ET.fromstring('<sdf version="1.10"><world name="posim_terrain"><spherical_coordinates/></world></sdf>'))
    world=tree.getroot().find('world');world.set('name','wwos_gebco')
    for child in list(world):
        if child.tag in ('include','model','gui'):world.remove(child)
    coords=world.find('spherical_coordinates');coords.clear()
    for tag,value in [('surface_model','EARTH_WGS84'),('world_frame_orientation','ENU'),('latitude_deg',spec['latitude']),('longitude_deg',spec['longitude']),('elevation',0),('heading_deg',0)]:ET.SubElement(coords,tag).text=str(value)
    model=ET.SubElement(world,'model',name='gebco_terrain');ET.SubElement(model,'static').text='true';link=ET.SubElement(model,'link',name='terrain')
    for kind in ('collision','visual'):
        element=ET.SubElement(link,kind,name='gebco_'+kind);geometry=ET.SubElement(element,'geometry');mesh=ET.SubElement(geometry,'mesh');ET.SubElement(mesh,'uri').text=str((out/'terrain.dae').resolve())
        if kind=='visual':
            material=ET.SubElement(element,'material');ET.SubElement(material,'ambient').text='0.32 0.48 0.35 1';ET.SubElement(material,'diffuse').text='0.45 0.62 0.43 1'
    # IMU/magnetometer systems are needed by BlueROV, as in the upstream configuration.
    for filename,name in [('gz-sim-imu-system','gz::sim::systems::Imu'),('gz-sim-magnetometer-system','gz::sim::systems::Magnetometer')]:
        if not any(p.get('name')==name for p in world.findall('plugin')): ET.SubElement(world,'plugin',filename=filename,name=name)
    from .seabed_visual import decorate
    decorate(tree, out, xs, ys, heights,land_texture=land_texture)
    meta['visual_aid']={'synthetic_texture':True,'grid_spacing_m':1,'collision_changed':False,'land_texture':land_texture}
    (out/'terrain.json').write_text(json.dumps({'xs':xs.tolist(),'ys':ys.tolist(),'heights':heights.tolist(),'land_texture':land_texture},separators=(',',':')))
    (out/'manifest.json').write_text(json.dumps(meta,indent=2))
    ET.indent(tree);tree.write(out/'world.sdf',encoding='utf-8',xml_declaration=True)
    if validate_sdf:
        result=subprocess.run(['gz','sdf','-k',str(out/'world.sdf')],capture_output=True,text=True,timeout=20)
        (out/'sdf-validation.txt').write_text(result.stdout+result.stderr)
        if result.returncode:raise ValueError('generated_sdf_invalid')
    meta['engine_world']=engine_world
    meta['sdf_validation']='gz sdf -k passed' if validate_sdf else 'XML exported; Gazebo validation not requested'
    (out/'manifest.json').write_text(json.dumps(meta,indent=2))
    return meta

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--spec',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    build(json.loads(Path(args.spec).read_text()),args.output)
