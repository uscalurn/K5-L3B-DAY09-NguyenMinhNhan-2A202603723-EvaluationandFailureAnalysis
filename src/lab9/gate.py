"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item
from lab9 import evalset
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems = []

    def finite(value):
        return isinstance(value, (int, float)) and math.isfinite(value)

    try:
        metric_defs = spec["metrics"]
        mids = [m["id"] for m in metric_defs]
        if not mids or len(mids) != len(set(mids)):
            problems.append("spec metrics are empty or duplicated")
        slices = spec.get("slices", [])
        slice_ids = [(s["name"], s["metric"]) for s in slices]
        if len(slice_ids) != len(set(slice_ids)):
            problems.append("duplicate slice definitions")
        for m in metric_defs:
            th = m.get("threshold", {})
            mid = m["id"]
            derived = mid in {"pass_5", "cost_per_success"} or any(mid == f"{name}_{k}" for k in spec.get("pass_k", []) for name in ("pass_at", "pass_hat"))
            if mid not in GRADERS and not derived:
                problems.append(f"metric {mid} has no registered grader or aggregation")
            if m.get("kind") == "safety" and (m.get("blocking") is not True or th.get("max") != 0):
                problems.append(f"safety metric {m['id']} must block at max=0")
            for key, value in th.items():
                if key in {"min", "max", "max_increase"} and (not finite(value) or isinstance(value, bool)):
                    problems.append(f"invalid threshold {m['id']}/{key}")
        for s in slices:
            if s["metric"] not in mids:
                problems.append(f"slice {s['name']} references an unspecified metric")
            for key in ("min", "max"):
                if key in s and not finite(s[key]):
                    problems.append(f"invalid slice threshold {s['name']}/{key}")
        bs = spec.get("bootstrap", {})
        if bs.get("method", "percentile") != "percentile" or bs.get("confidence", 0.95) != 0.95:
            problems.append("bootstrap must use the specified 95% percentile CI")
        if type(bs.get("n_resamples", 1000)) is not int or bs.get("n_resamples", 1000) < 1:
            problems.append("invalid bootstrap resample count")
        if type(bs.get("rng", 2027)) is not int:
            problems.append("invalid bootstrap RNG seed")

        # Optional local sources strengthen the report-only grid check. Minimal
        # in-memory reports can still be checked without filesystem fixtures.
        source_items = None
        if "eval_set" in spec:
            path = Path(spec["eval_set"])
            if not path.is_file():
                problems.append("configured eval set is missing")
            else:
                source_items = evalset.load_jsonl(path)
                if any(i.get("eval_set_version") != spec["eval_set_version"] for i in source_items):
                    problems.append("eval-set version differs from the spec")
                for tag, report in (("PR", pr), ("baseline", base)):
                    if report.get("manifest", {}).get("eval_set_sha") != evalset.sha12(path):
                        problems.append(f"{tag}: eval_set_sha differs from configured data")

        grids = []
        for tag, report in (("PR", pr), ("baseline", base)):
            manifest, rows, metrics = report["manifest"], report["rows"], report["metrics"]
            failures = report["evaluator_failures"]
            if not isinstance(failures, list) or failures:
                problems.append(f"{tag}: evaluator_failures is nonempty or invalid")
            count, seeds = manifest["items"], manifest["seeds"]
            if type(count) is not int or count < 1:
                problems.append(f"{tag}: invalid manifest item count")
            if not isinstance(seeds, list) or not seeds or any(type(s) is not int for s in seeds) or len(seeds) != len(set(seeds)):
                problems.append(f"{tag}: missing, duplicated or invalid manifest seeds")
            if "seeds" in spec and set(seeds) != set(spec["seeds"]):
                problems.append(f"{tag}: seeds differ from spec")
            if not isinstance(manifest.get("eval_set_sha"), str) or not manifest["eval_set_sha"]:
                problems.append(f"{tag}: missing eval_set_sha")
            if "eval_set" in spec:
                for key in ("eval_set", "metric_spec_version", "llm", "python"):
                    if not isinstance(manifest.get(key), str) or not manifest[key]:
                        problems.append(f"{tag}: missing manifest {key}")
                if manifest.get("eval_set") != spec["eval_set"]:
                    problems.append(f"{tag}: eval_set path differs from spec")
                if not report.get("run_id") or not report.get("shape"):
                    problems.append(f"{tag}: missing run metadata")
                if tag == "PR" and manifest.get("metric_spec_version") != spec.get("metric_spec_version"):
                    problems.append("PR: metric_spec_version differs from spec")
            if not isinstance(rows, list) or len(rows) != count * len(seeds):
                problems.append(f"{tag}: rows != items x seeds")
            pairs = [(r["item_id"], r["seed"]) for r in rows]
            item_ids = {i for i, _ in pairs}
            if any(not isinstance(i, str) or not i or type(s) is not int for i, s in pairs):
                problems.append(f"{tag}: invalid item/seed key")
            want = {(i, s) for i in item_ids for s in seeds}
            if len(item_ids) != count or len(set(pairs)) != len(pairs) or set(pairs) != want:
                problems.append(f"{tag}: incomplete or duplicated item/seed grid")
            if source_items is not None and item_ids != {i["item_id"] for i in source_items}:
                problems.append(f"{tag}: item identities differ from configured eval set")
            grid = {}
            tags_by_item = {}
            for r in rows:
                tags = r.get("slices", [])
                if not isinstance(tags, list) or any(not isinstance(s, str) for s in tags) or len(tags) != len(set(tags)):
                    problems.append(f"{tag}: invalid slice tags")
                    continue
                tags = frozenset(tags)
                if r["item_id"] in tags_by_item and tags_by_item[r["item_id"]] != tags:
                    problems.append(f"{tag}: slice tags vary across seeds")
                tags_by_item[r["item_id"]] = tags
                grid[(r["item_id"], r["seed"])] = tags
            grids.append(grid)
            if source_items is not None and any(tags_by_item.get(i["item_id"]) != frozenset(i.get("slices", [])) for i in source_items):
                problems.append(f"{tag}: slices differ from configured eval set")
            for mid, value in metrics.items():
                if not finite(value):
                    problems.append(f"{tag}: {mid} is missing/nonfinite")
            for mid in mids:
                if mid not in metrics:
                    problems.append(f"{tag}: missing aggregate {mid}")
            if tag == "PR" and any(mid in GRADERS and mid not in mids for mid in base["metrics"]):
                problems.append("spec removed a baseline graded metric")

            # Check raw evidence and recompute aggregates, including reliability.
            # This catches a green aggregate even when a grader failed on a row.
            graded = {mid for mid in mids if mid in GRADERS or any(mid in r for r in rows)}
            graded |= {s["metric"] for s in slices}
            graded |= {mid for mid in GRADERS if mid in metrics}
            for mid in graded:
                values = [r.get(mid) for r in rows]
                if any(not finite(v) or v not in (0, 1) for v in values):
                    problems.append(f"{tag}: missing/nonbinary graded value for {mid}")
                    continue
                definition = next((m for m in metric_defs if m["id"] == mid), {})
                total = sum(values)
                expected = total if definition.get("kind") == "safety" or mid in {"approval_bypass", "secret_leak"} else round(total / len(values), 4)
                if not finite(metrics.get(mid)) or not math.isclose(metrics[mid], expected, abs_tol=1e-9):
                    problems.append(f"{tag}: {mid} aggregate disagrees with rows")
            if rows and all(finite(r.get("pass_rate")) and r["pass_rate"] in (0, 1) for r in rows):
                groups = {i: [r["pass_rate"] for r in rows if r["item_id"] == i] for i in item_ids}
                for mid in metrics:
                    estimate = None
                    if mid == "pass_5":
                        estimate = sum(all(v) for v in groups.values()) / len(groups)
                    elif mid.startswith(("pass_at_", "pass_hat_")):
                        k = int(mid.rsplit("_", 1)[1])
                        fn = stats.pass_at_k if mid.startswith("pass_at_") else stats.pass_hat_k
                        estimate = sum(fn(len(v), int(sum(v)), k) for v in groups.values()) / len(groups)
                    if estimate is not None and (not finite(metrics[mid]) or not math.isclose(metrics[mid], round(estimate, 4), abs_tol=1e-9)):
                        problems.append(f"{tag}: {mid} aggregate disagrees with rows")
                if "cost_per_success" in metrics:
                    costs = [r.get("cost") for r in rows]
                    if any(not finite(v) or v < 0 for v in costs):
                        problems.append(f"{tag}: invalid/missing cost observations")
                    else:
                        successes = sum(r["pass_rate"] for r in rows)
                        expected = round(sum(costs) / successes, 4) if successes else None
                        if expected is None or not finite(metrics["cost_per_success"]) or not math.isclose(metrics["cost_per_success"], expected, abs_tol=1e-9):
                            problems.append(f"{tag}: cost_per_success aggregate disagrees with rows")
        if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
            problems.append("eval_set_sha differs between reports")
        if pr["manifest"]["items"] != base["manifest"]["items"] or set(pr["manifest"]["seeds"]) != set(base["manifest"]["seeds"]):
            problems.append("PR and baseline manifests use different grids")
        if grids[0] != grids[1]:
            problems.append("PR item/seed identities or slice tags differ from baseline")

        if base_spec is not None and base_spec.get("eval_set_version") == spec.get("eval_set_version"):
            def ratchet(old, new, identity, threshold_key=None):
                for key in ("blocking", "no_significant_drop"):
                    if old.get(key) and new.get(key) is not True:
                        problems.append(f"ratchet {identity}: removed {key}")
                before = old.get(threshold_key, {}) if threshold_key else old
                after = new.get(threshold_key, {}) if threshold_key else new
                for key in ("min", "max", "max_increase", "no_significant_drop"):
                    if key not in before:
                        continue
                    if key not in after or (key == "min" and after[key] < before[key]) or (key in {"max", "max_increase"} and after[key] > before[key]) or (key == "no_significant_drop" and before[key] and after[key] is not True):
                        problems.append(f"ratchet {identity}: loosened {key}")
            current = {m["id"]: m for m in metric_defs}
            for old in base_spec["metrics"]:
                mid = old["id"]
                if mid not in current:
                    problems.append(f"ratchet: removed metric {mid}")
                else:
                    ratchet(old, current[mid], mid, "threshold")
                    for key in ("kind", "grader", "level"):
                        if key in old and current[mid].get(key) != old[key]:
                            problems.append(f"ratchet {mid}: changed {key}")
            current_slices = {(s["name"], s["metric"]): s for s in slices}
            for old in base_spec.get("slices", []):
                sid = (old["name"], old["metric"])
                if sid not in current_slices:
                    problems.append(f"ratchet: removed slice {sid}")
                else:
                    ratchet(old, current_slices[sid], str(sid))
            if "seeds" in base_spec and set(spec.get("seeds", [])) != set(base_spec["seeds"]):
                problems.append("ratchet: changed seed set")
            old_bs = base_spec.get("bootstrap", {})
            if bs.get("n_resamples", 1000) < old_bs.get("n_resamples", 1000) or bs.get("rng", 2027) != old_bs.get("rng", 2027):
                problems.append("ratchet: weakened bootstrap or changed resampling RNG")
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        problems.append(f"malformed evaluation evidence: {type(exc).__name__}: {exc}")
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for entry in spec.get("slices", []):
        name, mid = entry["name"], entry["metric"]
        sid = f"slice:{name}/{mid}"
        a, b = per_item(pr, mid, name), per_item(base, mid, name)
        paired = bool(a) and set(a) == set(b)
        relevant = [r for report in (pr, base) for r in report["rows"] if name in (r.get("slices") or [])]
        paired = paired and all(isinstance(r.get(mid), (int, float)) and math.isfinite(r[mid]) for r in relevant)
        value = sum(a.values()) / len(a) if paired else None
        fail = not paired
        note = "missing/unpaired slice observations" if fail else ""
        if value is not None:
            fail = ("min" in entry and value < entry["min"]) or ("max" in entry and value > entry["max"])
            if entry.get("no_significant_drop"):
                keys = sorted(a)
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                  n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                                  confidence=bs.get("confidence", 0.95))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                fail = fail or hi < 0
        if fail and (entry.get("blocking") or not paired):
            blocked.append(sid)
        verdict = "BLOCK" if sid in blocked else ("warn" if fail else "ok")
        lines.append(f"  {sid:<42} {value!s:<8} {note:<28} {verdict}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    # Pair the full item/seed grid before reducing to per-item means.
    keys_pr = [(r["item_id"], r["seed"]) for r in pr["rows"]]
    keys_base = [(r["item_id"], r["seed"]) for r in base["rows"]]
    if set(keys_pr) != set(keys_base) or len(set(keys_pr)) != len(keys_pr) or len(set(keys_base)) != len(keys_base):
        return ["integrity"], ["  INTEGRITY unpaired or duplicate item/seed rows"]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if not isinstance(val, (int, float)) or not math.isfinite(val):
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid) is not None:
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and verdict != "missing":
            a, b = per_item(pr, mid), per_item(base, mid)
            if not a or set(a) != set(b) or any(r.get(mid) is None for r in pr["rows"] + base["rows"]):
                return ["integrity"], [f"  INTEGRITY {mid}: missing paired observations"]
            keys = sorted(a)
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027),
                                               confidence=bs.get("confidence", 0.95))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and (m.get("blocking") or m.get("kind") == "safety" or verdict == "missing"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr.get('run_id', '?')} vs main {base.get('run_id', '?')} · eval set {pr.get('manifest', {}).get('eval_set_sha', '?')}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
