# 도시 지형 예제

[English](README.md)

각 묶음은 20 × 20 km 지역의 육지와 해저를 포함합니다. 격자는 257 × 257 꼭짓점이며 간격은 78.125 m입니다. 지리 좌표는 WGS84, 수치 지형은 로컬 x 동쪽·y 북쪽·z 위쪽을 m 단위로 사용합니다.

![Sentinel-2 텍스처를 적용한 Gazebo 3D 월드](cities-3d.png)

| 도시 | 중심 위도 / 경도 | 지형 묶음 |
|---|---|---|
| 뉴욕 | 40.65° / −74.035° | [new-york.zip](terrains/new-york.zip) |
| 부산 | 35.09° / 129.055° | [busan.zip](terrains/busan.zip) |
| 도쿄 | 35.60° / 139.83° | [tokyo.zip](terrains/tokyo.zip) |

압축을 풀면 `manifest.json`으로 출처를 확인하고 `projected.npz`를 읽거나 상대경로 `world.sdf`와 메시를 사용할 수 있습니다. `world.sdf`에는 위성 텍스처를 적용한 지형이 있습니다. `preview.sdf`에는 물리·렌더링·카메라 두 대와 높이 0 m의 평평한 수면이 있습니다. 항해에는 POSIM 월드의 차량·파도 설정을 연결합니다.

```sh
python -m posim_terrain.coastal_detail --spec examples/new-york.json
python -m posim_terrain --spec examples/new-york.json --output out/new-york
python -m posim_terrain.coastal_detail --spec examples/busan.json
python -m posim_terrain --spec examples/busan.json --output out/busan
python -m posim_terrain.coastal_detail --spec examples/tokyo.json
python -m posim_terrain --spec examples/tokyo.json --output out/tokyo
```

자료는 GEBCO 2026·Copernicus GLO-30·Copernicus Marine SDB와 이용 가능한 KHOA BADA2024입니다. 기록에 자료별 범위·해시·수직 기준 가정을 남깁니다. 격자 간격은 측정 정확도를 높이지 않습니다. [출처 고지](../NOTICE.md)를 확인하세요.

[체크섬](terrains/SHA256SUMS)과 [수치 목록](terrains/catalog.json)을 함께 제공합니다.

## 3D 월드 캡처

Gazebo Sim 10 / Ogre2 Metal 카메라 프레임입니다. 고도 배율은 1:1입니다. 각 지형 기록에 위성 장면 ID·촬영일·자료 URL·체크섬을 남깁니다.

| 도시 | 월드 화면 | 항만 화면 |
|---|---|---|
| 뉴욕 | [원본 해상도](renders/new-york-world.png) | [원본 해상도](renders/new-york-harbour.png) |
| 부산 | [원본 해상도](renders/busan-world.png) | [원본 해상도](renders/busan-harbour.png) |
| 도쿄 | [원본 해상도](renders/tokyo-world.png) | [원본 해상도](renders/tokyo-harbour.png) |

[캡처 기록](renders/capture-report.json) · [수치 고도 지도](cities.png)
