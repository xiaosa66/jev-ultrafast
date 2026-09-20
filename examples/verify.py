"""Multi-point requirement verification on top of jev-ultrafast.

One requirement point = one narrow-goal Agent run + one deterministic assertion.
This file owns everything Jev must not decide: ordering, prechecks, reset between
points, retries, pass/fail, and reporting.

    uv run --env-file .env python examples/verify.py --plan examples/plan.example.json
    uv run --env-file .env python examples/verify.py --plan plan.json --only limit_buy
    uv run --env-file .env python examples/verify.py --plan plan.json --dry-run

Plan format (JSON, stdlib only -- no extra dependency):

    {
      "url": "https://www.bitget.com/trade-swap/usdc/BTCPERP",
      "reset": true,                 # navigate back to "url" before each point
      "settle_seconds": 5,           # SPA render wait
      "max_steps": 20,               # hard cap on agent actions per point
      "keep_open": true,             # never close the tab (default true)
      "artifacts": "artifacts/verify",
      "points": [
        {
          "id": "limit_buy",
          "goal": "Place a limit buy (long) order for 0.001 BTC at 70000, post only.",
          "precheck": {"text_contains": ["Open orders"]},
          "assert": {"count_grew": "open_orders"},
          "info": {"text_contains": ["70000"]}
        }
      ]
    }

Assertions are deterministic: counters, URL substrings, or a JS expression
evaluated in the page. `info` is recorded but never decides pass/fail -- panel
text renders asynchronously, so text matches are evidence, not proof.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from jev_ultrafast import Agent, Browser

COUNT_LABELS = {"open_orders": "Open orders", "positions": "Positions"}


def counts(text):
    """Read 'Open orders (3)' style counters out of panel text."""
    found = {}
    for key, label in COUNT_LABELS.items():
        match = re.search(rf"{label}\s*\((\d+)\)", text or "")
        found[key] = int(match.group(1)) if match else None
    return found


def snapshot(browser):
    page = browser.observe(screenshot=False)
    return page.get("text") or "", page.get("url") or ""


def matches(spec, text, url):
    """All listed conditions must hold. Empty spec -> trivially true."""
    if not spec:
        return True
    for needle in spec.get("text_contains", []):
        if needle not in text:
            return False
    for needle in spec.get("url_contains", []):
        if needle not in url:
            return False
    return True


def evaluate_assertion(point, text, url, baseline, browser):
    """Return (checks, info). Every entry in checks must be True to pass."""
    spec = point.get("assert") or {}
    checks = {}
    now = counts(text)
    baseline = baseline or {}

    for key, direction in (("count_grew", 1), ("count_shrank", -1)):
        name = spec.get(key)
        if not name:
            continue
        before, after = baseline.get(name), now.get(name)
        checks[key] = before is not None and after is not None and (after - before) * direction > 0

    for needle in spec.get("url_contains", []):
        checks[f"url_contains:{needle}"] = needle in url

    expression = spec.get("js")
    if expression:
        try:
            checks["js"] = bool(browser.evaluate(expression))
        except Exception as exc:  # a broken assertion must fail the point, not the run
            checks["js"] = False
            print(f"  ! js assertion errored: {exc}", file=sys.stderr)

    info_spec = point.get("info") or {}
    info = {}
    for needle in info_spec.get("text_contains", []):
        info[f"text:{needle}"] = needle in text
    info["counts"] = now
    return checks, info


def reset_tab(plan):
    """Return the tab to a known state. Never closes it (reuse=True -> owned=False)."""
    if not plan.get("reset", True):
        return
    browser = Browser(
        plan["url"],
        background=plan.get("background", False),
        reuse=True,
        settle_seconds=plan.get("settle_seconds", 0),
    )
    browser.close()


def run_point(plan, point, artifacts):
    """One point: reset -> agent run -> deterministic assertion. At most 2 attempts."""
    url = point.get("url") or plan["url"]
    folder = artifacts / point["id"]
    folder.mkdir(parents=True, exist_ok=True)

    reset_tab(plan)
    baseline_text, baseline_url = snapshot(Browser(url, background=False, reuse=True))
    if not matches(point.get("precheck"), baseline_text, baseline_url):
        return {"id": point["id"], "result": "skipped", "reason": "precheck failed", "attempts": 0}
    baseline = counts(baseline_text)

    attempts = plan.get("retries", 1) + 1
    for attempt in range(1, attempts + 1):
        agent = Agent(
            url,
            point["goal"],
            background=plan.get("background", False),
            settle_seconds=plan.get("settle_seconds", 5),
            reuse=True,
            screenshots=True,
            record_dir=folder / f"try{attempt}",
        )
        steps = 0
        cap = point.get("max_steps") or plan.get("max_steps", 20)
        try:
            for state in agent.run():
                steps = len(state["history"])
                if steps >= cap:
                    break
            text, final_url = snapshot(agent.browser)
        finally:
            if not plan.get("keep_open", True):
                agent.close()

        checks, info = evaluate_assertion(point, text, final_url, baseline, agent.browser)
        passed = all(checks.values())
        result = {
            "id": point["id"],
            "result": "pass" if passed else "fail",
            "attempts": attempt,
            "steps": steps,
            "status": state.get("status"),
            "checks": checks,
            "info": info,
            "url": final_url,
            "frames": str(folder / f"try{attempt}"),
        }
        if passed:
            return result
    result["result"] = "blocked"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", required=True, help="JSON plan file")
    parser.add_argument("--only", action="append", help="Run only these point ids (repeatable)")
    parser.add_argument("--output", default=None, help="Report path (default: <artifacts>/report.json)")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan, touch nothing")
    args = parser.parse_args()

    plan = json.loads(Path(args.plan).read_text())
    points = plan["points"]
    if args.only:
        points = [p for p in points if p["id"] in args.only]

    if args.dry_run:
        print(f"url          : {plan['url']}")
        print(f"settle/retry : {plan.get('settle_seconds', 5)}s / {plan.get('retries', 1)}")
        print(f"points ({len(points)}):")
        for p in points:
            print(f"  - {p['id']:<16} assert={json.dumps(p.get('assert') or {}, ensure_ascii=False)}")
            print(f"    goal: {p['goal'][:110]}")
        return

    artifacts = Path(args.output).parent if args.output else Path(plan.get("artifacts", "artifacts/verify"))
    artifacts.mkdir(parents=True, exist_ok=True)
    results = [run_point(plan, p, artifacts) for p in points]

    width = max((len(str(r["id"])) for r in results), default=10)
    print(f"\n{'POINT'.ljust(width)}  RESULT    TRY  STEPS  EVIDENCE")
    for r in results:
        evidence = ", ".join(f"{k}={'ok' if v else 'NO'}" for k, v in (r.get("checks") or {}).items()) or r.get(
            "reason", ""
        )
        print(
            f"{str(r['id']).ljust(width)}  {r['result']:<9} {r.get('attempts', 0):>3}  "
            f"{r.get('steps', 0):>5}  {evidence[:60]}"
        )

    report = Path(args.output) if args.output else artifacts / "report.json"
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print(f"\nreport -> {report}")
    failed = [r["id"] for r in results if r["result"] != "pass"]
    print(
        f"{len(results) - len(failed)}/{len(results)} passed"
        + (f"  | needs review: {', '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
