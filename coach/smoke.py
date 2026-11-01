"""Smoke test: one coaching turn from the configured LLM, with latency and validator verdict.

    python -m coach.smoke [--timeout 30]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from coach.catalogue import families_by_id
from coach.llm import LLMClient
from coach.session import CoachRequest, next_turn
from config import settings

SAMPLE_FIRED = [
    {"id": "R01", "points": 2, "en": "First transfer to this recipient", "values": {}},
    {"id": "R03", "points": 3, "en": "Leaves under 20% of the balance", "values": {}},
    {"id": "R07", "points": 3, "en": "Active phone call during the transfer", "values": {}},
    {"id": "R10", "points": 2, "en": "Note mentions a fee, prize, activation, grant or deposit",
     "values": {"categories": ["prize"]}},
]


async def main(timeout: float, runs: int) -> None:
    client = LLMClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
    print(f"endpoint: {settings.llm_base_url}  model: {settings.llm_model}")
    print(f"reachable: {await client.ping(5)}")
    families = families_by_id()
    for family_id in ["prize_fee", "generic"][:runs]:
        req = CoachRequest(turn=1, fired=SAMPLE_FIRED, family=families[family_id], amount_band=["أغلب رصيدك"])
        if family_id == "generic":
            req.fired = SAMPLE_FIRED[:3]
        result = await next_turn(req, client, timeout)
        print(f"\n--- family={family_id} source={result.source} latency_ms={result.latency_ms} errors={result.errors}")
        print(json.dumps(result.payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()
    asyncio.run(main(args.timeout, args.runs))
