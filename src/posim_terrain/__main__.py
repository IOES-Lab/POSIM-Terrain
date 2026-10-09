"""Export a geographic terrain region."""
import argparse, json
from pathlib import Path
from .terrain import build

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--engine-world',action='store_true',help='Use the installed POSIM ocean world and validate with Gazebo')
    args=parser.parse_args()
    result=build(json.loads(args.spec.read_text()),args.output,engine_world=args.engine_world,validate_sdf=args.engine_world)
    print(json.dumps({k:result[k] for k in ('source','minimum_m','maximum_m','land_vertices','ocean_vertices','sdf_validation')},indent=2))

if __name__=='__main__':main()
