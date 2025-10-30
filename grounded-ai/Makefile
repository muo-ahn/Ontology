# ==========================================
# 🧠 Ontology × vLM × LLM Prototype
# Makefile (Local / GPU / Metal 공용)
# ==========================================

PROJECT_NAME = grounded-ai
OLLAMA_HOST = http://localhost:11434
NEO4J_URI = bolt://neo4j:7687
NEO4J_USER = neo4j
NEO4J_PASS = test1234
QDRANT_URL = http://qdrant:6333
NEO4J_SVC ?= neo4j 
IMPORT_DIR ?= /var/lib/neo4j/import
HOST_DATA    ?= ./data/medical_dummy
CYpher_FILE  ?= ./scripts/cyphers/seed.cypher
CYpher_FILE_load_all  ?= ./scripts/cyphers/load_all.cypher
CYpher_FILE_load_a  ?= ./scripts/cyphers/seed_dummy_A.cypher
CYpher_FILE_load_b  ?= ./scripts/cyphers/seed_dummy_B.cypher
CYpher_FILE_load_c  ?= ./scripts/cyphers/seed_dummy_C.cypher
CYpher_MIGRATE_IMAGE ?= ./cypher/migrate_image_id.cypher

# ------------------------------------------
# 기본 명령
# ------------------------------------------
.PHONY: up down rebuild logs seed pull models clean db-cp db-load migrate-image-id migrate-constraints verify-pipeline-debug

## Image.id -> image_id property migration
migrate-image-id:
	@echo "🔄 Migrating Image.id to Image.image_id..."
	@[ -f $(CYpher_MIGRATE_IMAGE) ] || { echo "❌ $(CYpher_MIGRATE_IMAGE) not found."; exit 1; }
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	docker cp $(CYpher_MIGRATE_IMAGE) $$cid:/tmp/migrate_image_id.cypher; \
	docker exec -t $$cid cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS) -f /tmp/migrate_image_id.cypher; \
	docker exec -t $$cid rm -f /tmp/migrate_image_id.cypher;

.PHONY: migrate-constraints
migrate-constraints:
	cypher-shell -u $${NEO4J_USER} -p $${NEO4J_PASS} -f cypher/migrate_constraints.cypher

## 컨테이너 기동 (FastAPI, Neo4j, Qdrant, UI)
up:
	@echo "🚀 Starting $(PROJECT_NAME) stack..."
	OLLAMA_HOST=$(OLLAMA_HOST) docker compose up -d --build

## 컨테이너 종료
down:
	@echo "🧹 Stopping containers..."
	docker compose down -v

## 전체 재빌드
rebuild: down up

## 실시간 로그 보기 (API만)
logs:
	docker compose logs -f api

## 시드 데이터 주입 (Neo4j)
seed:
	@echo "🌱 Seeding Neo4j with sample Ontology..."
	@[ -f $(CYpher_FILE) ] || { echo "❌ $(CYpher_FILE) not found. Create scripts/seed.cypher first."; exit 1; }
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	docker cp $(CYpher_FILE) $$cid:/tmp/seed.cypher; \
	docker exec -t $$cid cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS) -f /tmp/seed.cypher; \
	docker exec -t $$cid rm -f /tmp/seed.cypher; \

## 불러오기 (Ollama 모델)
pull:
	@echo "⬇️ Pulling base models from Ollama..."
# 	ollama pull qwen2:7b-instruct-q4_K_M
	ollama pull qwen2.5:7b-instruct-q4_K_M
	ollama pull qwen2.5vl:latest
	ollama pull minicpm-v:latest

## 로컬 모델 폴더 준비
models:
	mkdir -p ./models ./data ./ui ./api

## 정리
clean:
	@echo "🧽 Cleaning build artifacts..."
	docker system prune -f
	rm -rf ./__pycache__ */__pycache__ .pytest_cache || true

## /pipeline/analyze 디버그 필드 검증 (cURL + jq)
verify-pipeline-debug:
	@./scripts/verify_pipeline_debug.sh

# ------------------------------------------
# 유틸리티
# ------------------------------------------

## FastAPI API 포트 확인
status:
	@echo "🌍 API endpoint: http://localhost:8000"
	@echo "📊 Streamlit UI: http://localhost:8501"
	@echo "🧩 Neo4j Browser: http://localhost:7474"
	@echo "💾 Qdrant Dashboard: http://localhost:6333/dashboard"

