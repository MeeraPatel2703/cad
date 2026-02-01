#!/usr/bin/env python3
"""
Testbed for CAD inspection pipeline - full visibility into Gemini outputs.

Usage:
    python testbed.py master.pdf check.pdf
"""
import asyncio
import json
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.agents.ingestor import run_ingestor, _load_images, EXTRACTION_PROMPT
from app.agents.comparator import run_comparator
from app.agents.physicist import run_physicist
from app.agents.sherlock import run_sherlock
from app.agents.state import AuditState, ComparisonState, MachineState
from app.config import settings

import google.generativeai as genai


def print_section(title: str):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80 + "\n")


def print_json(data: dict, indent: int = 2):
    print(json.dumps(data, indent=indent, default=str))


async def run_raw_gemini(file_path: str, max_retries: int = 3) -> dict:
    """Run Gemini directly and show raw response."""
    genai.configure(api_key=settings.GOOGLE_API_KEY)

    print(f"Loading file: {file_path}")
    image_parts = _load_images(file_path)
    print(f"Loaded {len(image_parts)} image part(s)")

    model = genai.GenerativeModel(settings.VISION_MODEL)
    print(f"Using model: {settings.VISION_MODEL}")

    content_parts = []
    for img in image_parts:
        content_parts.append({"inline_data": img})
    content_parts.append(EXTRACTION_PROMPT)

    print("\nSending to Gemini Vision...")
    print("-" * 40)

    # Retry logic for transient failures
    last_error = None
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                wait_time = 2 ** attempt  # Exponential backoff: 2, 4, 8 seconds
                print(f"Retry attempt {attempt + 1}/{max_retries} after {wait_time}s wait...")
                await asyncio.sleep(wait_time)

            response = await model.generate_content_async(
                content_parts,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                request_options={"timeout": 300},  # 5 minute timeout
            )
            break  # Success
        except Exception as e:
            last_error = e
            print(f"Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
            if attempt == max_retries - 1:
                print(f"All {max_retries} attempts failed. Returning empty result.")
                return {}

    if last_error and 'response' not in dir():
        return {}

    print("\n[RAW GEMINI RESPONSE]")
    print("-" * 40)
    print(response.text[:5000] if len(response.text) > 5000 else response.text)
    if len(response.text) > 5000:
        print(f"\n... (truncated, total {len(response.text)} chars)")

    # Parse response
    try:
        extracted = json.loads(response.text)
    except json.JSONDecodeError as e:
        print(f"\n[JSON PARSE ERROR]: {e}")
        extracted = repair_truncated_json(response.text)

    return extracted


def extract_partial_data(text: str) -> dict:
    """Extract whatever partial data we can from malformed JSON."""
    import re

    result = {"zones": [], "dimensions": [], "part_list": [], "gdt_callouts": [], "title_block": {}}

    # Try to extract dimensions array - handle nested objects like coordinates
    dims_match = re.search(r'"dimensions"\s*:\s*\[', text)
    if dims_match:
        dims_start = dims_match.end()
        # Find complete dimension objects by matching balanced braces
        depth = 0
        obj_start = None
        for i, char in enumerate(text[dims_start:], dims_start):
            if char == '{':
                if depth == 0:
                    obj_start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and obj_start is not None:
                    try:
                        dim_text = text[obj_start:i+1]
                        dim = json.loads(dim_text)
                        if "value" in dim:
                            result["dimensions"].append(dim)
                    except:
                        pass
                    obj_start = None
            elif char == ']' and depth == 0:
                break

    # Try to extract zones array - handle nested bounds objects
    zones_match = re.search(r'"zones"\s*:\s*\[', text)
    if zones_match:
        zones_start = zones_match.end()
        depth = 0
        obj_start = None
        for i, char in enumerate(text[zones_start:], zones_start):
            if char == '{':
                if depth == 0:
                    obj_start = i
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0 and obj_start is not None:
                    try:
                        zone_text = text[obj_start:i+1]
                        zone = json.loads(zone_text)
                        if "name" in zone:
                            result["zones"].append(zone)
                    except:
                        pass
                    obj_start = None
            elif char == ']' and depth == 0:
                break

    if result["dimensions"] or result["zones"]:
        print(f"[PARTIAL EXTRACTION - {len(result['dimensions'])} dims, {len(result['zones'])} zones]")
    else:
        print("[EXTRACTION FAILED COMPLETELY]")

    return result


def repair_truncated_json(text: str) -> dict:
    """Attempt to repair truncated JSON from Gemini."""
    import re

    # Find the start of JSON
    start = text.find("{")
    if start < 0:
        print("[JSON FIX FAILED - no opening brace]")
        return {}

    text = text[start:]

    # Remove trailing comma before closing brackets
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # Try parsing as-is first
    try:
        result = json.loads(text)
        print("[JSON FIXED AND PARSED]")
        return result
    except json.JSONDecodeError:
        pass

    # Count unclosed brackets/braces
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")

    # Truncate at last complete element
    # Find last complete object/array by looking for patterns
    last_complete = max(
        text.rfind("},"),
        text.rfind("}]"),
        text.rfind("],"),
        text.rfind("]]"),
    )

    if last_complete > 0:
        text = text[:last_complete + 1]
        # Recount
        open_braces = text.count("{") - text.count("}")
        open_brackets = text.count("[") - text.count("]")

    # Close any remaining open structures
    text += "]" * max(0, open_brackets)
    text += "}" * max(0, open_braces)

    try:
        result = json.loads(text)
        print(f"[JSON REPAIRED - closed {open_brackets} brackets, {open_braces} braces]")
        return result
    except json.JSONDecodeError as e:
        print(f"[JSON REPAIR FAILED]: {e}")
        # Last resort: extract whatever arrays we can find
        return extract_partial_data(text)


async def run_ingestor_with_logging(file_path: str, label: str) -> dict:
    """Run ingestor with full logging."""
    print_section(f"INGESTING {label.upper()}: {Path(file_path).name}")

    # First show raw Gemini output
    raw_extracted = await run_raw_gemini(file_path)

    print("\n[PARSED EXTRACTION]")
    print("-" * 40)

    # Show each section
    print(f"\nZones ({len(raw_extracted.get('zones', []))}):")
    for z in raw_extracted.get('zones', [])[:10]:
        print(f"  - {z.get('name')}: {z.get('features', [])[:3]}...")

    print(f"\nDimensions ({len(raw_extracted.get('dimensions', []))}):")
    for i, d in enumerate(raw_extracted.get('dimensions', [])[:20]):
        val = d.get('value', '?')
        unit = d.get('unit', 'mm')
        zone = d.get('zone', '?')
        tol = d.get('tolerance_class', '')
        coords = d.get('coordinates', {})
        print(f"  {i+1:3}. {val} {unit} [{tol or 'no-tol'}] in {zone} @ ({coords.get('x','?')}, {coords.get('y','?')})")
    if len(raw_extracted.get('dimensions', [])) > 20:
        print(f"  ... and {len(raw_extracted.get('dimensions', [])) - 20} more")

    print(f"\nPart List ({len(raw_extracted.get('part_list', []))}):")
    for p in raw_extracted.get('part_list', [])[:10]:
        print(f"  - Item {p.get('item_number')}: {p.get('description')} ({p.get('material')})")

    print(f"\nGD&T Callouts ({len(raw_extracted.get('gdt_callouts', []))}):")
    for g in raw_extracted.get('gdt_callouts', [])[:10]:
        print(f"  - {g.get('symbol')} {g.get('value')} datum {g.get('datum')}")

    print(f"\nTitle Block:")
    print_json(raw_extracted.get('title_block', {}))

    print(f"\nRaw Text (first 500 chars):")
    raw_text = raw_extracted.get('raw_text', '')
    if isinstance(raw_text, list):
        raw_text = '\n'.join(raw_text)
    print(raw_text[:500] if raw_text else "(empty)")

    # Validate with MachineState
    print("\n[MACHINESTATE VALIDATION]")
    print("-" * 40)
    try:
        ms = MachineState(**raw_extracted)
        print("SUCCESS - MachineState validated")
        return ms.model_dump()
    except Exception as e:
        print(f"FAILED: {e}")
        # Return raw extracted anyway
        return raw_extracted


async def run_physicist_with_logging(machine_state: dict, label: str) -> dict:
    """Run physicist agent with logging."""
    print_section(f"PHYSICIST ANALYSIS: {label}")

    audit_state: AuditState = {
        "drawing_id": f"test-{label.lower()}",
        "file_path": "",
        "machine_state": machine_state,
        "findings": [],
        "agent_log": [],
        "reflexion_count": 0,
        "status": "ingested",
        "crop_region": None,
        "rfi": None,
        "inspection_sheet": None,
        "integrity_score": None,
    }

    print("Running physics validation...")
    result = await run_physicist(audit_state)

    findings = result.get("findings", [])
    print(f"\nPhysics Findings ({len(findings)}):")
    for f in findings:
        sev = f.get("severity", "?")
        desc = f.get("description", "")[:80]
        ftype = f.get("finding_type", "?")
        print(f"  [{sev.upper()}] {ftype}: {desc}...")

    return result


async def run_sherlock_with_logging(machine_state: dict, label: str) -> dict:
    """Run sherlock agent with logging."""
    print_section(f"SHERLOCK ANALYSIS: {label}")

    audit_state: AuditState = {
        "drawing_id": f"test-{label.lower()}",
        "file_path": "",
        "machine_state": machine_state,
        "findings": [],
        "agent_log": [],
        "reflexion_count": 0,
        "status": "ingested",
        "crop_region": None,
        "rfi": None,
        "inspection_sheet": None,
        "integrity_score": None,
    }

    print("Running cross-verification checks...")
    result = await run_sherlock(audit_state)

    findings = result.get("findings", [])
    print(f"\nSherlock Findings ({len(findings)}):")
    for f in findings:
        sev = f.get("severity", "?")
        desc = f.get("description", "")[:80]
        ftype = f.get("finding_type", "?")
        item = f.get("item_number", "")
        print(f"  [{sev.upper()}] {ftype}: {desc}...")
        if f.get("evidence"):
            print(f"    Evidence: {f['evidence']}")

    return result


async def run_comparison_with_logging(master_state: dict, check_state: dict) -> dict:
    """Run comparator with full logging."""
    print_section("COMPARISON")

    master_dims = master_state.get('dimensions', [])
    check_dims = check_state.get('dimensions', [])

    print(f"Master dimensions: {len(master_dims)}")
    print(f"Check dimensions: {len(check_dims)}")

    state: ComparisonState = {
        "session_id": "test-session",
        "master_drawing_id": "test-master",
        "master_file_path": "",
        "check_drawing_id": "test-check",
        "check_file_path": "",
        "master_machine_state": master_state,
        "check_machine_state": check_state,
        "comparison_items": [],
        "findings": [],
        "agent_log": [],
        "status": "started",
        "master_balloon_data": [],
        "check_balloon_data": [],
        "summary": None,
        "rfi": None,
    }

    print("\nRunning comparator...")
    result = await run_comparator(state)

    print("\n[COMPARISON RESULTS]")
    print("-" * 40)

    comparisons = result.get('comparison_items', [])
    print(f"\nTotal comparisons: {len(comparisons)}")

    # Group by status
    by_status = {}
    for c in comparisons:
        s = c.get('status', 'unknown')
        by_status.setdefault(s, []).append(c)

    for status, items in by_status.items():
        print(f"\n{status.upper()} ({len(items)}):")
        for item in items[:10]:
            bn = item.get('balloon_number')
            feat = item.get('feature_description', '')[:40]
            nominal = item.get('master_nominal')
            actual = item.get('check_actual')
            dev = item.get('deviation')
            print(f"  #{bn}: {feat}... | nominal={nominal} actual={actual} dev={dev}")
        if len(items) > 10:
            print(f"  ... and {len(items) - 10} more")

    print("\n[SUMMARY]")
    print("-" * 40)
    print_json(result.get('summary', {}))

    print("\n[MASTER BALLOONS]")
    print("-" * 40)
    master_balloons = result.get('master_balloon_data', [])
    print(f"Count: {len(master_balloons)}")
    for b in master_balloons[:10]:
        print(f"  #{b.get('balloon_number')}: {b.get('value')} @ {b.get('coordinates')}")

    print("\n[CHECK BALLOONS]")
    print("-" * 40)
    check_balloons = result.get('check_balloon_data', [])
    print(f"Count: {len(check_balloons)}")
    for b in check_balloons[:10]:
        print(f"  #{b.get('balloon_number')}: {b.get('value')} @ {b.get('coordinates')}")

    return result


async def main():
    if len(sys.argv) < 3:
        print("Usage: python testbed.py <master.pdf> <check.pdf>")
        print("\nThis testbed shows full visibility into what Gemini extracts.")
        sys.exit(1)

    master_path = sys.argv[1]
    check_path = sys.argv[2]

    if not Path(master_path).exists():
        print(f"Error: Master file not found: {master_path}")
        sys.exit(1)
    if not Path(check_path).exists():
        print(f"Error: Check file not found: {check_path}")
        sys.exit(1)

    print_section("CAD INSPECTION TESTBED")
    print(f"Master: {master_path}")
    print(f"Check:  {check_path}")
    print(f"Gemini Vision Model: {settings.VISION_MODEL}")
    print(f"Gemini Reasoning Model: {settings.REASONING_MODEL}")

    # Ingest master
    master_state = await run_ingestor_with_logging(master_path, "MASTER")

    # Ingest check
    check_state = await run_ingestor_with_logging(check_path, "CHECK")

    # Compare
    if master_state.get('dimensions') and check_state.get('dimensions'):
        await run_comparison_with_logging(master_state, check_state)
    else:
        print_section("COMPARISON SKIPPED")
        print("Not enough dimensions extracted to compare.")
        print(f"  Master dimensions: {len(master_state.get('dimensions', []))}")
        print(f"  Check dimensions: {len(check_state.get('dimensions', []))}")

    # Run Physicist on master
    if master_state.get('dimensions'):
        await run_physicist_with_logging(master_state, "MASTER")

    # Run Sherlock on master
    if master_state.get('dimensions'):
        await run_sherlock_with_logging(master_state, "MASTER")

    # Run Sherlock on check
    if check_state.get('dimensions'):
        await run_sherlock_with_logging(check_state, "CHECK")

    print_section("DONE")


if __name__ == "__main__":
    asyncio.run(main())
