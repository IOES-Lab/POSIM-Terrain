# POSIM-Terrain

[English](README.md)

위도·경도로 육지와 해저 지형을 생성합니다. 메시·충돌 검사·센서 형상에 같은 수치 지형을 사용합니다. WWW-POSIM의 지역 월드는 이 라이브러리로 지형을 만듭니다.

## 설치

Python 3.10 이상이 필요합니다.

```sh
git clone https://github.com/IOES-Lab/POSIM-Terrain.git
cd POSIM-Terrain
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Windows에서는 `.venv\Scripts\activate`로 가상환경을 활성화합니다.

## 지역 생성

```sh
python -m posim_terrain --spec examples/busan.json --output out/busan
```

수치 자료로 지형 메시와 SDF를 내보냅니다. 설치된 POSIM의 해양 월드를 포함하고 Gazebo로 SDF를 검사하려면 POSIM·ROS 환경에서 `--engine-world`를 추가합니다.

위성 텍스처를 포함하려면 Sentinel-2 캐시를 준비한 뒤 지형을 생성합니다.

```sh
python -m posim_terrain.coastal_detail --spec examples/busan.json
python -m posim_terrain --spec examples/busan.json --output out/busan
```

캐시 준비 명령은 해당 지역의 구름이 적은 Sentinel-2 RGB 영상과 OSM 해안선 자료를 받습니다. `manifest.json`에 텍스처 상태를 기록합니다. 텍스처 준비 중에도 수치 고도는 유지합니다.
자료 식별값에는 생성 지역의 연안 자료를 포함합니다. 다른 지역의 자료를 갱신해도 이 값은 유지합니다.

```python
from posim_terrain.terrain import build

build({"latitude": 35.09, "longitude": 129.055,
       "width_m": 20000, "height_m": 20000, "samples": 257,
       "source_policy": "auto"}, "out/busan",
      engine_world=False, validate_sdf=False)
```

- 위도: −75°~+75°
- 지역 너비·높이: 1~20 km
- 축별 표본 수: 33, 65, 129, 257
- 지역 좌표: x 동쪽, y 북쪽, z 위쪽, 단위 m
- 해수면: 0 m
- 자료 캐시: `~/.cache/posim-terrain`; `POSIM_TERRAIN_DATA_DIR`로 다른 폴더 지정

`auto`는 이용 가능한 수심 자료와 Copernicus GLO-30 육지 자료를 조합합니다. `gebco`는 GEBCO만 사용합니다. 출처 URL·해시·범위·품질·수직 기준 가정을 기록합니다. 수치 자료가 비어 있는 곳은 생성 실패로 처리합니다.

## 출력과 구조

| 경로 | 내용 |
|---|---|
| `src/posim_terrain/terrain.py` | 좌표 투영·수치 표본 추출·내보내기 |
| `terrain_sources.py`, `bathymetry.py` | 자료 선택·다운로드 한도·범위 확인 |
| `coastal_detail.py`, `seabed_visual.py` | OSM·Sentinel 선택 보정과 시각 텍스처 |
| `floating_origin.py` | 지리 좌표계의 강체 변환 |
| `canal_override.py`, `data/` | 범위가 정해진 수에즈 시뮬레이션 형상 |
| `examples/` | 뉴욕·부산·도쿄 설정과 지형 묶음 |
| `tests/` | 출처·해안·날짜 변경선·좌표계 검사 |

생성 폴더에는 `projected.npz`, `terrain.json`, `terrain.dae`, `world.sdf`, `manifest.json`, `source-providers.json`이 있습니다. 메시 보간은 원자료의 측정 정확도를 높이지 않습니다. Copernicus 육지는 EGM2008 고도를 평균해수면으로 근사합니다. 수에즈 수역에는 표시된 10 m 시뮬레이션 수심 가정을 사용합니다. 지형은 시뮬레이션용이며 공인 항해 해도가 아닙니다.

[도시 지형 예제](examples/README.ko.md)와 [출처 고지](NOTICE.md)를 확인하세요.

## 시험

```sh
python -m unittest discover -s tests
```

코드: [Apache-2.0](LICENSE). 데이터에는 각 자료의 이용 조건과 출처 표시가 적용됩니다.

## 해저면 표현

해저면은 [Rob Tuytel의 Aerial Beach 01](https://polyhaven.com/a/aerial_beach_01)에서 제공한 2K 컬러·OpenGL 노멀·거칠기 맵을 사용합니다. 라이선스는 [CC0](https://polyhaven.com/license)입니다. 질감은 30 m마다 반복되며 격자선이 포함되어 있지 않습니다. 이미지 URL과 SHA-256 해시는 `src/posim_terrain/assets/seabed/manifest.json`에 기록되어 있습니다. 실행 중 질감 사용에는 네트워크 연결이 필요하지 않습니다. 이미지는 일반적인 모래를 표현하며 실제 지형 형상은 수치 수심 자료로 정합니다.
