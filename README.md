# POSIM-Terrain

[한국어](README.ko.md)

Generate land and ocean terrain from latitude and longitude. POSIM-Terrain exports one numeric field for meshes, collision checks and sensor geometry. WWW-POSIM uses this library for its geographic worlds.

## Install

Python 3.10 or later is required.

```sh
git clone https://github.com/IOES-Lab/POSIM-Terrain.git
cd POSIM-Terrain
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

On Windows, activate `.venv\Scripts\activate` instead.

## Generate a region

```sh
python -m posim_terrain --spec examples/busan.json --output out/busan
```

This exports a terrain mesh and SDF using numeric sources. To include the installed POSIM ocean world and run Gazebo's SDF check, add `--engine-world` in a configured POSIM/ROS environment.

For a satellite-textured export, prepare the Sentinel-2 cache first, then generate the region:

```sh
python -m posim_terrain.coastal_detail --spec examples/busan.json
python -m posim_terrain --spec examples/busan.json --output out/busan
```

The cache command downloads clear Sentinel-2 RGB imagery and OSM coastline cells for the region. `manifest.json` records the texture status. Texture preparation preserves the numeric elevation field.
Source identity includes the coastal data for the requested region. Refreshing another region preserves that identity.

```python
from posim_terrain.terrain import build

build({"latitude": 35.09, "longitude": 129.055,
       "width_m": 20000, "height_m": 20000, "samples": 257,
       "source_policy": "auto"}, "out/busan",
      engine_world=False, validate_sdf=False)
```

- Latitude: −75° to +75°
- Region width and height: 1–20 km
- Samples per axis: 33, 65, 129 or 257
- Local axes: x east, y north, z up; metres
- Sea level: 0 m
- Data cache: `~/.cache/posim-terrain`; set `POSIM_TERRAIN_DATA_DIR` to select another directory

`auto` combines available bathymetry with Copernicus GLO-30 land. `gebco` uses only GEBCO. Provider URLs, hashes, coverage, quality and vertical datum assumptions are recorded. Missing numeric coverage fails generation.

## Output and structure

| Path | Contents |
|---|---|
| `src/posim_terrain/terrain.py` | Geographic projection, numeric sampling and export |
| `terrain_sources.py`, `bathymetry.py` | Source selection, bounded downloads and coverage |
| `coastal_detail.py`, `seabed_visual.py` | Optional OSM/Sentinel refinement and visual textures |
| `floating_origin.py` | Rigid geographic frames |
| `canal_override.py`, `data/` | Bounded Suez simulation geometry |
| `examples/` | New York, Busan and Tokyo specifications and terrain bundles |
| `tests/` | Source, shoreline, date-line and frame checks |

Each generated directory contains `projected.npz`, `terrain.json`, `terrain.dae`, `world.sdf`, `manifest.json` and `source-providers.json`. Mesh interpolation does not improve source measurement accuracy. Copernicus land uses an explicit EGM2008-to-MSL approximation. Suez water uses a labelled 10 m simulation depth assumption. These artifacts support simulation; they are not certified navigation charts.

See [city terrain examples](examples/README.md) and [source notices](NOTICE.md).

## Tests

```sh
python -m unittest discover -s tests
```

Code: [Apache-2.0](LICENSE). Datasets retain their own terms and attribution.
