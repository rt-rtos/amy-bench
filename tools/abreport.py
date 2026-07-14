#!/usr/bin/env python3
"""Render abcompare's compare.json as a Markdown table.

Usage:
    abreport.py compare.json                     # one run: the headline table
    abreport.py run*/compare.json                # several runs: one delta column each
    abreport.py compare.json -o RESULTS.md

abcompare.py prints a fixed-width dump with the numbers spread over three
blocks (deltas, headroom, tags). That is the right shape for reading a single
run at the terminal and the wrong shape for answering the two questions anyone
actually asks afterwards: *did this get faster*, and *is any scene about to run
out of block budget*. This folds compare.json back into one table per run, with
the delta next to the noise that qualifies it and the headroom transition next
to both, and counts how many scenes sit under the headroom floor.

Nothing is recomputed here - every number already exists in compare.json, and
the verdicts are abcompare's. This is a view, so it stays honest by construction
and can be re-run over archived runs long after the board is gone.

With more than one compare.json it emits the attribution matrix instead: scenes
down the side, runs across the top, each cell that run's delta. That is how you
read a stack of A/B runs that share a baseline and separate one change's effect
from another's.
"""

import argparse
import json
import os
import sys


# Verdicts abcompare hands back that mean "the delta cleared this scene's own
# measured noise", i.e. the ones worth setting in bold. Everything else
# ('within noise', 'small', '1 capture') is a number you should not act on.
DECISIVE = {"IMPROVEMENT", "REGRESSION"}


def load(path):
    with open(path) as f:
        rep = json.load(f)
    if "scenes" not in rep:
        sys.exit(f"{path}: not an abcompare report (no 'scenes' key)")
    rep["_path"] = path
    return rep


def label_for(rep):
    """Name a run by the directory holding its compare.json.

    abrun writes one outdir per run, so the parent directory is the only name a
    run ever gets ('run4', 'pie-simd'). Fall back to the file's own name when
    the report was not written into a directory of its own.
    """
    parent = os.path.basename(os.path.dirname(os.path.abspath(rep["_path"])))
    return parent or os.path.basename(rep["_path"])


def metric_keys(rep):
    m = rep.get("metric", "cyc")
    return f"a_median_{m}", f"b_median_{m}", f"delta_{m}_pct", m


def fmt_num(v):
    return "n/a" if v is None else f"{v:,.0f}"


def fmt_pct(v, bold=False):
    if v is None:
        return "n/a"
    s = f"{v:+.2f}%"
    return f"**{s}**" if bold else s


def fmt_noise(v):
    return "-" if v is None else f"±{v:.2f}%"


def fmt_headroom(sc):
    a, b = sc.get("a_headroom_pct"), sc.get("b_headroom_pct")
    if a is None or b is None:
        return "-"
    return f"{a:.1f}% -> {b:.1f}%"


def sort_scenes(scenes, how):
    """Default to tightest-first: the scene closest to blowing the block budget
    is the one whose delta you need to read, whatever the sign of the others."""
    names = list(scenes)
    if how == "name":
        return sorted(names)
    if how == "delta":
        return sorted(names, key=lambda s: scenes[s].get("delta_cyc_pct") or 0.0)
    if how == "cost":
        return sorted(names, key=lambda s: -(scenes[s].get("b_median_cyc") or 0))
    # headroom: least headroom first, scenes without a budget last
    return sorted(names, key=lambda s: (scenes[s].get("b_headroom_pct") is None,
                                        scenes[s].get("b_headroom_pct") or 0.0))


def context_line(rep):
    """One line of the run's config, from the firmware's own header - the
    numbers below are only comparable within it."""
    h = rep["a"]["header"]
    bits = [
        f"{h.get('cpu_mhz')} MHz",
        h.get("fp"),
        f"{h.get('sr')} Hz / block {h.get('block')}",
        f"{h.get('pacing')} pacing",
        f"{len(rep['a']['logs'])} captures/side",
        f"{h.get('passes')} passes",
    ]
    if h.get("profile"):
        bits.append("**profile build - absolute numbers are inflated**")
    line = " · ".join(str(b) for b in bits if b)
    if h.get("block_us"):
        line += f" · block budget {h['block_us']} us"
    return line


def warnings_for(rep):
    out = []
    if not rep.get("config_ok", True):
        out.append("**CONFIG MISMATCH between the two sides - this comparison "
                   "is not apples-to-apples.**")
    if not rep.get("deterministic", True):
        out.append("**A scene rendered differently across boots - the output "
                   "check below is meaningless until that is understood.**")
    changed = sorted(s for s, sc in rep["scenes"].items()
                     if sc.get("output") == "CHANGED")
    if changed:
        out.append(f"**Audio output CHANGED in: {', '.join(changed)}** - expected "
                   f"for a real DSP change, a bug otherwise.")
    return out


