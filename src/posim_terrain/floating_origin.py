"""Rigid floating origins with bounded projection error and a periodic sea.

Only the private voyage uses this frame metadata. Translation is an exact
whole number of terrain cells AND 256 m wave periods, so existing collision
meshes, their canonical sample halos and the FFT wave phase remain intact.
"""
import math
import numpy as np
from pyproj import Geod

GEO=Geod(ellps='WGS84')

class FramedTransform:
    def __init__(self,base,frame,inverse):
        self.base,self.inverse=base,inverse
        self.matrix=np.asarray(frame['matrix'],dtype=float)
        self.offset=np.asarray(frame['offset'],dtype=float)
        if self.matrix.shape!=(2,2) or self.offset.shape!=(2,) or not np.isfinite(self.matrix).all() or not np.isfinite(self.offset).all():raise ValueError('invalid_native_frame')
        if not np.allclose(self.matrix.T@self.matrix,np.eye(2),atol=1e-8):raise ValueError('native_frame_must_be_rigid')
    def transform(self,x,y):
        x,y=np.broadcast_arrays(x,y)
        if self.inverse:
            a=self.matrix[0,0]*x+self.matrix[0,1]*y+self.offset[0]
            b=self.matrix[1,0]*x+self.matrix[1,1]*y+self.offset[1]
            return self.base.transform(a,b)
        a,b=self.base.transform(x,y);a=np.asarray(a)-self.offset[0];b=np.asarray(b)-self.offset[1]
        return self.matrix[0,0]*a+self.matrix[1,0]*b,self.matrix[0,1]*a+self.matrix[1,1]*b

def translation(position,width,height):
    # Current 1 km cells use a 32 km common period. Smaller power-of-two
    # cells can re-anchor more often without changing the water phase.
    sizes=[int(round(width)),int(round(height)),256]
    if any(abs(size-actual)>1e-6 for size,actual in zip(sizes,[width,height,256])):raise ValueError('nonintegral_origin_cell_size')
    quantum=math.lcm(*sizes);step=quantum*max(1,round(8192/quantum))
    return [math.copysign(step,p) if abs(p)>step/2+10 else 0. for p in position]

def prepare_frame(spec,position,shift,points,maximum_error_m=.25):
    from .terrain import projection
    _,_,old=projection(spec)
    longitude,latitude=old.transform(*shift)
    new=dict(spec,longitude=float(longitude),latitude=float(latitude))
    new.pop('_native_frame',None)
    _,forward,_=projection(new)
    position=np.asarray(position,dtype=float);shift=np.asarray(shift,dtype=float)
    centre=np.asarray(forward.transform(*old.transform(*position)),dtype=float)
    jacobian=np.column_stack([np.asarray(forward.transform(*old.transform(*(position+axis))))-centre for axis in ([1.,0.],[0.,1.])])
    u,_,v=np.linalg.svd(jacobian);matrix=u@v
    offset=centre-matrix@(position-shift)
    new['_native_frame']=dict(matrix=matrix.tolist(),offset=offset.tolist())
    _,_,inverse=projection(new)
    points=np.asarray(points,dtype=float)
    a,b=old.transform(points[:,0],points[:,1]);c,d=inverse.transform(points[:,0]-shift[0],points[:,1]-shift[1])
    errors=np.asarray(GEO.inv(a,b,c,d)[2]);error=float(errors.max(initial=0))
    if error>maximum_error_m:raise ValueError('origin_projection_error_exceeds_budget_'+str(error))
    return new,error
