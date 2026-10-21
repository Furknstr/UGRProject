"""run_pipeline.py — CLI entry point for the UGR Booking pipeline.

Usage
-----
Run all 34 emails through the real Ollama model:
    uv run python scripts/run_pipeline.py

Re-use a previously written LLM-response cache (skip Ollama):
    uv run python scripts/run_pipeline.py --reuse-llm-cache

Write responses to cache while running (first real run):
    uv run python scripts/run_pipeline.py --reuse-llm-cache --write-cache

Override cache file location:
    uv run python scripts/run_pipeline.py --reuse-llm-cache --cache-file cache/llm_cache.json

Use the built-in FakeLLMParser (empty stubs, for smoke-testing the plumbing):
    uv run python scripts/run_pipeline.py --fake

Cache format
------------
JSON file: { "EMAIL-001": { ...LLMResponse JSON... }, ... }
When --write-cache is used, the real Ollama response is serialised and written
after each email so a partial run is still useful.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ugr_booking.models.schemas import LLMResponse
from ugr_booking.pipeline.agent import run_pipeline
from ugr_booking.pipeline.llm_parser import BaseLLMParser, FakeLLMParser, OllamaLLMParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

_DEFAULT_CACHE = Path(__file__).parent.parent / "cache" / "llm_cache.json"


# ---------------------------------------------------------------------------
# Caching LLM wrapper
# ---------------------------------------------------------------------------

class CachedLLMParser(BaseLLMParser):
    """Wraps another parser; reads from / writes to a JSON cache file.

    Read mode (reuse=True, write=False): returns cached response or falls
    back to the inner parser if the email_id is not in the cache.

    Write mode (reuse=True, write=True): always calls inner parser and
    persists responses to the cache file after each call.
    """

    def __init__(
        self,
        inner: BaseLLMParser,
        cache_path: Path,
        write: bool = False,
    ) -> None:
        self._inner = inner
        self._cache_path = cache_path
        self._write = write
        self._cache: dict[str, dict] = {}

        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
                log.info("Loaded LLM cache from %s (%d entries)", cache_path, len(self._cache))
            except Exception as exc:
                log.warning("Cache file unreadable, starting fresh: %s", exc)
        else:
            cache_path.parent.mkdir(parents=True, exist_ok=True)

    def parse(self, email_id: str, subject: str | None, body: str) -> LLMResponse:
        if not self._write and email_id in self._cache:
            log.info("Cache HIT  %s", email_id)
            data = self._cache[email_id]
            data["source_email_id"] = email_id
            return LLMResponse.model_validate(data)

        log.info("Cache MISS %s — calling LLM", email_id)
        result = self._inner.parse(email_id, subject, body)

        if self._write or email_id not in self._cache:
            self._cache[email_id] = json.loads(result.model_dump_json())
            self._flush()

        return result

    def _flush(self) -> None:
        try:
            self._cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.error("Failed to write LLM cache: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="UGR Booking pipeline runner")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use FakeLLMParser stubs (no Ollama, for smoke-testing only)",
    )
    parser.add_argument(
        "--reuse-llm-cache",
        action="store_true",
        help="Read LLM responses from cache; fall back to Ollama on MISS",
    )
    parser.add_argument(
        "--write-cache",
        action="store_true",
        help="Always call Ollama and write responses to cache (ignored without --reuse-llm-cache)",
    )
    parser.add_argument(
        "--cache-file",
        default=str(_DEFAULT_CACHE),
        metavar="PATH",
        help=f"Cache file path (default: {_DEFAULT_CACHE})",
    )
    args = parser.parse_args()

    # Build parser
    if args.fake:
        llm: BaseLLMParser = FakeLLMParser()
    else:
        base_llm = OllamaLLMParser()
        if args.reuse_llm_cache:
            llm = CachedLLMParser(
                inner=base_llm,
                cache_path=Path(args.cache_file),
                write=args.write_cache,
            )
        else:
            llm = base_llm

    results = run_pipeline(llm=llm)

    # Summary
    print(f"\n✓ Processed {len(results)} emails")
    intents: dict[str, int] = {}
    for r in results:
        intents[r.message_intent] = intents.get(r.message_intent, 0) + 1
    for intent, count in sorted(intents.items()):
        print(f"  {intent}: {count}")
    print()
    print("Run  uv run python scripts/eval_real_llm.py  to compare against the golden set.")


if __name__ == "__main__":
    main()
