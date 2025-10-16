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
CYpher_FILE  ?= ./scripts/load_all.cypher

# ------------------------------------------
# 기본 명령
# ------------------------------------------
.PHONY: up down rebuild logs seed pull models clean db-cp db-load

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
	cypher-shell -a $(NEO4J_URI) -u $(NEO4J_USER) -p $(NEO4J_PASS) -f /data/seed.cypher || true

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
	@echo "🧠 Running Cypher LOAD CSV from $(CYpher_FILE)..."
	@[ -f $(CYpher_FILE) ] || { echo "❌ $(CYpher_FILE) not found. Create scripts/load_all.cypher first."; exit 1; }
	@cid=$$(docker compose ps -q $(NEO4J_SVC)); \
	if [ -z "$$cid" ]; then echo "❌ Neo4j container not running. Run 'make up' first."; exit 1; fi; \
	docker cp $(CYpher_FILE) $$cid:/tmp/load_all.cypher; \
	docker exec -t $$cid cypher-shell -u $(NEO4J_USER) -p $(NEO4J_PASS) -f /tmp/load_all.cypher; \
	docker exec -t $$cid rm -f /tmp/load_all.cypher; \
	echo "✅ Neo4j LOAD CSV done."
