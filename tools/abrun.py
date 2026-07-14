#!/usr/bin/env python3
"""Automate an on-target A/B run: two git refs, one board, one report.

Usage (with the ESP-IDF environment sourced):
    abrun.py --port /dev/ttyACM0                       # working tree vs merge-base
    abrun.py --port /dev/ttyACM0 --head exp/faster-filter
    abrun.py --port /dev/ttyACM0 --base HEAD --head HEAD --repeat 5   # noise floor

What it does, and why each step is the way it is:

1.  Materialises each side's src/ with `git archive` into a scratch dir. Read-only
    by construction, so unlike switching branches it cannot disturb your working
    tree - and the working tree is where the *harness* comes from, so both sides
    are measured with the same ruler even though bench/ may not exist on the
    baseline ref at all.

2.  Refuses to build a src/ tree that lacks the two `#ifndef` guards from
    bench/AMY-EDITS.md. Without them a `-DAMY_SAMPLE_RATE=48000` compile
    definition is silently clobbered by amy.h's own later `#define` and
    AMY_USE_FLOAT is ignored, so the side would build at 44100/fixed and compare
    clean against a 48000/float side. That failure produces plausible numbers,
    which makes it far more dangerous than a build error.

3.  Flashes BOTH firmwares once - side A into ota_0, side B into ota_1 - then
    alternates between them with a boot-partition switch and a reset. Swapping
    sides costs about a second instead of a ~20s reflash, which is what makes
    repeats affordable, and repeats are the whole game: a delta means nothing
    until you can see the run-to-run spread it has to clear.

4.  Interleaves A B A B rather than running A A B B, so board drift (thermal,
    supply) cannot masquerade as the change under test.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.abspath(os.path.join(HERE, "..", "esp32s3"))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

# The two build-config guards from bench/AMY-EDITS.md. A src/ tree without these
# cannot honour the bench's compile definitions - see the module docstring.
REQUIRED_GUARDS = [
    ("#ifndef AMY_SAMPLE_RATE", "AMY_SAMPLE_RATE overridable"),
    ("#ifndef AMY_USE_FLOAT", "float mode selectable"),
]

WORKTREE = "worktree"


def run(cmd, **kw):
    """Run a command, echoing it, and abort the whole tool if it fails."""
    kw.setdefault("cwd", REPO_ROOT)
    printable = " ".join(str(c) for c in cmd)
    print(f"  $ {printable}", flush=True)
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        sys.exit(f"[abrun] FAILED ({r.returncode}): {printable}")
    return r


def git(*args, cwd=REPO_ROOT):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[abrun] git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def describe(ref):
    if ref == WORKTREE:
        return git("describe", "--always", "--dirty", "--tags")
    return git("describe", "--always", "--tags", ref)


def materialise_src(ref, dest):
    """Put `ref`'s src/ tree at dest/src. For the working tree, use it in place
    so uncommitted experiments are measurable without a commit."""
    if ref == WORKTREE:
        return os.path.join(REPO_ROOT, "src")

    os.makedirs(dest, exist_ok=True)
    tar_path = os.path.join(dest, "src.tar")
    with open(tar_path, "wb") as f:
        r = subprocess.run(["git", "archive", ref, "src/"],
                           cwd=REPO_ROOT, stdout=f)
    if r.returncode != 0:
        sys.exit(f"[abrun] git archive {ref} failed - is it a valid ref?")
    with tarfile.open(tar_path) as t:
        t.extractall(dest)
    os.remove(tar_path)

    src_dir = os.path.join(dest, "src")

    # git archive stamps every entry with the *commit* time, but the build dir is
    # reused across runs and its objects carry the *wall-clock* time they were
    # compiled at. Extract a ref whose commit predates the last build and ninja
    # compares those two stamps, concludes the sources are older than the objects,
    # and skips them - silently linking the PREVIOUS ref's code into a firmware
    # labelled with this one. That is the worst possible failure here: it does not
    # error, it produces a clean A/B report about two binaries that are not the
    # ones named in it. Stamping the throwaway tree with "now" makes mtime mean
    # what ninja assumes it means.
    now = time.time()
    for root, _dirs, files in os.walk(src_dir):
        for name in files:
            os.utime(os.path.join(root, name), (now, now))

    return src_dir


def ensure_guards(src_dir, ref, in_place):
    """Make a materialised src/ tree honour the bench's compile definitions.

    Upstream amy.h hard-#defines AMY_SAMPLE_RATE and AMY_USE_FIXEDPOINT, so an
    injected -DAMY_SAMPLE_RATE=48000 is silently clobbered (GCC keeps the last
    definition and only warns) and -DAMY_USE_FLOAT is ignored outright. A
    baseline built from such a tree would compile clean at 44100/fixed-point and
    compare against a 48000/float head - plausible numbers, entirely wrong.

    Rather than require every baseline ref to carry the guards (upstream does
    not, and merge bases are old), wrap the offending #defines here, in the
    throwaway tree git archive just produced. The transform is what
    bench/AMY-EDITS.md describes: each #define becomes conditional. It changes
    zero instructions unless a define is injected - and the same definitions are
    injected on both sides - so it cannot bias the comparison.

    The working tree is never patched (in_place): a src/ you are editing is
    yours, and it must already carry the guards.
    """
    amy_h = os.path.join(src_dir, "amy.h")
    if not os.path.exists(amy_h):
        sys.exit(f"[abrun] {ref}: no amy.h in {src_dir}")

    text = open(amy_h, errors="replace").read()
    missing = [why for guard, why in REQUIRED_GUARDS if guard not in text]
    if not missing:
        return

    if in_place:
        sys.exit(
            f"\n[abrun] REFUSING TO BUILD the working tree: src/amy.h is missing "
            f"the build-config guard(s) for: {', '.join(missing)}.\n\n"
            f"        Without them the bench's compile definitions are silently\n"
            f"        ignored, so this side would build at 44100/fixed-point.\n"
            f"        abrun patches a *materialised* baseline tree automatically,\n"
            f"        but it will not edit your working tree. Apply the two\n"
            f"        #ifndef guards from bench/AMY-EDITS.md.\n")

    patched = re.sub(
        r"^(#define\s+AMY_SAMPLE_RATE\s+\d+.*)$",
        r"#ifndef AMY_SAMPLE_RATE\n\1\n#endif",
        text, flags=re.M)
    patched = re.sub(
        r"^(#define\s+AMY_USE_FIXEDPOINT\s*)$",
        r"#ifndef AMY_USE_FLOAT\n\1\n#endif",
        patched, flags=re.M)

    still = [why for guard, why in REQUIRED_GUARDS if guard not in patched]
    if still:
        sys.exit(
            f"\n[abrun] could not apply the build-config guard(s) for "
            f"{', '.join(still)} to ref '{ref}'.\n\n"
            f"        amy.h no longer matches the shape bench/AMY-EDITS.md\n"
            f"        describes, so the guards must be applied by hand rather\n"
            f"        than guessed at. Refusing to measure a tree whose sample\n"
            f"        rate and arithmetic mode cannot be verified.\n")

    with open(amy_h, "w") as f:
        f.write(patched)
    print(f"[abrun] {ref}: applied build-config guards to the scratch tree "
          f"({', '.join(missing)}) - no-ops unless a define is injected, and the "
          f"same defines go to both sides")


def build_side(side, src_dir, rev, args, env):
    """Build one firmware. Same harness, same sdkconfig - only src/ differs."""
    build_dir = os.path.join(BENCH_DIR, "build", "ab", side)
    sdkconfig = os.path.join(build_dir, "sdkconfig")

    defaults = ["sdkconfig.defaults"]
    if args.lto:
        defaults.append("sdkconfig.defaults.lto")

    # Bench Kconfig knobs. Set identically on both sides; abcompare's
    # check_headers() independently asserts the firmwares agree, from what the
    # binaries actually report rather than from what we intended here.
    overlay = os.path.join(args.scratch, f"bench-{side}.defaults")
    with open(overlay, "w") as f:
        f.write(f"CONFIG_BENCH_AMY_FLOAT={'y' if args.float_mode else 'n'}\n")
        f.write(f"CONFIG_BENCH_PROFILE={'y' if args.profile else 'n'}\n")
        f.write(f"CONFIG_BENCH_PASSES={args.passes}\n")
        if args.paced:
            f.write("CONFIG_BENCH_PACING_PACED=y\n")
        else:
            f.write("CONFIG_BENCH_PACING_FREE=y\n")
    defaults.append(overlay)

    print(f"\n[abrun] build {side}: src={src_dir} rev={rev}")
    run(["idf.py", "-B", build_dir,
         "-D", f"SDKCONFIG={sdkconfig}",
         "-D", f"SDKCONFIG_DEFAULTS={';'.join(defaults)}",
         "-D", f"AMY_SRC_DIR={src_dir}",
         "-D", f"BENCH_GIT_REV={rev}",
         "build"], cwd=BENCH_DIR, env=env)

    if args.lto:
        # The published cmake_utilities component's IPO check fails against the
        # ESP-IDF cross toolchain; managed_components/ is regenerated on fetch,
        # so the patched copy has to be reapplied and the build rerun.
        patched = os.path.join(HERE, "build-patches",
                               "espressif__cmake_utilities-gcc.cmake")
        target = os.path.join(BENCH_DIR, "managed_components",
                              "espressif__cmake_utilities", "gcc.cmake")
        if os.path.exists(patched) and os.path.exists(os.path.dirname(target)):
            shutil.copyfile(patched, target)
            run(["idf.py", "-B", build_dir, "build"], cwd=BENCH_DIR, env=env)

    return build_dir


def otatool(env, port, *args):
    idf_path = env["IDF_PATH"]
    tool = os.path.join(idf_path, "components", "app_update", "otatool.py")
    e = dict(env)
    # otatool imports parttool, which is not on the default path.
    e["PYTHONPATH"] = os.path.join(idf_path, "components", "partition_table")
    run([sys.executable, tool, "--port", port, *args], env=e)


def partition_offset(subtype):
    """Offset of a partition, read from the table rather than hardcoded."""
    csv_path = os.path.join(BENCH_DIR, "partitions.csv")
    for line in open(csv_path):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        cols = [c.strip() for c in line.split(",")]
        if len(cols) >= 4 and cols[2] == subtype:
            return cols[3]
    sys.exit(f"[abrun] no '{subtype}' partition in {csv_path}")


def stage_app(env, port, subtype, app_bin):
    """Write one app image into a partition, without disturbing the other slot.

    Not otatool's write_ota_partition: in ESP-IDF 6.0 that entry point is broken
    (otatool.py dispatches 'input' to a _write_ota_partition() whose parameter is
    named input_file, so it dies with a TypeError). Writing the image straight to
    the partition's offset does the same job with stock esptool.
    """
    offset = partition_offset(subtype)
    run([sys.executable, "-m", "esptool", "--chip", "esp32s3", "-p", port,
         "-b", "460800", "--before", "default-reset", "--after", "hard-reset",
         "write-flash", offset, app_bin], env=env)


def capture_one(port, out_path, timeout, quiet):
    sys.path.insert(0, HERE)
    import capture
    ok = capture.capture(port, 115200, timeout, out_path, quiet=quiet)
    if not ok:
        sys.exit(f"[abrun] capture timed out with no run_end -> {out_path}. "
                 f"The board may be wedged, or the run needs a longer --timeout.")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port, e.g. /dev/ttyACM0")
    ap.add_argument("--head", default=WORKTREE,
                    help="ref under test (default: the working tree, so "
                         "uncommitted experiments work)")
    ap.add_argument("--base", default=None,
                    help="baseline ref (default: merge-base of head and main)")
    ap.add_argument("--repeat", type=int, default=3,
                    help="captures per side, interleaved A B A B (default: 3). "
                         "Two is the minimum that yields any noise estimate.")
    ap.add_argument("--outdir", default=None,
                    help="where to write logs (default: bench/tools/abrun-out)")
    ap.add_argument("--scratch", default=None,
                    help="scratch dir for materialised src/ trees")
    # 0.5% is ~7x the measured code-layout floor (see bench/README.md): two
    # builds of identical sources, relinked, differ by up to 0.07%. Boot-to-boot
    # noise on one binary is far smaller (0.01%), but a threshold cannot be set
    # from that - both sides of a real A/B are different binaries, so layout
    # noise lands in the delta, never in the noise column.
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="seconds to wait for one run to finish")
    ap.add_argument("--float", dest="float_mode", action="store_true",
                    help="build float DSP (the S3 has an FPU and production "
                         "runs float); default is upstream fixed-point")
    ap.add_argument("--paced", action="store_true",
                    help="GPTimer-paced at the real block period; default is "
                         "free-running (back-to-back blocks, max sensitivity)")
    ap.add_argument("--profile", action="store_true",
                    help="per-tag AMY_DEBUG build (inflates absolute numbers)")
    ap.add_argument("--lto", action="store_true")
    ap.add_argument("--passes", type=int, default=3,
                    help="scene-list repetitions inside one run (default: 3)")
    ap.add_argument("--build-only", action="store_true",
                    help="build both sides and stop - no board needed")
    ap.add_argument("--quiet", action="store_true",
                    help="do not mirror the serial stream while capturing")
    args = ap.parse_args()

    if not args.build_only and not args.port:
        ap.error("--port is required unless --build-only")

    env = dict(os.environ)
    if "IDF_PATH" not in env:
        sys.exit("[abrun] IDF_PATH is not set - source the ESP-IDF export.sh "
                 "first (see bench/esp32s3/CLAUDE.md).")

    # Check everything the capture stage needs *before* the builds, which take
    # minutes. pyserial lives in the IDF python env, not necessarily in whatever
    # interpreter is running this.
    if not args.build_only:
        try:
            import serial  # noqa: F401
        except ImportError:
            sys.exit(f"[abrun] this interpreter ({sys.executable}) has no "
                     f"pyserial, so the capture stage would fail after the "
                     f"builds. Re-run with the IDF python env:\n"
                     f"    $IDF_PYTHON_ENV_PATH/bin/python "
                     f"{os.path.relpath(__file__, REPO_ROOT)} ...")
        if not os.path.exists(args.port):
            sys.exit(f"[abrun] no such port: {args.port}")

    args.scratch = args.scratch or os.path.join(BENCH_DIR, "build", "ab", "src")
    args.outdir = args.outdir or os.path.join(HERE, "abrun-out")
    os.makedirs(args.scratch, exist_ok=True)
    os.makedirs(args.outdir, exist_ok=True)

    head = args.head
    base = args.base
    if base is None:
        head_ref = "HEAD" if head == WORKTREE else head
        base = git("merge-base", head_ref, "main")
        print(f"[abrun] baseline defaults to merge-base({head_ref}, main) = "
              f"{base[:12]}")

    rev_a, rev_b = describe(base), describe(head)
    print(f"[abrun] A (base) = {base}  -> {rev_a}")
    print(f"[abrun] B (head) = {head}  -> {rev_b}")

    src_a = materialise_src(base, os.path.join(args.scratch, "A"))
    src_b = materialise_src(head, os.path.join(args.scratch, "B"))
    ensure_guards(src_a, base, in_place=(base == WORKTREE))
    ensure_guards(src_b, head, in_place=(head == WORKTREE))

    build_a = build_side("A", src_a, rev_a, args, env)
    build_b = build_side("B", src_b, rev_b, args, env)

    if args.build_only:
        print(f"\n[abrun] built both sides; stopping (--build-only)."
              f"\n  A: {build_a}\n  B: {build_b}")
        return

    # Side A's flash writes the bootloader, partition table, fresh otadata and
    # A's app into the first app slot (ota_0). Side B's app then goes into
    # ota_1, so both live on the board and swapping is just a boot-slot switch.
    print(f"\n[abrun] flashing A into ota_0 (also bootloader + partition table)")
    run(["idf.py", "-B", build_a, "-p", args.port, "flash"],
        cwd=BENCH_DIR, env=env)

    print(f"\n[abrun] staging B into ota_1")
    stage_app(env, args.port, "ota_1", os.path.join(build_b, "amy-bench.bin"))

    logs = {"A": [], "B": []}
    slot = {"A": "0", "B": "1"}
    for i in range(args.repeat):
        for side in ("A", "B"):
            out = os.path.join(args.outdir, f"{side}{i}.log")
            print(f"\n[abrun] --- repeat {i + 1}/{args.repeat}, side {side} "
                  f"(ota_{slot[side]}) ---")
            otatool(env, args.port, "switch_ota_partition", "--slot", slot[side])
            time.sleep(0.5)
            capture_one(args.port, out, args.timeout, args.quiet)
            logs[side].append(out)

    print("\n[abrun] comparing\n")
    cmp_json = os.path.join(args.outdir, "compare.json")
    subprocess.run([sys.executable, os.path.join(HERE, "abcompare.py"),
                    "-A", *logs["A"], "-B", *logs["B"],
                    "--threshold", str(args.threshold),
                    "--json", cmp_json])


if __name__ == "__main__":
    main()
