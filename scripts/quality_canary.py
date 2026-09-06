from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from pricewatch.adapters.ozon import OzonSearchAdapter
from pricewatch.adapters.wildberries import WildberriesSearchAdapter
from pricewatch.quality_canary import run_marketplace_canary
from pricewatch.search_plan import SearchPlan, normalize_query
from pricewatch.transport import HttpJsonFetcher, MarketplaceTransportError


def _limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 50:
        raise argparse.ArgumentTypeError("limit must be between 1 and 50")
    return parsed


def _plan_for_query(query: str) -> SearchPlan:
    normalized = normalize_query(query)
    tokens = tuple(dict.fromkeys(normalized.split()))
    return SearchPlan(
        canonical_name=query.strip(),
        primary_query=query.strip(),
        required_tokens=tokens,
        identity_attributes={},
    )


async def _run(args: argparse.Namespace) -> int:
    plan = _plan_for_query(args.query)
    timeout = httpx.Timeout(15.0)
    results: dict[str, object] = {}
    errors: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=timeout) as client:
        fetcher = HttpJsonFetcher(client)
        adapters = {
            "ozon": OzonSearchAdapter(fetcher),
            "wb": WildberriesSearchAdapter(fetcher),
        }
        selected = ("ozon", "wb") if args.marketplace == "both" else (args.marketplace,)
        for name in selected:
            try:
                result = await run_marketplace_canary(
                    plan,
                    adapters[name],
                    limit=args.limit,
                )
            except MarketplaceTransportError as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
                continue
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
                continue
            results[name] = result.to_payload()

    print(
        json.dumps(
            {
                "query": args.query,
                "limit": args.limit,
                "write_mode": "disabled",
                "results": results,
                "errors": errors,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 2 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="No-write Ozon/Wildberries exact-offer quality canary",
    )
    parser.add_argument("--query", required=True)
    parser.add_argument(
        "--marketplace",
        choices=("ozon", "wb", "both"),
        default="both",
    )
    parser.add_argument("--limit", type=_limit, default=10)
    args = parser.parse_args()
    if not args.query.strip():
        parser.error("query must not be empty")
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
