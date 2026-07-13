#!/usr/bin/env python3
"""Compare two amy-bench serial captures (run A vs run B).

Usage:
    abcompare.py runA.log runB.log [--threshold 3.0]

Feed it raw `idf.py monitor | tee run.log` captures; anything that is not a
bench JSON record is ignored (records may span multiple lines - see
tools/schema.md). Reports per-scene medians (across passes) for
wall-time and cycle counts, flags deltas above the noise threshold, verifies
per-scene CRC32s (within-run determinism and cross-run output equality), and
diffs per-tag profiler totals when both runs are profile builds.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict


def iter_bench_objects(f):
    """Yield decoded JSON objects from a capture where each bench record may
    span multiple lines (the firmware puts a newline after every top-level
    comma for readability). Non-JSON lines (monitor/log noise) in between
    are ignored; a record is complete once its braces balance to zero."""
    buf = []
    depth = 0
    for line in f:
        stripped = line.strip()
        if not buf:
            if not stripped.startswith("{"):
                continue
            depth = 0
        buf.append(stripped)
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0:
            text = "".join(buf)
            buf = []
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                continue


def parse_run(path):
    header = None
    summaries = defaultdict(list)   # scene -> [summary dict per pass]
    tags = defaultdict(list)        # (scene, tag) -> [us_total per pass]
    with open(path, "r", errors="replace") as f:
        for obj in iter_bench_objects(f):
            if obj.get("schema") != 1:
                continue
            if "run" in obj:
                header = obj
            elif obj.get("summary"):
                summaries[obj["scene"]].append(obj)
            elif "tag" in obj:
                tags[(obj["scene"], obj["tag"])].append(obj["us_total"])
    if header is None:
        sys.exit(f"{path}: no bench run header found - is this a bench capture?")
    if not summaries:
        sys.exit(f"{path}: no scene summaries found (incomplete capture?)")
    return {"header": header, "summaries": summaries, "tags": tags}


def median_of(summaries, field):
    return statistics.median(s[field] for s in summaries)


def check_headers(a, b):
    keys = ["sr", "block", "pacing", "fp", "profile", "cpu_mhz"]
    mismatches = [
        (k, a.get(k), b.get(k)) for k in keys if a.get(k) != b.get(k)
    ]
    if mismatches:
        print("!! RUN CONFIG MISMATCH - comparison is not apples-to-apples:")
        for k, va, vb in mismatches:
            print(f"!!   {k}: A={va}  B={vb}")
        print()


def check_crcs(run, label):
    """Within one run, every pass of a scene must produce the same CRC."""
    ok = True
    for scene, summaries in sorted(run["summaries"].items()):
        crcs = {s["crc32"] for s in summaries}
        if len(crcs) > 1:
            print(f"!! {label}: scene '{scene}' is NON-DETERMINISTIC across "
                  f"passes: {sorted(crcs)}")
            ok = False
    return ok


def fmt_delta(a_val, b_val, threshold):
    if a_val == 0:
        return "n/a", False
    pct = 100.0 * (b_val - a_val) / a_val
    flagged = abs(pct) >= threshold
    mark = " *" if flagged else ""
    return f"{pct:+7.2f}%{mark}", flagged


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--threshold", type=float, default=3.0,
                    help="flag |delta%%| at or above this (default 3.0)")
    args = ap.parse_args()

    a = parse_run(args.run_a)
    b = parse_run(args.run_b)
    ha, hb = a["header"], b["header"]

    print(f"A: {args.run_a}  rev={ha.get('rev')}  fp={ha.get('fp')}  "
          f"pacing={ha.get('pacing')}  profile={ha.get('profile')}")
    print(f"B: {args.run_b}  rev={hb.get('rev')}  fp={hb.get('fp')}  "
          f"pacing={hb.get('pacing')}  profile={hb.get('profile')}")
    print()

    check_headers(ha, hb)
    det_ok = check_crcs(a, "A") & check_crcs(b, "B")
    if not det_ok:
        print()

    scenes = sorted(set(a["summaries"]) | set(b["summaries"]))
    block_us = ha.get("block_us")

    hdr = (f"{'scene':<12} {'A med_us':>9} {'B med_us':>9} {'d_us':>13} "
           f"{'A med_cyc':>10} {'B med_cyc':>10} {'d_cyc':>13} {'output':>9}")
    print(hdr)
    print("-" * len(hdr))

    for scene in scenes:
        sa = a["summaries"].get(scene)
        sb = b["summaries"].get(scene)
        if not sa or not sb:
            missing = "A" if not sa else "B"
            print(f"{scene:<12} -- missing in run {missing} --")
            continue

        a_us = median_of(sa, "median_us")
        b_us = median_of(sb, "median_us")
        a_cy = median_of(sa, "median_cyc")
        b_cy = median_of(sb, "median_cyc")
        d_us, _ = fmt_delta(a_us, b_us, args.threshold)
        d_cy, _ = fmt_delta(a_cy, b_cy, args.threshold)

        crc_a = {s["crc32"] for s in sa}
        crc_b = {s["crc32"] for s in sb}
        output = "same" if crc_a == crc_b else "CHANGED"

        print(f"{scene:<12} {a_us:>9.0f} {b_us:>9.0f} {d_us:>13} "
              f"{a_cy:>10.0f} {b_cy:>10.0f} {d_cy:>13} {output:>9}")

    if block_us:
        print()
        print(f"headroom vs {block_us} us block budget "
              f"(100% = block costs nothing):")
        for scene in scenes:
            sa = a["summaries"].get(scene)
            sb = b["summaries"].get(scene)
            if not sa or not sb:
                continue
            ha_pct = 100.0 * (1 - median_of(sa, "median_us") / block_us)
            hb_pct = 100.0 * (1 - median_of(sb, "median_us") / block_us)
            print(f"  {scene:<12} A {ha_pct:6.1f}%   B {hb_pct:6.1f}%")

    overruns_a = sum(s.get("overruns", 0) for ss in a["summaries"].values() for s in ss)
    overruns_b = sum(s.get("overruns", 0) for ss in b["summaries"].values() for s in ss)
    if overruns_a or overruns_b:
        print(f"\noverruns (paced mode): A={overruns_a}  B={overruns_b}")

    shared_tags = sorted(set(a["tags"]) & set(b["tags"]))
    if shared_tags:
        print()
        thdr = (f"{'scene':<12} {'tag':<38} {'A us_total':>11} "
                f"{'B us_total':>11} {'delta':>13}")
        print(thdr)
        print("-" * len(thdr))
        rows = []
        for key in shared_tags:
            ta = statistics.median(a["tags"][key])
            tb = statistics.median(b["tags"][key])
            d, flagged = fmt_delta(ta, tb, args.threshold)
            rows.append((flagged, abs(tb - ta), key, ta, tb, d))
        rows.sort(key=lambda r: (not r[0], -r[1]))
        for _, _, (scene, tag), ta, tb, d in rows:
            print(f"{scene:<12} {tag:<38} {ta:>11.0f} {tb:>11.0f} {d:>13}")

    print("\n* = |delta| >= {:.1f}% threshold".format(args.threshold))
    if any(
        {s['crc32'] for s in a['summaries'][sc]} != {s['crc32'] for s in b['summaries'][sc]}
        for sc in scenes if sc in a['summaries'] and sc in b['summaries']
    ):
        print("!! OUTPUT CHANGED in at least one scene: expected for a real "
              "DSP change, a bug otherwise. Do not accept 'faster' without "
              "deciding which this is.")


if __name__ == "__main__":
    main()
