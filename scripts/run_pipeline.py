"""run_pipeline.py — CLI entry point.

Usage:
    uv run python scripts/run_pipeline.py           # real Ollama
    uv run python scripts/run_pipeline.py --fake    # FakeLLMParser (no Ollama)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make sure the src package is importable when run directly
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ugr_booking.pipeline.agent import run_pipeline
from ugr_booking.pipeline.llm_parser import FakeLLMParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="UGR Booking pipeline runner")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use FakeLLMParser (no Ollama required)",
    )
    args = parser.parse_args()

    llm = FakeLLMParser() if args.fake else None
    results = run_pipeline(llm=llm)

    print(f"\n✓ Processed {len(results)} emails")
    intents: dict[str, int] = {}
    for r in results:
        intents[r.message_intent] = intents.get(r.message_intent, 0) + 1
    for intent, count in sorted(intents.items()):
        print(f"  {intent}: {count}")


if __name__ == "__main__":
    main()
