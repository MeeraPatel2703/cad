#!/usr/bin/env python3
"""
Simple Testbed UI - Upload 2 PDFs and see everything Gemini does.

Run: python testbed_ui.py
Open: http://localhost:8501
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

from app.agents.ingestor import _load_images, EXTRACTION_PROMPT
from app.agents.comparator import run_comparator
from app.agents.state import ComparisonState, MachineState
from app.config import settings

import google.generativeai as genai

st.set_page_config(page_title="CAD Inspection Testbed", layout="wide")

st.title("CAD Inspection Testbed")
st.caption("Full visibility into Gemini extraction pipeline")

# File uploads
col1, col2 = st.columns(2)

with col1:
    st.subheader("Master Drawing")
    master_file = st.file_uploader("Upload Master PDF", type=["pdf", "png", "jpg"], key="master")

with col2:
    st.subheader("Check Drawing")
    check_file = st.file_uploader("Upload Check PDF", type=["pdf", "png", "jpg"], key="check")


async def run_gemini_extraction(file_bytes: bytes, filename: str) -> tuple[str, dict]:
    """Run Gemini extraction and return raw response + parsed data."""
    genai.configure(api_key=settings.GOOGLE_API_KEY)

    # Save temp file
    import tempfile
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(file_bytes)
        temp_path = f.name

    try:
        image_parts = _load_images(temp_path)
        model = genai.GenerativeModel(settings.VISION_MODEL)

        content_parts = []
        for img in image_parts:
            content_parts.append({"inline_data": img})
        content_parts.append(EXTRACTION_PROMPT)

        response = await model.generate_content_async(
            content_parts,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        raw_response = response.text

        # Parse
        try:
            extracted = json.loads(raw_response)
        except json.JSONDecodeError:
            import re
            text = raw_response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
                text = re.sub(r',\s*([}\]])', r'\1', text)
                try:
                    extracted = json.loads(text)
                except:
                    extracted = {"error": "Failed to parse JSON"}
            else:
                extracted = {"error": "No JSON found"}

        return raw_response, extracted
    finally:
        Path(temp_path).unlink(missing_ok=True)


def display_extraction(label: str, raw: str, data: dict):
    """Display extraction results."""
    st.subheader(f"{label} Extraction")

    # Tabs for different views
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Dimensions", "Zones", "Parts", "GD&T", "Raw JSON"])

    with tab1:
        dims = data.get("dimensions", [])
        st.metric("Dimensions Found", len(dims))
        if dims:
            # Create table
            table_data = []
            for i, d in enumerate(dims):
                table_data.append({
                    "#": i + 1,
                    "Value": d.get("value"),
                    "Unit": d.get("unit", "mm"),
                    "Tolerance": d.get("tolerance_class") or "-",
                    "Zone": d.get("zone") or "-",
                    "X": d.get("coordinates", {}).get("x", "-"),
                    "Y": d.get("coordinates", {}).get("y", "-"),
                })
            st.dataframe(table_data, use_container_width=True, height=400)

    with tab2:
        zones = data.get("zones", [])
        st.metric("Zones Found", len(zones))
        for z in zones:
            with st.expander(z.get("name", "Unknown")):
                st.json(z)

    with tab3:
        parts = data.get("part_list", [])
        st.metric("Parts Found", len(parts))
        if parts:
            st.dataframe(parts, use_container_width=True)

    with tab4:
        gdt = data.get("gdt_callouts", [])
        st.metric("GD&T Callouts", len(gdt))
        if gdt:
            st.dataframe(gdt, use_container_width=True)

    with tab5:
        st.text_area("Raw Gemini Response", raw[:10000], height=300)
        st.json(data)


async def run_comparison_async(master_data: dict, check_data: dict) -> dict:
    """Run comparison."""
    state: ComparisonState = {
        "session_id": "testbed",
        "master_drawing_id": "master",
        "master_file_path": "",
        "check_drawing_id": "check",
        "check_file_path": "",
        "master_machine_state": master_data,
        "check_machine_state": check_data,
        "comparison_items": [],
        "findings": [],
        "agent_log": [],
        "status": "started",
        "master_balloon_data": [],
        "check_balloon_data": [],
        "summary": None,
        "rfi": None,
    }
    return await run_comparator(state)


def display_comparison(result: dict):
    """Display comparison results."""
    st.header("Comparison Results")

    summary = result.get("summary", {})

    # Metrics row
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total", summary.get("total_dimensions", 0))
    col2.metric("Pass", summary.get("pass", 0), delta=None)
    col3.metric("Fail", summary.get("fail", 0), delta=None)
    col4.metric("Warning", summary.get("warning", 0), delta=None)
    col5.metric("Not Found", summary.get("not_found", 0), delta=None)

    st.metric("Score", f"{summary.get('score', 0):.1f}%")

    # Comparison items
    comparisons = result.get("comparison_items", [])

    # Group by status
    tabs = st.tabs(["All", "Pass", "Fail", "Warning", "Not Found"])

    def show_items(items):
        if not items:
            st.info("No items")
            return
        table = []
        for c in items:
            table.append({
                "#": c.get("balloon_number"),
                "Feature": c.get("feature_description", "")[:50],
                "Zone": c.get("zone") or "-",
                "Nominal": c.get("master_nominal"),
                "Actual": c.get("check_actual"),
                "Deviation": c.get("deviation"),
                "Status": c.get("status"),
            })
        st.dataframe(table, use_container_width=True, height=400)

    with tabs[0]:
        show_items(comparisons)
    with tabs[1]:
        show_items([c for c in comparisons if c.get("status") == "pass"])
    with tabs[2]:
        show_items([c for c in comparisons if c.get("status") == "fail"])
    with tabs[3]:
        show_items([c for c in comparisons if c.get("status") == "warning"])
    with tabs[4]:
        show_items([c for c in comparisons if c.get("status") == "not_found"])

    # Balloon data
    st.subheader("Balloon Overlay Data")
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        st.write("**Master Balloons**")
        st.json(result.get("master_balloon_data", [])[:20])
    with bcol2:
        st.write("**Check Balloons**")
        st.json(result.get("check_balloon_data", [])[:20])


# Main logic
if master_file and check_file:
    if st.button("Run Extraction & Comparison", type="primary"):
        # Read files upfront to avoid stream issues
        master_bytes = master_file.getvalue()
        check_bytes = check_file.getvalue()

        st.write(f"Master file size: {len(master_bytes)} bytes")
        st.write(f"Check file size: {len(check_bytes)} bytes")

        with st.spinner("Running Gemini extraction on Master..."):
            try:
                master_raw, master_data = asyncio.run(
                    run_gemini_extraction(master_bytes, master_file.name)
                )
                st.success(f"Master: {len(master_data.get('dimensions', []))} dimensions")
            except Exception as e:
                st.error(f"Master extraction failed: {e}")
                master_raw, master_data = "", {"error": str(e)}

        with st.spinner("Running Gemini extraction on Check..."):
            try:
                check_raw, check_data = asyncio.run(
                    run_gemini_extraction(check_bytes, check_file.name)
                )
                st.success(f"Check: {len(check_data.get('dimensions', []))} dimensions")
            except Exception as e:
                st.error(f"Check extraction failed: {e}")
                check_raw, check_data = "", {"error": str(e)}

        # Store in session state
        st.session_state.master_raw = master_raw
        st.session_state.master_data = master_data
        st.session_state.check_raw = check_raw
        st.session_state.check_data = check_data

        # Run comparison
        if master_data.get("dimensions") and check_data.get("dimensions"):
            with st.spinner("Running comparison..."):
                comparison_result = asyncio.run(
                    run_comparison_async(master_data, check_data)
                )
                st.session_state.comparison = comparison_result

# Display results if available
if hasattr(st.session_state, 'master_data'):
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        display_extraction("Master", st.session_state.master_raw, st.session_state.master_data)
    with col2:
        display_extraction("Check", st.session_state.check_raw, st.session_state.check_data)

if hasattr(st.session_state, 'comparison'):
    st.divider()
    display_comparison(st.session_state.comparison)

# Footer
st.divider()
st.caption(f"Model: {settings.VISION_MODEL} | API Key: {'configured' if settings.GOOGLE_API_KEY else 'missing'}")
