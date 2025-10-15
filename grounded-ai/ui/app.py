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


st.header("Vision Inference")
uploaded_file = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg"])
prompt = st.text_area("Prompt", value="Summarize the key findings in this image.")
if st.button("Run Vision Model", disabled=uploaded_file is None):
    if uploaded_file is None:
        st.warning("Please upload an image first.")
    else:
        files = {"image": (uploaded_file.name, uploaded_file.read(), uploaded_file.type)}
        data = {"prompt": prompt, "task": "caption"}
        try:
            response = requests.post(f"{API_URL}/vision/inference", data=data, files=files, timeout=30)
            response.raise_for_status()
            payload = response.json()
            st.success(payload.get("output"))
            st.caption(f"Model: {payload.get('model')} • Latency: {payload.get('latency_ms')} ms")
        except requests.RequestException as exc:
            st.error(f"Vision endpoint error: {exc}")


st.header("Cypher Playground")
default_query = "MATCH (p:Patient) RETURN p LIMIT 5"
query = st.text_area("Cypher Query", value=default_query, height=150)
if st.button("Run Cypher"):
    try:
        response = requests.post(
            f"{API_URL}/kg/cypher",
            json={"query": query, "params": {}},
            timeout=10,
        )
        response.raise_for_status()
        st.json(response.json())
    except requests.RequestException as exc:
        st.error(f"Neo4j query failed: {exc}")
