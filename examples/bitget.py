"""Bitget demo trading regression. One goal per order type; independent count checks.

Scenarios: limit_buy, market_buy, limit_sell, market_sell.
Calls TypeSafe for decisions; never trades real funds — uses the Bitget demo account.

Prerequisites (documented in README):
  - Chrome running with --remote-debugging-port=9222, logged into Bitget, switched to the
    Demo (unified) account. The agent opens a foreground tab in that Chrome.
  - .env with TYPESAFE_API_KEY and TEXT_MODEL_API_KEY (a Vercel vck_ key works for both
    via the gateway; see .env.example).
"""

import argparse
import json
import re
from pathlib import Path

from jev_ultrafast import Agent

URL = "https://www.bitget.com/trade-swap/usdc/BTCPERP"

SCENARIOS = {
    "limit_buy": (
        "Place a limit buy (long) order for 0.001 BTC at price 70000. "
        "Click the 'Limit' tab if not already selected. "
        "Fill the price textbox with 70000. Fill the quantity textbox with 0.001. "
        "Click the 'Buy (long)' button. Click the confirm button in the order confirmation dialog. "
        "Then click the 'Open orders' tab to view the order list. "
        "Stop when the Open orders list shows a new open limit buy order for 0.001 BTC at 70000.",
    ),
    "market_buy": (
        "Place a market buy (long) order for 0.001 BTC. "
        "Click the 'Market' tab. Fill the quantity textbox with 0.001. "
        "Click the 'Buy (long)' button. Click the confirm button in the order confirmation dialog. "
        "Then click the 'Positions' tab to view positions. "
        "Stop when the Positions list shows a long position for 0.001 BTC.",
    ),
    "limit_sell": (
        "Place a limit sell (short) order for 0.001 BTC at price 90000. "
        "Click the 'Limit' tab if not already selected. "
        "Fill the price textbox with 90000. Fill the quantity textbox with 0.001. "
        "Click the 'Sell (short)' button. Click the confirm button in the order confirmation dialog. "
        "Then click the 'Open orders' tab to view the order list. "
        "Stop when the Open orders list shows a new open limit sell order for 0.001 BTC at 90000.",
    ),
    "market_sell": (
        "Place a market sell (short) order for 0.001 BTC. "
        "Click the 'Market' tab. Fill the quantity textbox with 0.001. "
        "Click the 'Sell (short)' button. Click the confirm button in the order confirmation dialog. "
        "Then click the 'Positions' tab to view positions. "
        "Stop when the Positions list shows a short position for 0.001 BTC.",
    ),
}


def counts(text):
    """Extract Open orders (N) and Positions (N) counts from page text."""
    out = {}
    for label, key in (("Open orders", "open_orders"), ("Positions", "positions")):
        m = re.search(rf"{label}\s*\((\d+)\)", text)
        out[key] = int(m.group(1)) if m else None
    return out


def verify(scenario, baseline, final_text):
    final = counts(final_text)
    checks = {"final_counts_seen": all(v is not None for v in final.values())}
    if scenario.startswith("limit"):
        price = "70000" if scenario == "limit_buy" else "90000"
        checks["open_orders_grew"] = (
            final["open_orders"] is not None
            and baseline["open_orders"] is not None
            and final["open_orders"] > baseline["open_orders"]
        )
        checks[f"price_{price}_visible"] = price in final_text or f"{price[:-3]},{price[-3:]}" in final_text
    else:
        checks["positions_visible"] = "Positions" in final_text
        checks["qty_0_001_visible"] = "0.001" in final_text
    return {"passed": all(checks.values()), "checks": checks, "baseline": baseline, "final": final}


def main():
    parser = argparse.ArgumentParser(description="Bitget demo trading regression")
    parser.add_argument("scenario", choices=SCENARIOS, help="order type to place")
    parser.add_argument("--output", default=None, help="artifact folder (default: artifacts/bitget/<scenario>)")
    parser.add_argument("--keep-open", action=argparse.BooleanOptionalAction, default=True,
                        help="leave the browser tab open after the run (default: open)")
    args = parser.parse_args()

    goal = SCENARIOS[args.scenario]
    folder = Path(args.output) if args.output else Path("artifacts") / "bitget" / args.scenario
    folder.mkdir(parents=True, exist_ok=True)

    agent = Agent(
        URL, goal,
        screenshots=True, record_dir=folder / "frames",
        background=False, settle_seconds=5,  # foreground tab + SPA settle for Bitget
    )
    try:
        baseline = counts(agent.state["page"]["text"])
        for state in agent.run():
            last = state["history"][-1] if state["history"] else {}
            print(state["elapsed_ms"], state["status"], last.get("action", ""), flush=True)
        print("=== run ended:", state["status"], "===")
    finally:
        state = agent.snapshot()
        final_page = agent.browser.observe(screenshot=False)
        state["verification"] = verify(args.scenario, baseline, final_page["text"])
        (folder / "state.json").write_text(json.dumps(state, indent=2, default=str))
        if not args.keep_open:
            agent.close()

    print(json.dumps(state["verification"], indent=2))
    print("history:")
    for h in state["history"]:
        print(" ", h.get("step"), h.get("kind"), repr(h.get("action"))[:60], "text=", repr(h.get("text")))
    if not state["verification"]["passed"]:
        raise SystemExit("Verification did not pass; inspect state.json and the open tab")


if __name__ == "__main__":
    main()
