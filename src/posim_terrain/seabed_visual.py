"""Synthetic metric sand/grid visual; never changes measured collision heights."""
from pathlib import Path
import json
import hashlib
import uuid
import xml.etree.ElementTree as ET
import numpy as np
from PIL import Image, ImageDraw

SHORELINE_MAX_HEIGHT_M = .5
SHORELINE_MIN_HEIGHT_M = -1.

def renderer_albedo(path):
    """Give distinct tile pixels distinct Ogre resource basenames.

    Ogre2 caches PBR images by basename, even with absolute paths in SDF.
    Keep the public land-satellite.png endpoint but use immutable content names
    for native rendering, including saved terrain made by older versions.
    """
    path=Path(path)
    data=path.read_bytes();sha256=hashlib.sha256(data).hexdigest()
    target=path.with_name('land-satellite-'+sha256+'.png')
    if not target.is_file() or target.read_bytes()!=data:
        temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.part')
        try:
            temporary.write_bytes(data);temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
    return target

def texture(path):
    size=512
    rng=np.random.default_rng(20261002)
    y,x=np.indices((size,size))
    grain=rng.normal(0,8,(size,size)) + 6*np.sin(x*.17+2*np.sin(y*.027))
    pixels=np.clip(np.array([164,145,104])[None,None,:]+grain[...,None],0,255).astype(np.uint8)
    image=Image.fromarray(pixels)
    draw=ImageDraw.Draw(image)
    for p in range(0,size,128):
        draw.line((p,0,p,size),fill=(18,18,18),width=4)
        draw.line((0,p,size,p),fill=(18,18,18),width=4)
    image.save(path)

def shoreline_pixels(pixels,elevation):
    """Blend terrain in a narrow height band around mean sea level.

    A face-wide depth classification paints a broad underwater band through
    the translucent waves. Sample height per texel instead, and ease both ends
    of the height band so there is no new material boundary.
    """
    h=np.asarray(elevation)
    def smooth(value):
        value=np.clip(value,0,1)
        return value*value*(3-2*value)
    weight=.85*smooth((h-SHORELINE_MIN_HEIGHT_M)/(-.12-SHORELINE_MIN_HEIGHT_M)) \
        *(1-smooth((h-.12)/(SHORELINE_MAX_HEIGHT_M-.12)))
    mask=weight>0
    result=np.asarray(pixels).copy()
    if not mask.any():return result
    values=np.asarray(pixels).astype(np.float32)
    light=np.clip((values[...,0]*.2126+values[...,1]*.7152+values[...,2]*.0722)/105,.5,1.5)
    grey=light[...,None]*np.array([75.,90.,102.])
    blended=values*(1-weight[...,None])+grey*weight[...,None]
    result[mask]=np.clip(np.rint(blended[mask]),0,255).astype(np.uint8)
    return result

def shoreline_albedo(out,xs,ys,heights,land_texture):
    """Bake a visual-only, georeferenced mask without editing source imagery."""
    from .coastal_detail import surface_height
    width,height=xs[-1]-xs[0],ys[-1]-ys[0]
    if land_texture:
        source=out/land_texture['file']
        if np.max(heights)<=SHORELINE_MIN_HEIGHT_M or np.min(heights)>=SHORELINE_MAX_HEIGHT_M:return renderer_albedo(source)
        with Image.open(source) as image:
            image=image.convert('RGB')
            # Mask detail is independent of satellite survey resolution.
            nx=max(512,image.width);ny=max(512,image.height)
            base=np.asarray(image.resize((nx,ny),Image.Resampling.BILINEAR))
    else:
        source=out/'seabed-grid.png';nx=ny=1024
    xx,yy=np.meshgrid(xs[0]+(np.arange(nx)+.5)*width/nx,
                     ys[-1]-(np.arange(ny)+.5)*height/ny)
    if not land_texture:
        with Image.open(source) as image: sand=np.asarray(image.convert('RGB'))
        # Match the existing absolute-metre, 4 m repeating seabed UVs.
        u=np.floor(np.mod(xx/4,1)*sand.shape[1]).astype(int)
        v=np.floor(np.mod(-yy/4,1)*sand.shape[0]).astype(int)
        base=sand[v,u]
    graded=shoreline_pixels(base,surface_height(xs,ys,heights,xx,yy))
    if land_texture and np.array_equal(graded,base):return renderer_albedo(source)
    target=out/('land-shoreline.png' if land_texture else 'seabed-shoreline.png')
    temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.part')
    try:
        Image.fromarray(graded).save(temporary,format='PNG');temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
    return renderer_albedo(target)

