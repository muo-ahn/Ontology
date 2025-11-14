# Canonical Label & Location Map

| Canonical Label | Aliases |
| --------------- | ------- |
| Mass | lesion / mass lesion / 덩어리 |
| Nodule | nodule / small mass / 결절 |
| Opacity | opacity / infiltrate / 음영 |
| Hypodensity | low attenuation area / reduced density / 저음영 |
| Subarachnoid Hemorrhage | SAH / Subarachnoid bleeding / 수막하출혈 |

| Canonical Location | Aliases |
| ------------------ | ------- |
| Right lobe of the liver | right hepatic lobe / rhl / right lobe liver |
| Left parietal lobe | left parietal region / 좌측두정엽 |
| Right middle lobe | rml / right middle lung lobe |
| Lung | pulmonary / lungs |

이 표는 `services/ontology_map.py` 의 `LABEL_CANONICALS`, `LOCATION_CANONICALS`에서 자동 생성된 값과 동일하게 유지되어야 합니다. 새로운 라벨/위치를 추가할 경우 코드와 본 문서를 함께 갱신하고, `scripts/check_label_drift.py`를 실행하여 seed 데이터가 최신 canonical 정의를 따르는지 확인하세요.
