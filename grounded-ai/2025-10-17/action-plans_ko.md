# Vision→LLM→Ontology 개편 액션 플랜

## 1단계 – 그래프 업서트 및 API 개선
- `/vision/inference` 요청에 `modality`, `patient_id`, `encounter_id` 등 메타데이터를 수용하고 ID가 없으면 결정적으로 생성.
- Neo4j 쓰기를 `MERGE` 기반 아이덴포턴시 패턴으로 리팩터링해 `Image`와 관련 노드/관계를 동일 트랜잭션에서 작성.
- SHA256 키를 활용한 `Idempotency` 노드를 추가해 중복 처리를 방지.
- 요청/워커 타임아웃을 조정하고 구조화 로그를 확충해 파라미터·생성 ID를 기록.

## 2단계 – 이벤트 기반 파이프라인
- Docker Compose에 Redis Streams를 추가하고 `image.received`, `vision.captions`, `nlp.interpretation`, `graph.upserted` 토픽 및 데드레터 큐 구성.
- 동기식 엔드포인트를 다음과 같이 분리:
  1. **Ingress 핸들러**: 파일 저장 → 아이덴포턴시 키 계산 → `image.received` 발행 → 202 + 토큰 반환.
  2. **백그라운드 워커**: Redis를 구독해 VLM, LLM, 그래프 업서트를 순차 처리.
- Redis 기반 상태 업데이트를 스트리밍하는 SSE 엔드포인트(`/events/{key}`) 구현.
- Streamlit UI에서 업로드, SSE 구독, 단계별 진행 상황 표시 지원.

## 3단계 – 영속성 및 검색 개선
- 파이프라인 처리 시 Qdrant 임베딩을 자동 생성해 `Image`, `AIInference`에 벡터 ID 저장.
- 환자/내원 정보가 지연 도착할 경우 “link-intent” 이벤트를 발행해 중복 없이 관계 연결.
- LLM 후처리로 그래프 확장 아이디어(질환 추가, 검사 추천 등)를 제안.

## 4단계 – 안정성, 테스트, 문서화
- 재시도/백오프 로직을 도입하고 실패 이벤트는 데드레터 스트림으로 전달.
- 단계별 지연, 성공/실패 카운트, 재시도 횟수 등 메트릭을 수집해 로그/대시보드로 노출.
- 워커 로직, Neo4j 업서트, 아이덴포턴시 동작에 대한 단위·통합 테스트 작성.
- README에 브로커/워커/SSE 설정을 문서화하고 `make workers`, `make replay` 등 개발 편의 타깃을 제공.
