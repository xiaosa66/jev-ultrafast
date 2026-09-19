#!/usr/bin/env python3
"""Measure the token cost of a single real jev decision, broken down by part.

Usage (run from the jev-ultrafast directory):

    uv run --env-file .env python scripts/measure_tokens.py --url https://example.com --goal "click the first link"

    # Add 10 steps of history to see whether the cost accumulates
    uv run --env-file .env python scripts/measure_tokens.py --url <url> --goal "<goal>" --history 10

Note: this makes one real (billed) evaluation-model call. The page is closed afterwards.
"""

import argparse
import json
import os
from pathlib import Path

from jev_ultrafast.browser import Browser
from jev_ultrafast.model import action_space, choose


def load_env():
    """Mirror demo.load_environment() so this runs without uv --env-file."""
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--goal", required=True)
    ap.add_argument("--history", type=int, default=0,
                    help="Number of synthetic history entries, to observe accumulation")
    args = ap.parse_args()

    load_env()
    browser = Browser(args.url)
    try:
        page = browser.observe(screenshot=False)
        elements, targets, controls = action_space(page["actions"])

        print(f"page            : {page['url']}")
        print(f"viewport        : {page['w']}x{page['h']}")
        print(f"visible text    : {len(page['text'])} chars")
        print(f"interactive els : {len(page['actions'])} (dropped {page.get('omitted_actions', 0)})")
        print(f"element table   : {len(elements)} rows")
        print(f"target options  : {sum(len(v) for v in targets.values())}")
        print()

        history = [
            {"step": i, "action": f"[{i}] click", "kind": "click",
             "choice": "e1", "text": None, "page_changed": True}
            for i in range(1, args.history + 1)
        ] or []

        decision = choose(page, args.goal, history)
        usage = decision["usage"]
        body = json.dumps(decision["request"], ensure_ascii=False)
        state = json.dumps(decision["request"]["state"], ensure_ascii=False)
        questions = json.dumps(decision["request"]["questions"], ensure_ascii=False)

        print(f"=== measured ({len(history)} history entries) ===")
        print(f"input tokens    : {usage.get('inputTokens')}")
        print(f"output tokens   : {usage.get('outputTokens')}")
        print(f"latency         : {decision['latency_ms']} ms")
        print()
        print("=== request body size (chars) ===")
        print(f"total {len(body)} | state {len(state)} | questions {len(questions)}"
              f" ({len(questions) / max(len(body), 1) * 100:.0f}%)")
        if len(elements):
            print(f"hint: ~{usage.get('inputTokens', 0) / max(len(elements), 1):.0f} input tokens per element")
    finally:
        browser.close()


if __name__ == "__main__":
    main()
