"""Numeric seabed providers; no coloured map pixels are used as elevations.

Cesium exports must be supplied with permission and a declared MSL conversion.
Other sources are bounded, public scientific downloads with per-vertex fallback.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import urllib.error
import urllib.parse
import zipfile
import shutil
import uuid
import numpy as np
from scipy.interpolate import RegularGridInterpolator
from .terrain_sources import cached_download, error_reason, raster_samples

MARINE_PRODUCT='BATHYMETRY_GLO_PHY_COASTAL_L4_MY_016_001'
MARINE_RELEASE='cmems_obs-sdb_glo_phy_comp_my_100m-l4-s2_static_202511'
MARINE_STAC=f'https://stac.marine.copernicus.eu/metadata/{MARINE_PRODUCT}/{MARINE_RELEASE}/dataset.stac.json'
KHOA_URL='https://www.khoa.go.kr/oceangrid/gis/category/seaface/downloadNcFile.do'
SOURCE_IDS={3:'Cesium_World_Bathymetry',4:'Copernicus_Marine_SDB_202511',5:'EMODnet_DTM_2024',6:'KHOA_BADA2024',
            7:'Suez_assumed_water_10m',8:'Suez_OSM_modelled_bank'}


def empty(lon):return np.full(lon.shape,np.nan)


def local_export(name,lon,lat):
    """Operator supplied numeric raster and metadata, never credentials from users."""
    record={'provider':name,'status':'not_configured','assets':[]}
    env={'Cesium_World_Bathymetry':'WWOS_CESIUM_EXPORT','EMODnet_DTM_2024':'WWOS_EMODNET_EXPORT'}[name]
    path=os.environ.get(env,'')
    if not path:
        if name.startswith('Cesium'):
            record['reason']='permitted_numeric_export_required_for_persisted_gazebo_geometry'
            record['asset_id']=2426648
        return empty(lon),record
    try:
        path=Path(path);meta=json.loads(path.with_suffix(path.suffix+'.json').read_text())
        if meta.get('provider')!=name or meta.get('vertical_datum')!='MSL' or meta.get('units')!='m' or meta.get('positive')!='up':
            raise ValueError('export_requires_MSL_metres_positive_up_metadata')
        spacing=float(meta['native_spacing_m'])
        if not math.isfinite(spacing) or spacing<=0:raise ValueError('invalid_source_spacing')
        if not meta.get('release') or not meta.get('attribution'):raise ValueError('missing_release_or_attribution')
        if name.startswith('Cesium') and not meta.get('permission_reference'):
            raise ValueError('cesium_export_permission_reference_required')
        # The metadata must identify an actual permission / permitted clip, not an API token.
        values=raster_samples(path,lon,lat)
        if path.stat().st_size>2*1024**3:raise ValueError('export_size_limit')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            while chunk:=stream.read(1024*1024):digest.update(chunk)
        record.update(status='available' if np.isfinite(values).any() else 'no_coverage',
                      native_spacing_m=spacing,vertical_datum='MSL',release=meta['release'],
                      attribution=meta['attribution'],assets=[{'sha256':digest.hexdigest(),'bytes':path.stat().st_size}],
                      permission_reference=meta.get('permission_reference'))
        return values,record
    except Exception as error:return empty(lon),dict(record,status='unavailable',reason=error_reason(error))


def config_fingerprint():
    """Source configuration changes invalidate cached worlds without exposing paths."""
    values=[]
    for name in ('WWOS_CESIUM_EXPORT','WWOS_EMODNET_EXPORT'):
        path=Path(os.environ.get(name,''))
        if not os.environ.get(name):continue
        for p in (path,path.with_suffix(path.suffix+'.json')):
            values.append([name,str(p),p.stat().st_size,p.stat().st_mtime_ns] if p.is_file() else [name,str(p),'missing'])
    from .canal_override import fingerprint
    values.append(['Suez_simulation_override',fingerprint()])
    from .coastal_detail import REVISION,fingerprint as detail_fingerprint
    values.append(['coastal_detail',REVISION,os.getenv('WWOS_COASTAL_DETAIL','1'),detail_fingerprint()])
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()[:20]


def zarr_window(base,metadata,variable,r0,r1,c0,c1,assets):
    """Read only intersecting v2 chunks (maximum 16), with bounded decompression."""
    from numcodecs import get_codec
    info=metadata[variable+'/.zarray'];attrs=metadata[variable+'/.zattrs']
    if info['zarr_format']!=2 or attrs['_ARRAY_DIMENSIONS']!=['latitude','longitude'] or info.get('filters'):
        raise ValueError('unsupported_marine_zarr_layout')
    cr,cc=info['chunks'];dtype=np.dtype(info['dtype'])
    if cr*cc*dtype.itemsize>8*1024**2:raise ValueError('marine_decompression_limit')
    chunks=[(r,c) for r in range(r0//cr,(r1-1)//cr+1) for c in range(c0//cc,(c1-1)//cc+1)]
    if len(chunks)>16:raise ValueError('marine_chunk_limit')
    fill=info['fill_value'];fill=np.nan if fill=='NaN' else fill
    output=np.full((r1-r0,c1-c0),fill,dtype=dtype)
    for r,c in chunks:
        key=f'{variable}/{r}.{c}';url=base+'/'+key
        try:
            p,asset=cached_download(url,'marine-'+hashlib.sha256(url.encode()).hexdigest()[:24]+'.chunk',8*1024**2,30)
        except urllib.error.HTTPError as error:
            if error.code==404:continue # Zarr omitted all-fill chunk, not invented coverage.
            raise
        raw=p.read_bytes();decoded=get_codec(info['compressor']).decode(raw) if info['compressor'] else raw
        if len(decoded)!=cr*cc*dtype.itemsize:raise ValueError('marine_chunk_shape')
        data=np.frombuffer(decoded,dtype=dtype).reshape((cr,cc),order=info['order'])
        a=max(r0,r*cr);b=min(r1,(r+1)*cr);x=max(c0,c*cc);y=min(c1,(c+1)*cc)
        output[a-r0:b-r0,x-c0:y-c0]=data[a-r*cr:b-r*cr,x-c*cc:y-c*cc]
        assets.append(asset)
    return output


def marine(lon,lat):
    record={'provider':SOURCE_IDS[4],'native_spacing_m':100,'native_spacing_arc_seconds':3.75,
      'vertical_datum':'MSL (FES2014)','release':MARINE_RELEASE,'quality_maximum':2,
      'attribution':'E.U. Copernicus Marine Service Information; DOI 10.48670/mds-00364; modified by regional sampling.',
      'assets':[]}
    try:
        path,asset=cached_download(MARINE_STAC,'marine-202511-dataset.json',2*1024**2,30)
        dataset=json.loads(path.read_text());record['assets'].append(asset)
        item=dataset['assets']['static'];base=item['href']
        if not base.startswith('https://s3.waw3-1.cloudferro.com/mdl-arco-time-003/arco/'+MARINE_PRODUCT+'/'):
            raise ValueError('unexpected_marine_asset_host')
        path,asset=cached_download(base+'/.zmetadata','marine-202511-zmetadata.json',2*1024**2,30)
        metadata=json.loads(path.read_text())['metadata'];record['assets'].append(asset)
        dims=item['viewDims'];la=dims['latitude']['coords'];lo=dims['longitude']['coords']
        r0=max(0,math.floor((float(lat.min())-la['min'])/la['step'])-1)
        r1=min(la['len'],math.ceil((float(lat.max())-la['min'])/la['step'])+2)
        c0=max(0,math.floor((float(lon.min())-lo['min'])/lo['step'])-1)
        c1=min(lo['len'],math.ceil((float(lon.max())-lo['min'])/lo['step'])+2)
        if (r1-r0)*(c1-c0)>500000:raise ValueError('marine_window_limit')
        height=zarr_window(base,metadata,'height',r0,r1,c0,c1,record['assets']).astype(float)
        quality=zarr_window(base,metadata,'Quality_indicator',r0,r1,c0,c1,record['assets'])
        finite=np.isfinite(height)
        valid=finite&(height>=-100)&(height<=10)&(quality>=0)&(quality<=2)
        height[~valid]=np.nan
        axes=(la['min']+np.arange(r0,r1)*la['step'],lo['min']+np.arange(c0,c1)*lo['step'])
        # Nearest valid native pixel: do not bridge NoData or quality-rejected coastal gaps.
        result=RegularGridInterpolator(axes,height,method='nearest',bounds_error=False,fill_value=np.nan)(np.stack((lat,lon),axis=-1))
        record.update(status='available' if np.isfinite(result).any() else 'no_coverage',
                      window_shape=list(height.shape),valid_native_pixels=int(valid.sum()),
                      rejected_native_pixels=int((finite&~valid).sum()))
        return result,record
    except Exception as error:return empty(lon),dict(record,status='unavailable',reason=error_reason(error))


def khoa(lon,lat):
    record={'provider':SOURCE_IDS[6],'native_spacing_m':150,'vertical_datum':'MSL',
      'release':'BADA2024','attribution':'Korea Hydrographic and Oceanographic Agency, BADA2024 MSL 150 m; modified by regional sampling.','assets':[]}
    # Bounds are a request prefilter; the file's coordinates and NoData remain authoritative.
    if lon.max()<120 or lon.min()>136 or lat.max()<30 or lat.min()>43:
        return empty(lon),dict(record,status='outside_region')
    try:
        from netCDF4 import Dataset
        path,asset=cached_download(KHOA_URL,'KHOA_BADA2024_MSL_150m.nc',400*1024**2,180)
        record['assets'].append(asset)
        from scipy.interpolate import LinearNDInterpolator
        from scipy.spatial import cKDTree
        from pyproj import CRS,Transformer
        pieces=[]
        with Dataset(path) as data:
            if set(data.variables)!={'Lat','Lon','MSL'}:raise ValueError('unsupported_khoa_release_schema')
            for a in range(0,len(data['Lat']),500000):
                ys=np.ma.asarray(data['Lat'][a:a+500000],dtype=float).filled(np.nan)
                xs=np.ma.asarray(data['Lon'][a:a+500000],dtype=float).filled(np.nan)
                select=(ys>=lat.min()-.005)&(ys<=lat.max()+.005)&(xs>=lon.min()-.005)&(xs<=lon.max()+.005)
                if select.any():
                    depth=np.ma.asarray(data['MSL'][a:a+500000],dtype=float).filled(np.nan)[select]
                    valid=np.isfinite(depth)&(depth>0)&(depth<12000)
                    pieces.append(np.column_stack((xs[select][valid],ys[select][valid],-depth[valid])))
        if not pieces:return empty(lon),dict(record,status='no_coverage')
        points=np.concatenate(pieces)
        if len(points)<3 or len(points)>100000:raise ValueError('khoa_subset_limit')
        local=CRS.from_proj4(f'+proj=aeqd +lat_0={float(lat.mean())} +lon_0={float(lon.mean())} +datum=WGS84 +units=m')
        forward=Transformer.from_crs('EPSG:4326',local,always_xy=True)
        xy=np.column_stack(forward.transform(points[:,0],points[:,1]))
        queries=np.column_stack(forward.transform(lon.ravel(),lat.ravel()))
        result=LinearNDInterpolator(xy,points[:,2])(queries)
        distance,_=cKDTree(xy).query(queries)
        result[distance>225]=np.nan # Do not interpolate across unsurveyed holes or extrapolate coast.
        result=result.reshape(lon.shape)
        record.update(status='available' if np.isfinite(result).any() else 'no_coverage',
                      subset_points=len(points),support_limit_m=225,positive='depth down; converted to elevation up')
        return result,record
    except Exception as error:return empty(lon),dict(record,status='unavailable',reason=error_reason(error))


def emodnet(lon,lat):
    """Discover numeric MSL tiles through official WFS, not the LAT WCS layer."""
    values,record=local_export(SOURCE_IDS[5],lon,lat)
    if record['status']!='not_configured':return values,record
    record.update(native_spacing_m=115,native_spacing_arc_seconds=3.75,vertical_datum='MSL',
                  release='2024',attribution='EMODnet Bathymetry Consortium, EMODnet DTM 2024, DOI 10.12770/cf51df64-56f9-4a99-b1aa-36b8d7b743a1; modified by regional sampling.',tiles=[])
    # European / Caribbean DTM coverage. The coloured global base layer is GEBCO,
    # and must never be mistaken for a high-resolution global numeric dataset.
    europe=lon.min()<43 and lon.max()>-36 and lat.min()<90 and lat.max()>15
    caribbean=lon.min()<-60 and lon.max()>-70 and lat.min()<19 and lat.max()>11
    if not (europe or caribbean):return empty(lon),dict(record,status='outside_region')
    try:
        query={'service':'WFS','version':'2.0.0','request':'GetFeature','typeNames':'emodnet:download_tiles',
          'outputFormat':'application/json','count':100,
          'bbox':f'{float(lon.min())},{float(lat.min())},{float(lon.max())},{float(lat.max())},EPSG:4326'}
        url='https://ows.emodnet-bathymetry.eu/wfs?'+urllib.parse.urlencode(query)
        path,asset=cached_download(url,'emodnet-'+hashlib.sha256(url.encode()).hexdigest()[:24]+'.json',2*1024**2,30)
        record['assets'].append(asset);catalog=json.loads(path.read_text())
        tiles=[f['properties'] for f in catalog['features'] if f['properties'].get('dtm_release')=='2024'
               and f['properties'].get('product_format')=='ESRI ASCII Mean Sea Level']
        if len(tiles)>4:raise ValueError('emodnet_tile_limit')
        for tile in tiles:
            item={'tile':tile['dtm_tile']}
            try:
                source=tile['download_url']
                if not source.startswith('https://downloads.emodnet-bathymetry.eu/v12/') or not source.endswith('.msl.zip'):
                    raise ValueError('unexpected_emodnet_numeric_asset')
                archive,asset=cached_download(source,'emodnet-'+tile['dtm_tile']+'-2024-msl.zip',250*1024**2,100)
                record['assets'].append(asset)
                raster=archive.with_suffix('.asc')
                if not raster.exists():
                    with zipfile.ZipFile(archive) as z:
                        members=[i for i in z.infolist() if i.filename.lower().endswith(('.asc','.msl'))]
                        if len(members)!=1 or members[0].file_size>1200*1024**2:raise ValueError('emodnet_extraction_limit')
                        temp=raster.with_name(raster.name+'.'+uuid.uuid4().hex+'.part')
                        try:
                            with z.open(members[0]) as src,temp.open('wb') as out:shutil.copyfileobj(src,out,256*1024)
                            temp.replace(raster)
                        finally:temp.unlink(missing_ok=True)
                sampled=raster_samples(raster,lon,lat,assume_wgs84=True);valid=np.isfinite(sampled)&(sampled>=-12000)&(sampled<0)
                values[valid]=sampled[valid];item.update(status='available',valid_vertices=int(valid.sum()))
            except Exception as error:item.update(status='unavailable',reason=error_reason(error))
            record['tiles'].append(item)
        return values,dict(record,status='available' if np.isfinite(values).any() else 'no_coverage')
    except Exception as error:return empty(lon),dict(record,status='unavailable',reason=error_reason(error))


def resolve(heights,ids,water,lon,lat):
    """Cesium first; remaining valid sources by native spacing, per-vertex fallback."""
    candidates=[]
    for identifier,loader in [(3,lambda x,y:local_export(SOURCE_IDS[3],x,y)),(4,marine),
                               (5,emodnet),(6,khoa)]:
        values,record=loader(lon,lat);candidates.append((identifier,values,record))
    candidates.sort(key=lambda x:(0 if x[0]==3 else 1,x[2].get('native_spacing_m',float('inf'))))
    selected=np.zeros(heights.shape,dtype=bool);result=heights.copy();providers=ids.copy();records=[]
    for identifier,values,record in candidates:
        valid=np.isfinite(values)&(values<0)&(values>=-12000)&((water==1)|(water==-1))&~selected
        result[valid]=values[valid];providers[valid]=identifier;selected|=valid
        record['selected_vertices']=int(valid.sum());records.append(record)
    return result,providers,records
