api/routers/vision.py 를 점검하고, 캡션 응답을 다음 형식으로 통일하세요.

{
  "image": {"id":"IMG_001","path":"/data/img_001.png","modality":"XR"},
  "report":{"id":"r_<uuid>","text":"Chest X-ray – ...","model":"qwen2-vl","conf":0.83,"ts":"<iso8601>"},
  "findings":[{"id":"f_<uuid>","type":"nodule","location":"RUL","size_cm":1.8,"conf":0.87}]
}

- findings[].id 는 (image_id+type+location+rounded_size) 기반 해시로 생성
- location/size_cm 없으면 필드 생략
