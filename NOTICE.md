# Source and data notices

Python source: Copyright IOES-Lab. Licensed under Apache-2.0.

The bundled Suez geometry derives from OpenStreetMap contributors and is subject to the Open Database License (ODbL). Its metadata retains source URLs and attribution. Water depth is an explicit 10 m simulation assumption; bank heights are modelled.

Generated terrain retains per-provider URLs, checksums, attribution and datum assumptions in `manifest.json` and `source-providers.json`.

- GEBCO: GEBCO Bathymetric Compilation Group 2026, GEBCO_2026 Grid; DOI `10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa`.
- Copernicus DEM GLO-30: © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA. Regional sampling modifies the source.
- OpenStreetMap coastlines: © OpenStreetMap contributors, ODbL.
- Sentinel-2 imagery: modified Copernicus Sentinel data.
- Optional regional bathymetry and permitted numeric exports retain their own terms.

The code license does not replace dataset terms. See [GEBCO terms](https://www.gebco.net/data-products/gridded-bathymetry-data), [Copernicus DEM collection](https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model), and [OpenStreetMap copyright](https://www.openstreetmap.org/copyright).

Packaged seabed maps: [Aerial Beach 01 by Rob Tuytel, Poly Haven](https://polyhaven.com/a/aerial_beach_01), [CC0-1.0](https://polyhaven.com/license). The original 2K diffuse, OpenGL normal and roughness maps are included with source URLs and checksums in `src/posim_terrain/assets/seabed/manifest.json`.