def decorate_link(link,out,xs,ys,heights,*,normals=None,land_texture=None,prefix='terrain-visual'):
    """Split only visual faces; their union is exactly the collision mesh."""
    from .terrain import write_mesh
    out=Path(out)
    texture(out/'seabed-grid.png')
    for visual in list(link.findall('visual')):link.remove(visual)
    h=np.asarray(heights)
    a,b,c,d=h[:-1,:-1],h[:-1,1:],h[1:,:-1],h[1:,1:]
    maximum=np.stack((np.maximum.reduce([a,b,d]),np.maximum.reduce([a,d,c])),axis=-1).ravel()
    minimum=np.stack((np.minimum.reduce([a,b,d]),np.minimum.reduce([a,d,c])),axis=-1).ravel()
    shallow=(maximum>SHORELINE_MIN_HEIGHT_M)&(minimum<SHORELINE_MAX_HEIGHT_M)
    if land_texture:
        land=maximum>=0
        shallow_seabed=shallow&~land
        albedo=shoreline_albedo(out,xs,ys,h,land_texture) if land.any() else out/land_texture['file']
        parts=[('seabed',~(land|shallow_seabed),4,(0,0),'seabed-grid.png'),
               ('land',land,(xs[-1]-xs[0],ys[-1]-ys[0]),(-xs[0],-ys[0]),albedo)]
        if shallow_seabed.any():
            parts.append(('shallow',shallow_seabed,(xs[-1]-xs[0],ys[-1]-ys[0]),(-xs[0],-ys[0]),
                          shoreline_albedo(out,xs,ys,h,None)))
    else:
        parts=[('seabed',~shallow,4,(0,0),'seabed-grid.png')]
        if shallow.any():
            parts.append(('shallow',shallow,(xs[-1]-xs[0],ys[-1]-ys[0]),(-xs[0],-ys[0]),
                          shoreline_albedo(out,xs,ys,h,None)))
    for name,mask,scale,offset,image in parts:
        if mask is not None and not mask.any():continue
        path=out/(prefix+('' if name=='seabed' else '-'+name)+'.dae')
        write_mesh(path,xs,ys,heights,normals,uv_scale=scale,uv_offset=offset,face_mask=mask)
        visual=ET.SubElement(link,'visual',name=name)
        ET.SubElement(ET.SubElement(ET.SubElement(visual,'geometry'),'mesh'),'uri').text=str(path.resolve())
        mat=ET.SubElement(visual,'material')
        for tag,text in [('ambient','0.6 0.6 0.6 1'),('diffuse','1 1 1 1'),('specular','0.03 0.03 0.03 1')]:ET.SubElement(mat,tag).text=text
        metal=ET.SubElement(ET.SubElement(mat,'pbr'),'metal')
        albedo=out/image
        ET.SubElement(metal,'albedo_map').text=str(albedo.resolve())
        ET.SubElement(metal,'roughness').text='1'
        ET.SubElement(metal,'metalness').text='0'

def decorate(tree,out,xs,ys,heights,*,land_texture=None):
    link=tree.getroot().find("world/model[@name='gebco_terrain']/link")
    if link is None:raise ValueError('terrain_visual_missing')
    decorate_link(link,out,xs,ys,heights,land_texture=land_texture)

def prepare(world):
    """Upgrade old saved regions through a parent-owned runtime visual copy."""
    world=Path(world)
    tree=ET.parse(world)
    manifest=json.loads((world.parent/'manifest.json').read_text())
    land_texture=manifest.get('visual_aid',{}).get('land_texture')
    if land_texture:
        from .coastal_detail import digest
        path=world.parent/land_texture['file']
        if not path.is_file() or digest(path)!=land_texture['sha256']:land_texture=None
    with np.load(world.parent/'projected.npz') as grid:
        # Old saved jobs may predate the warmed satellite cache. Refresh only
        # their visual assets at launch; never alter resident collision heights.
        from .coastal_detail import VISUAL_REVISION
        if (not land_texture or land_texture.get('display_revision')!=VISUAL_REVISION) and manifest.get('source_policy')=='auto':
            from .coastal_detail import paint,atomic
            refreshed=paint(world.parent,manifest['spec'],grid['xs'],grid['ys'],grid['elevation'])
            if refreshed:
                land_texture=refreshed
                manifest.setdefault('visual_aid',{})['land_texture']=land_texture
                manifest['visual_aid']['collision_changed']=False
                if 'Sentinel-2 RGB' not in manifest['source']:
                    manifest['source']+=' + Sentinel-2 RGB'
                atomic(world.parent/'manifest.json',manifest)
                terrain_path=world.parent/'terrain.json'
                if terrain_path.is_file():
                    body=json.loads(terrain_path.read_text());body['land_texture']=land_texture
                    atomic(terrain_path,body)
        decorate(tree,world.parent,grid['xs'],grid['ys'],grid['elevation'],land_texture=land_texture)
    target=world.with_name('world-visual.sdf')
    ET.indent(tree);tree.write(target,encoding='utf-8',xml_declaration=True)
    return target
