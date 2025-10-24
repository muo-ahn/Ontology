1) 캡션
curl -X POST http://localhost:8000/vision/caption \
  -H "Content-Type: application/json" \
  -d '{"file_path":"data/medical_dummy/images/img_001.png"}'

2) 업서트
curl -X POST http://localhost:8000/graph/upsert \
  -H "Content-Type: application/json" \
  -d '{...Vision 응답 JSON..., "case_id":"C_img_001"}'

3) 컨텍스트
curl "http://localhost:8000/graph/context?image_id=IMG_001&k=2&mode=triples"

4) LLM (VGL)
curl -X POST http://localhost:8000/llm/answer \
  -H "Content-Type: application/json" \
  -d '{"mode":"VGL","image_id":"IMG_001","style":"one_line"}'
