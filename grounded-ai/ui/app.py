import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://api:8000")


st.set_page_config(page_title="Ontology • vLM • LLM", page_icon="🧠", layout="wide")

st.title("🧠 Ontology + vLM + LLM Prototype UI")

st.sidebar.header("Quick Actions")
if st.sidebar.button("Health Check"):
    try:
        response = requests.get(f"{API_URL}/health", timeout=3)
        st.sidebar.success(f"API status: {response.json().get('status')}")
    except requests.RequestException as exc:
        st.sidebar.error(f"Failed to reach API: {exc}")

st.markdown(
    """
    This Streamlit app is a lightweight front-end for interacting with the local
    ontology + vLM + LLM stack. Use it to:

    - Upload medical images and run quick captioning via the vision endpoint.
    - Run ad-hoc Cypher queries against Neo4j.
    - Test text/image embedding flows into Qdrant.
    """
)


st.header("Vision + LLM Inference")
uploaded_file = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg"])
prompt = st.text_area("Vision Prompt", value="Summarize the key findings in this image.")
llm_prompt = st.text_area(
    "LLM Follow-up Prompt",
    value="Given the vision summary, what follow-up actions or additional tests would you recommend?",
)
image_id = st.text_input(
    "Image ID (optional, links inference to existing Neo4j Image node)",
    value="",
)
modality = st.text_input(
    "Modality (optional)",
    value="",
)
patient_id = st.text_input(
    "Patient ID (optional)",
    value="",
)
encounter_id = st.text_input(
    "Encounter ID (optional)",
    value="",
)
idempotency_key = st.text_input(
    "Idempotency Key (optional)",
    help="Provide a stable key to prevent duplicate graph writes. Leave blank to auto-generate.",
    value="",
)
persist = st.checkbox(
    "Persist outputs to Neo4j",
    value=bool(image_id),
    help="Stores both VLM and LLM outputs as AIInference nodes if an Image ID is provided.",
)
if st.button("Run Vision Model", disabled=uploaded_file is None):
    if uploaded_file is None:
        st.warning("Please upload an image first.")
    else:
        files = {"image": (uploaded_file.name, uploaded_file.read(), uploaded_file.type)}
        data = {
            "prompt": prompt,
            "llm_prompt": llm_prompt,
            "task": "caption",
            "persist": str(persist).lower(),
        }
        if image_id.strip():
            data["image_id"] = image_id.strip()
        if modality.strip():
            data["modality"] = modality.strip()
        if patient_id.strip():
            data["patient_id"] = patient_id.strip()
        if encounter_id.strip():
            data["encounter_id"] = encounter_id.strip()
        if idempotency_key.strip():
            data["idempotency_key"] = idempotency_key.strip()
        try:
            response = requests.post(f"{API_URL}/vision/inference", data=data, files=files, timeout=120)
            response.raise_for_status()
            payload = response.json()
            st.subheader("Vision Model Output")
            st.success(payload.get("vlm_output"))
            st.caption(
                f"Model: {payload.get('vlm_model')} • Latency: {payload.get('vlm_latency_ms')} ms",
            )
            st.caption(f"Image ID: {payload.get('image_id')}")
            st.subheader("LLM Reasoning")
            st.info(payload.get("llm_output"))
            st.caption(
                f"Model: {payload.get('llm_model')} • Latency: {payload.get('llm_latency_ms')} ms",
            )
            if payload.get("persisted"):
                st.success(
                    f"Persisted inference nodes: VLM={payload.get('vlm_inference_id')} "
                    f"LLM={payload.get('llm_inference_id')}"
                )
            else:
                st.caption("Graph persistence skipped.")
        except requests.RequestException as exc:
            st.error(f"Vision endpoint error: {exc}")


st.header("Cypher Playground")
default_query = "MATCH (p:Patient) RETURN p LIMIT 5"
query = st.text_area("Cypher Query", value=default_query, height=150)
if st.button("Run Cypher"):
    try:
        response = requests.post(
            f"{API_URL}/graph/cypher",
            json={"query": query, "params": {}},
            timeout=10,
        )
        response.raise_for_status()
        st.json(response.json())
    except requests.RequestException as exc:
        st.error(f"Neo4j query failed: {exc}")
