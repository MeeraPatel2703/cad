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
from app.agents.sherlock import run_sherlock
from app.agents.physicist import run_physicist
from app.agents.state import ComparisonState, AuditState
from app.config import settings

import google.generativeai as genai

st.set_page_config(page_title="CAD Inspection Testbed", layout="wide")

st.title("CAD Inspection Testbed")
st.caption("Full visibility into Gemini extraction pipeline")

# Initialize session state for caching
if "master_bytes" not in st.session_state:
    st.session_state.master_bytes = None
    st.session_state.master_filename = None
if "check_bytes" not in st.session_state:
    st.session_state.check_bytes = None
    st.session_state.check_filename = None

# File uploads
col1, col2 = st.columns(2)

with col1:
    st.subheader("Upload Master PDF")
    master_file = st.file_uploader("Drag and drop file here", type=["pdf", "png", "jpg", "jpeg"], key="master",
                                    help="Limit 200MB per file • PDF, PNG, JPG, JPEG")
    # Cache master file when uploaded - use content hash for proper invalidation
    if master_file:
        new_bytes = master_file.getvalue()
        # Check both filename AND content size (simple change detection)
        if (st.session_state.master_bytes is None or
            st.session_state.master_filename != master_file.name or
            len(st.session_state.master_bytes) != len(new_bytes)):
            st.session_state.master_bytes = new_bytes
            st.session_state.master_filename = master_file.name
            # Clear old extraction when new file uploaded
            for key in ["master_data", "master_raw", "master_sherlock", "master_physicist", "comparison"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.toast(f"Master file loaded: {master_file.name}")

    if st.session_state.master_bytes:
        st.caption(f"📄 {st.session_state.master_filename} ({len(st.session_state.master_bytes):,} bytes)")

with col2:
    st.subheader("Upload Check PDF")
    check_file = st.file_uploader("Drag and drop file here", type=["pdf", "png", "jpg", "jpeg"], key="check",
                                   help="Limit 200MB per file • PDF, PNG, JPG, JPEG")
    # Cache check file when uploaded - use content hash for proper invalidation
    if check_file:
        new_bytes = check_file.getvalue()
        # Check both filename AND content size (simple change detection)
        if (st.session_state.check_bytes is None or
            st.session_state.check_filename != check_file.name or
            len(st.session_state.check_bytes) != len(new_bytes)):
            st.session_state.check_bytes = new_bytes
            st.session_state.check_filename = check_file.name
            # Clear old extraction when new file uploaded
            for key in ["check_data", "check_raw", "check_sherlock", "check_physicist", "comparison"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.toast(f"Check file loaded: {check_file.name}")

    if st.session_state.check_bytes:
        st.caption(f"📄 {st.session_state.check_filename} ({len(st.session_state.check_bytes):,} bytes)")


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

        # Debug: Check what we're sending to Gemini
        print(f"[DEBUG] File: {filename}, Size: {len(file_bytes):,} bytes")
        print(f"[DEBUG] Image parts count: {len(image_parts)}")
        for i, img in enumerate(image_parts):
            print(f"[DEBUG] Part {i}: mime_type={img.get('mime_type')}, data_len={len(img.get('data', ''))}")

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

        # Debug: Log response info
        print(f"[DEBUG] Gemini response length: {len(raw_response)} chars")
        print(f"[DEBUG] Response preview: {raw_response[:500]}...")

        # Check for empty or blocked response
        if not raw_response or len(raw_response.strip()) < 10:
            print(f"[DEBUG] WARNING: Empty or very short response!")
            return raw_response, {"error": "Gemini returned empty/short response", "raw_preview": raw_response}

        # Parse
        try:
            extracted = json.loads(raw_response)
            print(f"[DEBUG] Parsed JSON keys: {list(extracted.keys())}")
            print(f"[DEBUG] Dimensions count: {len(extracted.get('dimensions', []))}")

            # Warn if dimensions is empty
            if len(extracted.get("dimensions", [])) == 0:
                print(f"[DEBUG] WARNING: Parsed successfully but 0 dimensions found!")
                print(f"[DEBUG] Full response: {raw_response[:2000]}")

        except json.JSONDecodeError as e:
            print(f"[DEBUG] JSON decode error: {e}")
            import re
            text = raw_response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
                text = re.sub(r',\s*([}\]])', r'\1', text)
                try:
                    extracted = json.loads(text)
                    print(f"[DEBUG] Fallback parsed keys: {list(extracted.keys())}")
                    print(f"[DEBUG] Fallback dimensions count: {len(extracted.get('dimensions', []))}")
                except Exception as e2:
                    print(f"[DEBUG] Fallback parse failed: {e2}")
                    extracted = {"error": f"Failed to parse JSON: {e}", "raw_preview": raw_response[:1000]}
            else:
                extracted = {"error": "No JSON found in response", "raw_preview": raw_response[:1000]}

        return raw_response, extracted
    except Exception as e:
        import traceback
        print(f"[DEBUG] Exception during extraction: {e}")
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return "", {"error": str(e), "raw_preview": traceback.format_exc()[:1000]}
    finally:
        Path(temp_path).unlink(missing_ok=True)


def display_extraction(label: str, raw: str, data: dict):
    """Display extraction results."""
    st.subheader(f"{label} Extraction")

    # Debug info
    if data.get("error"):
        st.error(f"**Extraction Error:** {data.get('error')}")
        if data.get("raw_preview"):
            st.code(data.get("raw_preview"), language="text")

    # Show available keys in the data
    st.caption(f"Data keys: {list(data.keys())}")

    # PROMINENT warning if 0 dimensions
    dims = data.get("dimensions", [])
    if len(dims) == 0:
        st.error(f"⚠️ **{label} has 0 dimensions!** Check the Raw JSON tab to see what Gemini returned.")
        # Show first 1000 chars of raw response immediately
        with st.expander("🔍 Quick debug: Raw Gemini Response (first 1000 chars)", expanded=True):
            st.code(raw[:1000] if raw else "(empty response)", language="json")

    # Tabs for different views
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Dimensions", "Zones", "Parts", "GD&T", "Raw JSON"])

    with tab1:
        st.metric("Dimensions Found", len(dims))

        # Debug: Show if dimensions key exists vs empty
        if "dimensions" not in data:
            st.error("**DEBUG:** 'dimensions' key missing from extraction response!")
        elif len(dims) == 0:
            st.warning("**DEBUG:** 'dimensions' key exists but is empty - Gemini may have failed to extract")

        # Data quality check
        null_value_dims = [d for d in dims if d.get("value") is None]
        if null_value_dims:
            st.warning(f"⚠️ {len(null_value_dims)} dimensions have NULL values (extraction issue)")

        if dims:
            # Create table - ensure all values are strings to avoid Arrow type errors
            table_data = []
            for i, d in enumerate(dims):
                val = d.get("value")
                table_data.append({
                    "#": i + 1,
                    "Value": str(val) if val is not None else "⚠️ NULL",
                    "Unit": str(d.get("unit", "mm")),
                    "Tolerance": str(d.get("tolerance_class") or "-"),
                    "Zone": str(d.get("zone") or "-"),
                    "X": str((d.get("coordinates") or {}).get("x", "-")),
                    "Y": str((d.get("coordinates") or {}).get("y", "-")),
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


async def run_sherlock_async(machine_state: dict) -> dict:
    """Run Sherlock cross-verification."""
    state: AuditState = {
        "drawing_id": "testbed",
        "file_path": "",
        "machine_state": machine_state,
        "findings": [],
        "agent_log": [],
        "status": "ingested",
        "rfi": None,
        "inspection_sheet": None,
        "integrity_score": None,
        "reflexion_count": 0,
        "crop_region": None,
    }
    return await run_sherlock(state)


async def run_physicist_async(machine_state: dict, existing_findings: list) -> dict:
    """Run Physicist physics validation."""
    state: AuditState = {
        "drawing_id": "testbed",
        "file_path": "",
        "machine_state": machine_state,
        "findings": existing_findings,
        "agent_log": [],
        "status": "verified",
        "rfi": None,
        "inspection_sheet": None,
        "integrity_score": None,
        "reflexion_count": 0,
        "crop_region": None,
    }
    return await run_physicist(state)


def display_sherlock(result: dict, label: str = ""):
    """Display Sherlock cross-verification results."""
    st.subheader(f"🔍 Sherlock Agent {label}")
    st.caption("Cross-verification: consensus, envelope, omission, decimal consistency")

    findings = [f for f in result.get("findings", []) if f.get("source_agent") == "sherlock"]

    col1, col2, col3 = st.columns(3)
    critical = [f for f in findings if f.get("severity") == "critical"]
    warnings = [f for f in findings if f.get("severity") == "warning"]
    info = [f for f in findings if f.get("severity") == "info"]

    col1.metric("Critical", len(critical), delta=None)
    col2.metric("Warnings", len(warnings), delta=None)
    col3.metric("Info", len(info), delta=None)

    if not findings:
        st.success("No issues found by Sherlock!")
    else:
        # Group by category
        categories = {}
        for f in findings:
            cat = f.get("category", "other")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(f)

        for cat, cat_findings in categories.items():
            st.markdown(f"**{cat.upper().replace('_', ' ')}** ({len(cat_findings)} issues)")
            for f in cat_findings:
                severity = f.get("severity", "info")
                icon = "🔴" if severity == "critical" else "🟡" if severity == "warning" else "🔵"
                title = f.get('description', '')[:60] + "..." if len(f.get('description', '')) > 60 else f.get('description', '')
                with st.expander(f"{icon} [{f.get('finding_type')}] {title}"):
                    st.write(f"**Type:** {f.get('finding_type')}")
                    st.write(f"**Severity:** {severity.upper()}")
                    st.write(f"**Category:** {f.get('category', 'N/A')}")
                    st.write(f"**Description:** {f.get('description')}")

                    if f.get("affected_features"):
                        st.write(f"**Affected Features:** {', '.join(f.get('affected_features'))}")
                    if f.get("zone"):
                        st.write(f"**Zone:** {f.get('zone')}")
                    if f.get("item_number"):
                        st.write(f"**Item #:** {f.get('item_number')}")
                    if f.get("coordinates"):
                        st.write(f"**Coordinates:** X={f['coordinates'].get('x')}, Y={f['coordinates'].get('y')}")

                    if f.get("evidence"):
                        st.write("---")
                        st.write("**Evidence:**")
                        ev = f.get("evidence")
                        if ev.get("expected"):
                            st.write(f"- Expected: `{ev.get('expected')}`")
                        if ev.get("found"):
                            st.write(f"- Found: `{ev.get('found')}`")
                        if ev.get("views"):
                            st.write(f"- Views: {', '.join(ev.get('views'))}")
                        if ev.get("standard_reference"):
                            st.write(f"- Standard: {ev.get('standard_reference')}")

                    if f.get("recommendation"):
                        st.write("---")
                        st.info(f"**Recommendation:** {f.get('recommendation')}")


def display_physicist(result: dict, label: str = ""):
    """Display Physicist physics validation results."""
    st.subheader(f"⚙️ Physicist Agent {label}")
    st.caption("Physics validation: tolerance fits, bearing fits, threads, mass properties, structural integrity")

    findings = [f for f in result.get("findings", []) if f.get("source_agent") == "physicist"]

    col1, col2 = st.columns(2)
    critical = [f for f in findings if f.get("severity") == "critical"]
    warnings = [f for f in findings if f.get("severity") == "warning"]

    col1.metric("Critical", len(critical), delta=None)
    col2.metric("Warnings", len(warnings), delta=None)

    if not findings:
        st.success("No physics issues found!")
    else:
        # Group by category
        categories = {}
        for f in findings:
            cat = f.get("category", "other")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(f)

        for cat, cat_findings in categories.items():
            st.markdown(f"**{cat.upper().replace('_', ' ')}** ({len(cat_findings)} issues)")
            for f in cat_findings:
                severity = f.get("severity", "warning")
                icon = "🔴" if severity == "critical" else "🟡"
                title = f.get('description', '')[:60] + "..." if len(f.get('description', '')) > 60 else f.get('description', '')
                with st.expander(f"{icon} [{f.get('finding_type')}] {title}"):
                    st.write(f"**Type:** {f.get('finding_type')}")
                    st.write(f"**Severity:** {severity.upper()}")
                    st.write(f"**Category:** {f.get('category', 'N/A')}")
                    st.write(f"**Description:** {f.get('description')}")

                    if f.get("affected_features"):
                        st.write(f"**Affected Features:** {', '.join(f.get('affected_features'))}")
                    if f.get("zone"):
                        st.write(f"**Zone:** {f.get('zone')}")
                    if f.get("item_number"):
                        st.write(f"**Item #:** {f.get('item_number')}")
                    if f.get("coordinates"):
                        st.write(f"**Coordinates:** X={f['coordinates'].get('x')}, Y={f['coordinates'].get('y')}")

                    if f.get("evidence"):
                        st.write("---")
                        st.write("**Evidence (Machinery Handbook):**")
                        ev = f.get("evidence")
                        if ev.get("calculated"):
                            st.write(f"- Calculated: `{ev.get('calculated')}`")
                        if ev.get("specified"):
                            st.write(f"- Specified: `{ev.get('specified')}`")
                        if ev.get("formula"):
                            st.code(ev.get("formula"), language="text")
                        if ev.get("handbook_reference"):
                            st.write(f"- 📖 Reference: {ev.get('handbook_reference')}")

                    if f.get("recommendation"):
                        st.write("---")
                        st.info(f"**Recommendation:** {f.get('recommendation')}")


def display_comparison(result: dict):
    """Display comparison results."""
    st.header("4️⃣ Comparator Agent")

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

    # Missing dimensions analysis
    st.subheader("Missing Dimensions Analysis")
    not_found_items = [c for c in comparisons if c.get("status") == "not_found"]
    if not_found_items:
        st.error(f"**{len(not_found_items)} dimensions from Master NOT FOUND in Check:**")
        for item in not_found_items:
            with st.expander(f"#{item['balloon_number']}: {item.get('master_nominal')} {item.get('master_unit', 'mm')} - {item.get('zone', 'Unknown zone')}"):
                st.write(f"**Feature:** {item.get('feature_description', 'N/A')}")
                st.write(f"**Nominal Value:** {item.get('master_nominal')} {item.get('master_unit', 'mm')}")
                st.write(f"**Tolerance Class:** {item.get('master_tolerance_class') or 'Not specified'}")
                st.write(f"**Zone:** {item.get('zone') or 'Not specified'}")
                coords = item.get('master_coordinates')
                if coords:
                    st.write(f"**Location:** X={coords.get('x')}, Y={coords.get('y')}")
                st.write(f"**Notes:** {item.get('notes', '')}")
    else:
        st.success("All master dimensions found in check drawing!")

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
has_files = st.session_state.master_bytes and st.session_state.check_bytes
has_extractions = hasattr(st.session_state, 'master_data') and hasattr(st.session_state, 'check_data')

# Show cache status
if has_extractions:
    cache_col1, cache_col2 = st.columns(2)
    with cache_col1:
        m_dims = len(st.session_state.master_data.get('dimensions', []))
        st.success(f"✓ Master cached: {m_dims} dimensions")
    with cache_col2:
        c_dims = len(st.session_state.check_data.get('dimensions', []))
        st.success(f"✓ Check cached: {c_dims} dimensions")

if has_files:
    # Action buttons
    btn_col1, btn_col2, btn_col3 = st.columns([3, 1, 1])

    with btn_col1:
        run_full = st.button("🚀 Run All 4 Agents", type="primary", disabled=not has_files,
                             help="Ingestor → Sherlock → Physicist → Comparator")

    with btn_col2:
        force_reextract = st.button("🔄 Force Re-extract", type="secondary",
                                    help="Clear extractions and re-run Ingestor only")

    with btn_col3:
        if st.button("🗑️ Clear All", type="secondary"):
            for key in ["master_bytes", "master_filename", "check_bytes", "check_filename",
                        "master_data", "master_raw", "check_data", "check_raw", "comparison",
                        "master_sherlock", "master_physicist", "check_sherlock", "check_physicist"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    # Force re-extract clears cached extractions
    if force_reextract:
        for key in ["master_data", "master_raw", "check_data", "check_raw",
                    "master_sherlock", "master_physicist", "check_sherlock", "check_physicist", "comparison"]:
            if key in st.session_state:
                del st.session_state[key]
        st.info("Cache cleared - extractions will re-run")

    # Full extraction + comparison
    if run_full or force_reextract:
        master_bytes = st.session_state.master_bytes
        check_bytes = st.session_state.check_bytes

        st.write(f"Master file size: {len(master_bytes):,} bytes")
        st.write(f"Check file size: {len(check_bytes):,} bytes")

        # ====== AGENT 1: INGESTOR (Master) ======
        with st.spinner("1️⃣ Ingestor: Extracting Master drawing..."):
            try:
                master_raw, master_data = asyncio.run(
                    run_gemini_extraction(master_bytes, st.session_state.master_filename)
                )
                st.success(f"✓ Master: {len(master_data.get('dimensions', []))} dimensions")
            except Exception as e:
                st.error(f"Master extraction failed: {e}")
                master_raw, master_data = "", {"error": str(e)}

        # ====== AGENT 1: INGESTOR (Check) ======
        with st.spinner("1️⃣ Ingestor: Extracting Check drawing..."):
            try:
                check_raw, check_data = asyncio.run(
                    run_gemini_extraction(check_bytes, st.session_state.check_filename)
                )
                st.success(f"✓ Check: {len(check_data.get('dimensions', []))} dimensions")
            except Exception as e:
                st.error(f"Check extraction failed: {e}")
                check_raw, check_data = "", {"error": str(e)}

        # Store extraction results
        st.session_state.master_raw = master_raw
        st.session_state.master_data = master_data
        st.session_state.check_raw = check_raw
        st.session_state.check_data = check_data

        # ====== AGENT 2: SHERLOCK (Master) ======
        if master_data.get("dimensions"):
            with st.spinner("2️⃣ Sherlock: Cross-verifying Master..."):
                try:
                    master_sherlock = asyncio.run(run_sherlock_async(master_data))
                    st.session_state.master_sherlock = master_sherlock
                    findings = [f for f in master_sherlock.get("findings", []) if f.get("source_agent") == "sherlock"]
                    st.success(f"✓ Master Sherlock: {len(findings)} findings")
                except Exception as e:
                    st.error(f"Master Sherlock failed: {e}")
                    st.session_state.master_sherlock = {"findings": [], "error": str(e)}

        # ====== AGENT 2: SHERLOCK (Check) ======
        if check_data.get("dimensions"):
            with st.spinner("2️⃣ Sherlock: Cross-verifying Check..."):
                try:
                    check_sherlock = asyncio.run(run_sherlock_async(check_data))
                    st.session_state.check_sherlock = check_sherlock
                    findings = [f for f in check_sherlock.get("findings", []) if f.get("source_agent") == "sherlock"]
                    st.success(f"✓ Check Sherlock: {len(findings)} findings")
                except Exception as e:
                    st.error(f"Check Sherlock failed: {e}")
                    st.session_state.check_sherlock = {"findings": [], "error": str(e)}

        # ====== AGENT 3: PHYSICIST (Master) ======
        if master_data.get("dimensions"):
            with st.spinner("3️⃣ Physicist: Validating Master physics..."):
                try:
                    sherlock_findings = st.session_state.get("master_sherlock", {}).get("findings", [])
                    master_physicist = asyncio.run(run_physicist_async(master_data, sherlock_findings))
                    st.session_state.master_physicist = master_physicist
                    findings = [f for f in master_physicist.get("findings", []) if f.get("source_agent") == "physicist"]
                    st.success(f"✓ Master Physicist: {len(findings)} findings")
                except Exception as e:
                    st.error(f"Master Physicist failed: {e}")
                    st.session_state.master_physicist = {"findings": [], "error": str(e)}

        # ====== AGENT 3: PHYSICIST (Check) ======
        if check_data.get("dimensions"):
            with st.spinner("3️⃣ Physicist: Validating Check physics..."):
                try:
                    sherlock_findings = st.session_state.get("check_sherlock", {}).get("findings", [])
                    check_physicist = asyncio.run(run_physicist_async(check_data, sherlock_findings))
                    st.session_state.check_physicist = check_physicist
                    findings = [f for f in check_physicist.get("findings", []) if f.get("source_agent") == "physicist"]
                    st.success(f"✓ Check Physicist: {len(findings)} findings")
                except Exception as e:
                    st.error(f"Check Physicist failed: {e}")
                    st.session_state.check_physicist = {"findings": [], "error": str(e)}

        # ====== AGENT 4: COMPARATOR ======
        if master_data.get("dimensions") and check_data.get("dimensions"):
            with st.spinner("4️⃣ Comparator: Matching dimensions..."):
                try:
                    comparison_result = asyncio.run(
                        run_comparison_async(master_data, check_data)
                    )
                    st.session_state.comparison = comparison_result
                    st.success(f"✓ Comparison: {len(comparison_result.get('comparison_items', []))} items compared")
                except Exception as e:
                    st.error(f"Comparison failed: {e}")
                    import traceback
                    st.code(traceback.format_exc())
        else:
            st.warning(f"Cannot compare: Master has {len(master_data.get('dimensions', []))} dims, Check has {len(check_data.get('dimensions', []))} dims")

    # Re-run buttons for individual agents
    if has_extractions:
        st.divider()
        st.caption("Re-run individual agents (using cached extractions):")
        agent_col1, agent_col2, agent_col3 = st.columns(3)

        with agent_col1:
            rerun_sherlock = st.button("🔍 Re-run Sherlock", help="Re-run cross-verification")
        with agent_col2:
            rerun_physicist = st.button("⚙️ Re-run Physicist", help="Re-run physics validation")
        with agent_col3:
            rerun_comparator = st.button("📊 Re-run Comparator", help="Re-run dimension comparison")

        master_data = st.session_state.master_data
        check_data = st.session_state.check_data

        # Re-run Sherlock only
        if rerun_sherlock:
            st.info("Re-running Sherlock with cached extractions...")
            if master_data.get("dimensions"):
                with st.spinner("2️⃣ Sherlock: Cross-verifying Master..."):
                    try:
                        master_sherlock = asyncio.run(run_sherlock_async(master_data))
                        st.session_state.master_sherlock = master_sherlock
                        findings = [f for f in master_sherlock.get("findings", []) if f.get("source_agent") == "sherlock"]
                        st.success(f"✓ Master Sherlock: {len(findings)} findings")
                    except Exception as e:
                        st.error(f"Master Sherlock failed: {e}")

            if check_data.get("dimensions"):
                with st.spinner("2️⃣ Sherlock: Cross-verifying Check..."):
                    try:
                        check_sherlock = asyncio.run(run_sherlock_async(check_data))
                        st.session_state.check_sherlock = check_sherlock
                        findings = [f for f in check_sherlock.get("findings", []) if f.get("source_agent") == "sherlock"]
                        st.success(f"✓ Check Sherlock: {len(findings)} findings")
                    except Exception as e:
                        st.error(f"Check Sherlock failed: {e}")

        # Re-run Physicist only
        if rerun_physicist:
            st.info("Re-running Physicist with cached extractions...")
            if master_data.get("dimensions"):
                with st.spinner("3️⃣ Physicist: Validating Master physics..."):
                    try:
                        sherlock_findings = st.session_state.get("master_sherlock", {}).get("findings", [])
                        master_physicist = asyncio.run(run_physicist_async(master_data, sherlock_findings))
                        st.session_state.master_physicist = master_physicist
                        findings = [f for f in master_physicist.get("findings", []) if f.get("source_agent") == "physicist"]
                        st.success(f"✓ Master Physicist: {len(findings)} findings")
                    except Exception as e:
                        st.error(f"Master Physicist failed: {e}")

            if check_data.get("dimensions"):
                with st.spinner("3️⃣ Physicist: Validating Check physics..."):
                    try:
                        sherlock_findings = st.session_state.get("check_sherlock", {}).get("findings", [])
                        check_physicist = asyncio.run(run_physicist_async(check_data, sherlock_findings))
                        st.session_state.check_physicist = check_physicist
                        findings = [f for f in check_physicist.get("findings", []) if f.get("source_agent") == "physicist"]
                        st.success(f"✓ Check Physicist: {len(findings)} findings")
                    except Exception as e:
                        st.error(f"Check Physicist failed: {e}")

        # Re-run Comparator only
        if rerun_comparator:
            st.info("Re-running Comparator with cached extractions...")
            if master_data.get("dimensions") and check_data.get("dimensions"):
                with st.spinner("4️⃣ Comparator: Matching dimensions..."):
                    try:
                        comparison_result = asyncio.run(
                            run_comparison_async(master_data, check_data)
                        )
                        st.session_state.comparison = comparison_result
                        st.success(f"✓ Comparison: {len(comparison_result.get('comparison_items', []))} items compared")
                    except Exception as e:
                        st.error(f"Comparison failed: {e}")
                        import traceback
                        st.code(traceback.format_exc())
            else:
                st.warning(f"Cannot compare: Master has {len(master_data.get('dimensions', []))} dims, Check has {len(check_data.get('dimensions', []))} dims")

# Display results if available
if hasattr(st.session_state, 'master_data'):
    st.divider()

    # Quick summary of dimension differences
    master_dims = st.session_state.master_data.get("dimensions", [])
    check_dims = st.session_state.check_data.get("dimensions", [])
    diff = len(master_dims) - len(check_dims)

    if diff != 0:
        st.warning(f"**Dimension Count Mismatch:** Master has {len(master_dims)}, Check has {len(check_dims)} ({abs(diff)} {'more' if diff > 0 else 'fewer'} in master)")
    else:
        st.info(f"Both drawings have {len(master_dims)} dimensions")

    # ========================================
    # AGENT 1: INGESTOR RESULTS
    # ========================================
    st.header("1️⃣ Ingestor Agent")
    st.caption("Vision extraction using Gemini - extracts dimensions, zones, parts, GD&T")

    col1, col2 = st.columns(2)
    with col1:
        display_extraction("Master", st.session_state.master_raw, st.session_state.master_data)
    with col2:
        display_extraction("Check", st.session_state.check_raw, st.session_state.check_data)

    # ========================================
    # AGENT 2: SHERLOCK RESULTS
    # ========================================
    st.divider()
    st.header("2️⃣ Sherlock Agent")
    st.caption("Cross-verification: consensus audit, envelope verification, omission detection, decimal consistency")

    if hasattr(st.session_state, 'master_sherlock') or hasattr(st.session_state, 'check_sherlock'):
        col1, col2 = st.columns(2)
        with col1:
            if hasattr(st.session_state, 'master_sherlock'):
                display_sherlock(st.session_state.master_sherlock, "(Master)")
            else:
                st.info("Master Sherlock not run yet")
        with col2:
            if hasattr(st.session_state, 'check_sherlock'):
                display_sherlock(st.session_state.check_sherlock, "(Check)")
            else:
                st.info("Check Sherlock not run yet")
    else:
        st.info("Sherlock agent not run yet. Click 'Run Extraction & Comparison' to run all agents.")

    # ========================================
    # AGENT 3: PHYSICIST RESULTS
    # ========================================
    st.divider()
    st.header("3️⃣ Physicist Agent")
    st.caption("Physics validation: tolerance fits (ISO), mass properties, pressure safety")

    if hasattr(st.session_state, 'master_physicist') or hasattr(st.session_state, 'check_physicist'):
        col1, col2 = st.columns(2)
        with col1:
            if hasattr(st.session_state, 'master_physicist'):
                display_physicist(st.session_state.master_physicist, "(Master)")
            else:
                st.info("Master Physicist not run yet")
        with col2:
            if hasattr(st.session_state, 'check_physicist'):
                display_physicist(st.session_state.check_physicist, "(Check)")
            else:
                st.info("Check Physicist not run yet")
    else:
        st.info("Physicist agent not run yet. Click 'Run Extraction & Comparison' to run all agents.")

    # ========================================
    # AGENT 4: COMPARATOR RESULTS
    # ========================================
    st.divider()
    if hasattr(st.session_state, 'comparison'):
        display_comparison(st.session_state.comparison)
    else:
        st.header("4️⃣ Comparator Agent")
        st.info("Comparator not run yet. Click 'Run Extraction & Comparison' to run all agents.")

# Footer
st.divider()
st.caption(f"Vision Model: {settings.VISION_MODEL} | Reasoning Model: {settings.REASONING_MODEL} | API Key: {'✓ configured' if settings.GOOGLE_API_KEY else '✗ missing'}")