def headline(rep, sort, floor):
    a_key, b_key, d_key, metric = metric_keys(rep)
    scenes = rep["scenes"]
    names = sort_scenes(scenes, sort)
    show_output = any(sc.get("output") != "same" for sc in scenes.values())

    lines = []
    lines.append(f"## `{rep['a']['rev']}` -> `{rep['b']['rev']}`")
    lines.append("")
    lines.append(context_line(rep))
    lines.append("")
    for w in warnings_for(rep):
        lines.append(w)
        lines.append("")

    cols = ["Scene", f"A {metric}", f"B {metric}", "delta", "noise",
            "headroom", "verdict"]
    if show_output:
        cols.append("output")
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "---|" + "---:|" * 3 + "---:|" + "---|" * (
        3 if show_output else 2))

    for name in names:
        sc = scenes[name]
        row = [
            name,
            fmt_num(sc.get(a_key)),
            fmt_num(sc.get(b_key)),
            fmt_pct(sc.get(d_key), bold=sc.get("verdict") in DECISIVE),
            fmt_noise(sc.get("noise_pct")),
            fmt_headroom(sc),
            sc.get("verdict", "-"),
        ]
        if show_output:
            row.append(sc.get("output", "-"))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.extend(summary(rep, floor, d_key))
    return lines


def summary(rep, floor, d_key):
    """The two counts that decide what happens next: how the run landed, and how
    many scenes are close enough to the budget to care."""
    scenes = rep["scenes"]
    imp = [s for s, sc in scenes.items() if sc.get("verdict") == "IMPROVEMENT"]
    reg = [s for s, sc in scenes.items() if sc.get("verdict") == "REGRESSION"]
    flat = [s for s in scenes if s not in imp and s not in reg]

    out = [f"**{len(scenes)} scenes: {len(imp)} improved, {len(reg)} regressed, "
           f"{len(flat)} inconclusive** (within noise, below threshold, or "
           f"unjudgeable)."]
    if reg:
        worst = max(reg, key=lambda s: scenes[s].get(d_key) or 0)
        out.append("")
        out.append(f"Worst regression: `{worst}` at "
                   f"{fmt_pct(scenes[worst].get(d_key))}.")

    tight = [(sc["b_headroom_pct"], s) for s, sc in scenes.items()
             if sc.get("b_headroom_pct") is not None
             and sc["b_headroom_pct"] < floor]
    if tight:
        tight.sort()
        listed = ", ".join(f"`{s}` ({h:.1f}%)" for h, s in tight)
        out.append("")
        out.append(f"**{len(tight)} scene(s) under {floor:.0f}% headroom** "
                   f"(head side): {listed}. Nothing here is spare capacity - a "
                   f"regression in one of these overruns the block.")
    elif any(sc.get("b_headroom_pct") is not None for sc in scenes.values()):
        out.append("")
        out.append(f"No scene is under {floor:.0f}% headroom on the head side.")
    return out


def matrix(reps, sort, labels):
    """Attribution: one delta column per run, scenes down the side.

    Only meaningful when the runs share a scene set - which they do whenever
    they came off the same bench - so the column-to-column comparison is the
    point: run1's delta plus run2's should compose to run3's if the two changes
    are independent, and a column that wins nothing anywhere is a change that
    is not worth shipping.
    """
    a_key, _, d_key, metric = metric_keys(reps[0])
    names = sort_scenes(reps[0]["scenes"], sort)
    for rep in reps[1:]:
        for s in sort_scenes(rep["scenes"], sort):
            if s not in names:
                names.append(s)

    lines = ["## Attribution", ""]
    lines.append("| Run | A (base) | B (head) | metric |")
    lines.append("|---|---|---|---|")
    for rep, lab in zip(reps, labels):
        lines.append(f"| {lab} | `{rep['a']['rev']}` | `{rep['b']['rev']}` | "
                     f"{rep.get('metric', 'cyc')} |")
    lines.append("")

    cols = ["Scene"] + list(labels)
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|---|" + "---:|" * len(labels))
    for name in names:
        row = [name]
        for rep in reps:
            sc = rep["scenes"].get(name)
            if sc is None:
                row.append("-")
                continue
            row.append(fmt_pct(sc.get(d_key),
                               bold=sc.get("verdict") in DECISIVE))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append(f"Negative = faster. Bold = the delta cleared that scene's own "
                 f"measured noise in that run; everything else is a number to "
                 f"look at, not to act on. Metric: {metric}.")
    lines.append("")

    warned = False
    for rep, lab in zip(reps, labels):
        for w in warnings_for(rep):
            lines.append(f"- {lab}: {w}")
            warned = True
    if warned:
        lines.append("")
    return lines


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reports", nargs="+", metavar="compare.json")
    ap.add_argument("-o", "--out", metavar="PATH",
                    help="write Markdown here instead of stdout")
    ap.add_argument("--sort", choices=["headroom", "delta", "cost", "name"],
                    default="headroom",
                    help="row order (default: headroom - tightest scene first)")
    ap.add_argument("--headroom-floor", type=float, default=20.0,
                    help="count scenes with less than this %% of the block "
                         "budget left (default: 20)")
    ap.add_argument("--labels", metavar="L", nargs="+",
                    help="column names for the multi-run table (default: each "
                         "compare.json's parent directory)")
    args = ap.parse_args()

    reps = [load(p) for p in args.reports]

    if len(reps) == 1:
        lines = headline(reps[0], args.sort, args.headroom_floor)
    else:
        labels = args.labels or [label_for(r) for r in reps]
        if len(labels) != len(reps):
            ap.error(f"--labels: got {len(labels)} for {len(reps)} reports")
        lines = matrix(reps, args.sort, labels)

    text = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
