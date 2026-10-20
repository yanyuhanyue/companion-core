"""Explicit, bounded public-source probes. Never run from CI or save a transcript."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from source_reader.bilibili import BilibiliReader
from source_reader.heybox import HeyboxReader
from source_reader.network import PublicHTTP
from source_reader.worker import read_result


class ProbeHTTP(PublicHTTP):
    def __init__(self):
        super().__init__()
        self.receipts = []

    async def get(self, url, budget, role):
        raw = await super().get(url, budget, role)
        self.receipts = list(budget.sources)
        return raw


async def verify(urls, output, source="bilibili"):
    if not 1 <= len(urls) <= 10:
        raise ValueError("Provide 1–10 explicit public source URLs")
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "source": source,
        "credentials_used": False,
        "model_calls": 0,
        "qq_messages": 0,
        "samples": [],
    }
    async with ProbeHTTP() as public:
        reader = HeyboxReader(public) if source == "heybox" else BilibiliReader(public)
        for url in urls:
            public.receipts = []
            result = await read_result(
                reader,
                {
                    "lease_token": "public_acceptance_probe",
                    "operation": "read",
                    "url": url,
                    "max_bytes": 524288,
                    "max_requests": 4,
                    "max_items": 1,
                },
            )
            result.pop("lease_token")
            for item in result["items"]:
                item["text_chars"] = len(item.pop("text"))
            if not result["items"]:
                result["sources"] = public.receipts
            report["samples"].append({"url": url, **result})
            print(
                json.dumps(
                    {"url": url, "status": result["status"], "error_code": result.get("error_code")}
                ),
                flush=True,
            )
            if result.get("error_code") == "UPSTREAM_ACCESS_LIMIT":
                report["stopped_on_access_limit"] = True
                break
            await asyncio.sleep(1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", choices=["bilibili", "heybox"], default="bilibili")
    args = parser.parse_args()
    asyncio.run(verify(args.urls, args.output, args.source))