## Neo4j 콘솔 접속
neo4j:
	docker exec -it $$(docker ps -qf "name=neo4j") cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS)

## GPU 모드 실행 (4070 등)
gpu:
	@echo "⚡ Starting with GPU profile..."
	OLLAMA_HOST=$(OLLAMA_HOST) docker compose --profile gpu up -d --build

## CSV -> 컨테이너 import/ 폴더로 복사 (docker cp + container id)
db-cp:
	@echo "📦 Copy CSVs into Neo4j container import dir..."
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	for f in patients.csv encounters.csv observations.csv diagnoses.csv medications.csv imaging.csv ai_inference.csv; do \
		echo "  - $$f"; \
		docker cp $(HOST_DATA)/$$f $$cid:$(IMPORT_DIR)/; \
	done; \
	echo "✅ Copied all CSVs."

## Cypher 스크립트 실행(파일 기반; docker cp + exec)
db-load:
	@echo "🧠 Running Cypher LOAD CSV from $(CYpher_FILE_load_all)..."
	@[ -f $(CYpher_FILE_load_all) ] || { echo "❌ $(CYpher_FILE_load_all) not found. Create scripts/load_all.cypher first."; exit 1; }
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	docker cp $(CYpher_FILE_load_all) $$cid:/tmp/load_all.cypher; \
	docker exec -t $$cid cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS) -f /tmp/load_all.cypher; \
	docker exec -t $$cid rm -f /tmp/load_all.cypher; \
	echo "✅ Neo4j LOAD CSV done."

## Cypher 스크립트 실행(파일 기반; A 세트)
db-load-a:
	@echo "🧠 Running Cypher LOAD CSV for Dummy Set A from $(CYpher_FILE_load_a)..."
	@[ -f $(CYpher_FILE_load_a) ] || { echo "❌ $(CYpher_FILE_load_a) not found. Create scripts/seed_dummy_A.cypher first."; exit 1; }
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	docker cp $(CYpher_FILE_load_a) $$cid:/tmp/seed_dummy_A.cypher; \
	docker exec -t $$cid cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS) -f /tmp/seed_dummy_A.cypher; \
	docker exec -t $$cid rm -f /tmp/seed_dummy_A.cypher; \
	echo "✅ Neo4j LOAD CSV for Dummy Set A done."

## Cypher 스크립트 실행(파일 기반; B 세트)
db-load-b:
	@echo "🧠 Running Cypher LOAD CSV for Dummy Set B from $(CYpher_FILE_load_b)..."
	@[ -f $(CYpher_FILE_load_b) ] || { echo "❌ $(CYpher_FILE_load_b) not found. Create scripts/seed_dummy_B.cypher first."; exit 1; }
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	docker cp $(CYpher_FILE_load_b) $$cid:/tmp/seed_dummy_B.cypher; \
	docker exec -t $$cid cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS) -f /tmp/seed_dummy_B.cypher; \
	docker exec -t $$cid rm -f /tmp/seed_dummy_B.cypher; \
	echo "✅ Neo4j LOAD CSV for Dummy Set B done."

## Cypher 스크립트 실행(파일 기반; C 세트)
db-load-c:
	@echo "🧠 Running Cypher LOAD CSV for Dummy Set C from $(CYpher_FILE_load_c)..."
	@[ -f $(CYpher_FILE_load_c) ] || { echo "❌ $(CYpher_FILE_load_c) not found. Create scripts/seed_dummy_C.cypher first."; exit 1; }
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	docker cp $(CYpher_FILE_load_c) $$cid:/tmp/seed_dummy_C.cypher; \
	docker exec -t $$cid cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS) -f /tmp/seed_dummy_C.cypher; \
	docker exec -t $$cid rm -f /tmp/seed_dummy_C.cypher; \
	echo "✅ Neo4j LOAD CSV for Dummy Set C done."

# ------------------------------------------
db-delete:
	@echo "🗑️ Deleting all nodes and relationships in Neo4j..."
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	docker exec -t $$cid cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS) "MATCH (n) DETACH DELETE n;"; \
	echo "✅ Deleted all nodes and relationships."