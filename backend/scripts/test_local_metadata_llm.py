#!/usr/bin/env python3
"""Smoke-test local metadata discovery and LLM dataset recognition.

Examples (run from ``backend``):

    uv run python scripts/test_local_metadata_llm.py --catalog-only
    uv run python scripts/test_local_metadata_llm.py \
      -q "Which day and night satellite layers cover the Bobcat Fire?"

With ``LLM_PROVIDER=mock`` the second command uses a clearly labelled keyword
baseline. Set a real provider, model, and key in the ignored repository ``.env``
to test actual structured model inference.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from wildfire_agent.config import settings
from wildfire_agent.local_catalog import scan_local_data
from wildfire_agent.local_recognition import recognise_local_data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan local dataset metadata, then ask the configured LLM to select data."
    )
    parser.add_argument("-q", "--query", help="Natural-language wildfire data request")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=settings.resolved_local_data_root,
        help="Allow-listed local data root (defaults to LOCAL_DATA_ROOT)",
    )
    parser.add_argument(
        "--catalog-only",
        action="store_true",
        help="Print discovered metadata without invoking a model",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=10_000,
        help="Safety cap for filesystem entries inspected",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    catalog = scan_local_data(args.data_root, max_files=args.max_files)
    print(
        json.dumps(
            {
                "scan": {
                    "root": str(catalog.root),
                    "dataset_count": len(catalog.datasets),
                    "warnings": catalog.warnings,
                },
                "catalog": catalog.prompt_payload(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.catalog_only:
        return 0
    if not args.query:
        print("error: --query is required unless --catalog-only is used", file=sys.stderr)
        return 2

    recognition = await recognise_local_data(args.query, catalog)
    print("\n--- recognition ---")
    print(json.dumps(recognition.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    args = _parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except Exception as exc:  # provider errors should be readable in a demo shell
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
