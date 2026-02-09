#!/usr/bin/env python3
"""Quick test script to run comparison directly without FastAPI hot-reload."""
import asyncio
import json
import sys
import logging
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

# Enable logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from app.agents.comparison_graph import run_comparison

async def main():
    uploads = Path("uploads")

    # Find the most recent PDF pairs (one ~107k, one ~128k as typical master/check)
    pdfs = sorted(uploads.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)

    if len(pdfs) < 2:
        print("Need at least 2 PDFs in uploads/")
        return

    # Get the two most recent
    check_file = str(pdfs[0])
    master_file = str(pdfs[1])

    print("=" * 60)
    print("COMPARISON TEST")
    print("=" * 60)
    print(f"Master: {master_file}")
    print(f"Check:  {check_file}")
    print()

    import uuid
    test_session_id = str(uuid.uuid4())

    try:
        result = await run_comparison(
            session_id=test_session_id,
            master_file=master_file,
            check_file=check_file,
            master_drawing_id=str(uuid.uuid4()),
            check_drawing_id=str(uuid.uuid4()),
            master_machine_state=None,  # Force fresh extraction
        )

        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)

        master_ms = result.get("master_machine_state", {})
        check_ms = result.get("check_machine_state", {})

        print(f"\nMaster extracted: {len(master_ms.get('dimensions', []))} dimensions")
        print(f"Check extracted:  {len(check_ms.get('dimensions', []))} dimensions")

        master_balloons = result.get("master_balloon_data", [])
        check_balloons = result.get("check_balloon_data", [])

        print(f"\nMaster balloons: {len(master_balloons)}")
        print(f"Check balloons:  {len(check_balloons)}")

        summary = result.get("summary", {})
        print(f"\nSummary:")
        print(json.dumps(summary, indent=2))

        # Show balloon coordinate ranges
        if master_balloons:
            x_vals = [b["coordinates"]["x"] for b in master_balloons if b.get("coordinates")]
            y_vals = [b["coordinates"]["y"] for b in master_balloons if b.get("coordinates")]
            if x_vals and y_vals:
                print(f"\nMaster balloon X range: {min(x_vals)} - {max(x_vals)}")
                print(f"Master balloon Y range: {min(y_vals)} - {max(y_vals)}")

        if check_balloons:
            x_vals = [b["coordinates"]["x"] for b in check_balloons if b.get("coordinates")]
            y_vals = [b["coordinates"]["y"] for b in check_balloons if b.get("coordinates")]
            if x_vals and y_vals:
                print(f"\nCheck balloon X range: {min(x_vals)} - {max(x_vals)}")
                print(f"Check balloon Y range: {min(y_vals)} - {max(y_vals)}")

        # Show first few comparisons
        comparisons = result.get("comparison_items", [])
        print(f"\n\nFirst 10 comparisons:")
        for c in comparisons[:10]:
            print(f"  #{c['balloon_number']}: {c['status']} - master={c.get('master_nominal')} check={c.get('check_actual')}")

    except Exception as e:
        import traceback
        print(f"ERROR: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
