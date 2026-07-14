#!/usr/bin/env python3
"""Compare amy-bench serial captures (side A vs side B).

Usage:
    abcompare.py runA.log runB.log [--threshold 3.0]
    abcompare.py -A a1.log a2.log -B b1.log b2.log [--json out.json]

Feed it raw captures (from tools/capture.py, or `idf.py monitor | tee run.log`);
anything that is not a bench JSON record is ignored (records may span multiple
lines - see tools/schema.md).

Each side may be several captures of the *same* firmware - abrun.py flashes both
sides into two OTA slots and alternates A B A B, so repeats cost a reboot rather
than a reflash. Repeats are what make a delta readable: this reports each scene's
run-to-run **noise** (the spread of that side's own repeated measurements)
alongside the A-vs-B delta, and refuses to call a delta real unless it clears
that noise. With a single capture per side there is no noise estimate and the
verdict column says so.

Also verifies per-scene CRC32s (determinism within a side, and output equality
across sides) and diffs per-tag profiler totals when both sides are profile
builds.
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


def parse_capture(path):
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
    return {"path": path, "header": header, "summaries": summaries, "tags": tags}


def parse_side(paths, label):
    """One side = one or more captures of the same firmware."""
    caps = [parse_capture(p) for p in paths]
    base = caps[0]["header"]
    for c in caps[1:]:
        if c["header"].get("rev") != base.get("rev"):
            print(f"!! {label}: captures are from different revs "
                  f"({base.get('rev')} vs {c['header'].get('rev')}) - these are "
                  f"not repeats of one firmware")
    return {"label": label, "captures": caps, "header": base}


def samples(side, scene, field):
    """Every individual pass measurement for a scene, pooled across captures."""
    out = []
    for cap in side["captures"]:
        for s in cap["summaries"].get(scene, []):
            out.append(s[field])
    return out


def capture_medians(side, scene, field):
    """One number per capture: the median across that boot's passes.

    This, not the pooled per-pass list, is the unit of measurement. Passes
    within a boot are not independent samples of the same quantity - some
    scenes cost systematically more on a given pass (saw_lpf6's pass 1 runs
    ~4.6% hot, identically on every boot and on both sides of an A/B). That is
    a deterministic property of the scene, so it cancels between A and B, and
    folding it into the noise estimate would inflate the noise ~500x and hide
    every real regression in that scene behind it.

    Taking the median within a boot absorbs that fixed effect; the spread that
    remains *between* boots is the actual measurement error.
    """
    out = []
    for cap in side["captures"]:
        vals = [s[field] for s in cap["summaries"].get(scene, [])]
        if vals:
            out.append(statistics.median(vals))
    return out


def value_of(side, scene, field):
    meds = capture_medians(side, scene, field)
    return statistics.median(meds) if meds else None


def noise_of(side, scene, field):
    """Boot-to-boot spread as a percentage of the median, or None if 1 capture.

    Full range (max-min), not a standard deviation: for a gate you care about
    the worst excursion you have actually seen, not the typical one.
    """
    meds = capture_medians(side, scene, field)
    if len(meds) < 2:
        return None
    med = statistics.median(meds)
    if not med:
        return None
    return 100.0 * (max(meds) - min(meds)) / med


# Scenes whose audio legitimately differs from pass to pass, with the reason.
# AMY has no way to reset effects state: reverb's ten delay lines and its four
# IIR filter states (reverb_params_t, src/amy.h) are zeroed once at allocation
# (bzero in new_reverb(), the clearing loop in new_delay_line()) and never
# again. A scene's teardown can silence an effect but not drain it - the tail
# freezes in the buffers - so each pass starts the reverb from the previous
# pass's leftovers and renders different audio. See "Known AMY gap" in
# README.md. This is not measurement error: each pass is bit-identical
# across boots, so the audio is still fully comparable A-vs-B pass by pass.
PASS_VARYING_SCENES = {
    "fx_sine8": "reverb/chorus state carries between passes (no FX reset in AMY)",
}


def crcs_by_pass(side, scene):
    """pass index -> set of CRCs seen for it, across this side's captures.

    Keyed by pass, because a scene's passes are not interchangeable: with FX
    state carrying over, pass 1 legitimately renders different audio than pass
    0. What must hold is that a *given* pass is reproducible across boots.
    """
    out = defaultdict(set)
    for cap in side["captures"]:
        for s in cap["summaries"].get(scene, []):
            out[s["pass"]].add(s["crc32"])
    return out


def scene_crcs(side, scene):
    return {s["crc32"] for cap in side["captures"]
            for s in cap["summaries"].get(scene, [])}


def all_scenes(side):
    out = set()
    for cap in side["captures"]:
        out |= set(cap["summaries"])
    return out


def check_headers(a, b):
    keys = ["sr", "block", "pacing", "fp", "profile", "cpu_mhz"]
    ha, hb = a["header"], b["header"]
    mismatches = [(k, ha.get(k), hb.get(k)) for k in keys if ha.get(k) != hb.get(k)]
    if mismatches:
        print("!! RUN CONFIG MISMATCH - comparison is not apples-to-apples:")
        for k, va, vb in mismatches:
            print(f"!!   {k}: A={va}  B={vb}")
        print()
    return not mismatches


def check_crcs(side):
    """A given pass of a scene must render bit-identically on every boot.

    Note what this does NOT require: that all *passes* agree with each other.
    Some scenes carry state between passes (see PASS_VARYING_SCENES) and so
    render different-but-reproducible audio each pass. Demanding pass-to-pass
    equality would condemn those scenes as broken when they are merely stateful,
    and the audio stays perfectly comparable A-vs-B as long as we line the
    passes up. Real nondeterminism is a *boot*-to-boot disagreement, and needs
    at least two captures to see at all.
    """
    ok = True
    for scene in sorted(all_scenes(side)):
        by_pass = crcs_by_pass(side, scene)
        flapping = {p: sorted(c) for p, c in by_pass.items() if len(c) > 1}
        if flapping:
            print(f"!! {side['label']}: scene '{scene}' is NON-DETERMINISTIC - "
                  f"the same pass renders differently on different boots: "
                  f"{flapping}")
            ok = False
    return ok


def note_pass_varying(side):
    """Explain, rather than warn about, scenes that differ from pass to pass."""
    for scene in sorted(all_scenes(side)):
        if len(scene_crcs(side, scene)) > 1:
            why = PASS_VARYING_SCENES.get(scene)
            if why:
                print(f"   {side['label']}: '{scene}' renders different audio "
                      f"each pass - {why}. Reproducible per pass, so the A/B "
                      f"output check below still holds (it compares pass to "
                      f"pass).")
            else:
                print(f"!! {side['label']}: '{scene}' renders different audio "
                      f"each pass and is not a known stateful scene. Its output "
                      f"check is meaningless until that is understood.")


def compare_output(a, b, scene):
    """Compare rendered audio A vs B, pass by pass rather than as a blob.

    Lining passes up is what lets a stateful scene keep a usable output oracle:
    pass 1 of A and pass 1 of B start from the same carried state, so they must
    render identically unless the DSP actually changed.
    """
    pa, pb = crcs_by_pass(a, scene), crcs_by_pass(b, scene)
    shared = set(pa) & set(pb)
    if not shared:
        return "n/a"
    return "same" if all(pa[p] == pb[p] for p in shared) else "CHANGED"


def verdict_for(delta, noise, threshold, have_repeats):
    """Classify a delta against this scene's own measured noise.

    With a single capture per side the only spread available is between passes
    of one boot, which cannot see the run-to-run effects that dominate here - a
    relink shifts code across icache sets, and a reboot re-rolls that dice. That
    spread systematically *understates* the noise, so a verdict drawn from it
    would be confident and wrong. Withhold it instead.

    The ordering below matters too: noise is checked before the threshold, so a
    scene whose own measurements wander by 5% can never report a 4% regression.
    """
    if delta is None:
        return "n/a"
    if not have_repeats:
        return "1 capture"
    if noise is None:
        return "no noise est."
    if abs(delta) <= noise:
        return "within noise"
    if abs(delta) < threshold:
        return "small"
    return "REGRESSION" if delta > 0 else "IMPROVEMENT"


def pct(a_val, b_val):
    if not a_val:
        return None
    return 100.0 * (b_val - a_val) / a_val


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.2f}%"


def fmt_noise(n):
    return "  -  " if n is None else f"±{n:.2f}%"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_a", nargs="?", help="capture for side A")
    ap.add_argument("run_b", nargs="?", help="capture for side B")
    ap.add_argument("-A", dest="a_logs", nargs="+", metavar="LOG",
                    help="one or more captures of side A (repeats of one firmware)")
    ap.add_argument("-B", dest="b_logs", nargs="+", metavar="LOG",
                    help="one or more captures of side B")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="call a delta that clears the noise a regression at or "
                         "above this %% (default 0.5)")
    ap.add_argument("--metric", choices=["cyc", "us"], default="cyc",
                    help="metric the verdict is based on (default: cyc - the "
                         "cycle counter is less jittery than wall time)")
    ap.add_argument("--json", metavar="PATH",
                    help="also write the full comparison as JSON")
    args = ap.parse_args()

    a_logs = args.a_logs or ([args.run_a] if args.run_a else None)
    b_logs = args.b_logs or ([args.run_b] if args.run_b else None)
    if not a_logs or not b_logs:
        ap.error("need two sides: positional runA runB, or -A ... -B ...")

    a = parse_side(a_logs, "A")
    b = parse_side(b_logs, "B")
    ha, hb = a["header"], b["header"]

    for side, logs, h in ((a, a_logs, ha), (b, b_logs, hb)):
        print(f"{side['label']}: {len(logs)} capture(s)  rev={h.get('rev')}  "
              f"fp={h.get('fp')}  pacing={h.get('pacing')}  "
              f"profile={h.get('profile')}")
        for p in logs:
            print(f"     {p}")
    print()

    config_ok = check_headers(a, b)
    det_ok = check_crcs(a) & check_crcs(b)
    note_pass_varying(a)
    note_pass_varying(b)
    print()

    field = "median_cyc" if args.metric == "cyc" else "median_us"
    scenes = sorted(all_scenes(a) | all_scenes(b))
    block_us = ha.get("block_us")
    # Both sides need repeated captures before any delta is judgeable; see
    # verdict_for(). One capture per side still prints its pass spread, but the
    # verdict column withholds judgement rather than trusting it.
    have_repeats = len(a["captures"]) >= 2 and len(b["captures"]) >= 2

    hdr = (f"{'scene':<12} {'A med_us':>9} {'B med_us':>9} {'d_us':>9} "
           f"{'A med_cyc':>10} {'B med_cyc':>10} {'d_cyc':>9} "
           f"{'noise':>8} {'verdict':<14} {'output':>8}")
    print(hdr)
    print("-" * len(hdr))

    results = {}
    for scene in scenes:
        if scene not in all_scenes(a) or scene not in all_scenes(b):
            missing = "A" if scene not in all_scenes(a) else "B"
            print(f"{scene:<12} -- missing in side {missing} --")
            continue

        a_us, b_us = value_of(a, scene, "median_us"), value_of(b, scene, "median_us")
        a_cy, b_cy = value_of(a, scene, "median_cyc"), value_of(b, scene, "median_cyc")
        d_us, d_cy = pct(a_us, b_us), pct(a_cy, b_cy)

        # Noise is the worse of the two sides: a delta has to clear whichever
        # side wanders more before it can be called real.
        na, nb = noise_of(a, scene, field), noise_of(b, scene, field)
        noise = None if na is None or nb is None else max(na, nb)
        delta = d_cy if args.metric == "cyc" else d_us
        verdict = verdict_for(delta, noise, args.threshold, have_repeats)

        crc_a, crc_b = scene_crcs(a, scene), scene_crcs(b, scene)
        output = compare_output(a, b, scene)

        print(f"{scene:<12} {a_us:>9.0f} {b_us:>9.0f} {fmt_pct(d_us):>9} "
              f"{a_cy:>10.0f} {b_cy:>10.0f} {fmt_pct(d_cy):>9} "
              f"{fmt_noise(noise):>8} {verdict:<14} {output:>8}")

        results[scene] = {
            "a_median_us": a_us, "b_median_us": b_us, "delta_us_pct": d_us,
            "a_median_cyc": a_cy, "b_median_cyc": b_cy, "delta_cyc_pct": d_cy,
            "noise_pct": noise, "a_noise_pct": na, "b_noise_pct": nb,
            "verdict": verdict, "output": output,
            "a_crc": sorted(crc_a), "b_crc": sorted(crc_b),
            "a_capture_medians": capture_medians(a, scene, field),
            "b_capture_medians": capture_medians(b, scene, field),
            "a_samples": samples(a, scene, field),
            "b_samples": samples(b, scene, field),
        }

    if block_us:
        print()
        print(f"headroom vs {block_us} us block budget "
              f"(100% = block costs nothing):")
        for scene in scenes:
            if scene not in results:
                continue
            ha_pct = 100.0 * (1 - results[scene]["a_median_us"] / block_us)
            hb_pct = 100.0 * (1 - results[scene]["b_median_us"] / block_us)
            print(f"  {scene:<12} A {ha_pct:6.1f}%   B {hb_pct:6.1f}%")
            results[scene]["a_headroom_pct"] = ha_pct
            results[scene]["b_headroom_pct"] = hb_pct

    def total_overruns(side):
        return sum(s.get("overruns", 0)
                   for cap in side["captures"]
                   for ss in cap["summaries"].values()
                   for s in ss)

    ov_a, ov_b = total_overruns(a), total_overruns(b)
    if ov_a or ov_b:
        print(f"\noverruns (paced mode): A={ov_a}  B={ov_b}")

    # Profiler tags, pooled across each side's captures.
    def side_tags(side):
        out = defaultdict(list)
        for cap in side["captures"]:
            for key, vals in cap["tags"].items():
                out[key].extend(vals)
        return out

    ta_all, tb_all = side_tags(a), side_tags(b)
    shared_tags = sorted(set(ta_all) & set(tb_all))
    if shared_tags:
        print()
        thdr = (f"{'scene':<12} {'tag':<38} {'A us_total':>11} "
                f"{'B us_total':>11} {'delta':>10}")
        print(thdr)
        print("-" * len(thdr))
        rows = []
        for key in shared_tags:
            tav = statistics.median(ta_all[key])
            tbv = statistics.median(tb_all[key])
            d = pct(tav, tbv)
            rows.append((abs(tbv - tav), key, tav, tbv, d))
        rows.sort(key=lambda r: -r[0])
        for _, (scene, tag), tav, tbv, d in rows:
            print(f"{scene:<12} {tag:<38} {tav:>11.0f} {tbv:>11.0f} "
                  f"{fmt_pct(d):>10}")

    print()
    if not have_repeats:
        print("NOTE: at least one side has a single capture. The noise column "
              "is then only the spread between passes of one boot, which cannot "
              "see reboot- and relink-driven variation and so understates the "
              "real noise - the verdict column withholds judgement rather than "
              "trust it. Re-run with repeats (abrun.py --repeat N) to get a "
              "verdict.")
    else:
        print(f"verdict: 'within noise' = |delta| <= that scene's own measured "
              f"spread; REGRESSION/IMPROVEMENT = clears the noise AND >= "
              f"{args.threshold:.1f}%. Metric: {args.metric}.")

    changed = [sc for sc in results if results[sc]["output"] == "CHANGED"]
    if changed:
        print(f"!! OUTPUT CHANGED in: {', '.join(sorted(changed))} - expected "
              f"for a real DSP change, a bug otherwise. Do not accept 'faster' "
              f"without deciding which this is.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({
                "a": {"rev": ha.get("rev"), "logs": a_logs, "header": ha},
                "b": {"rev": hb.get("rev"), "logs": b_logs, "header": hb},
                "threshold": args.threshold,
                "metric": args.metric,
                "config_ok": config_ok,
                "deterministic": det_ok,
                "scenes": results,
            }, f, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
