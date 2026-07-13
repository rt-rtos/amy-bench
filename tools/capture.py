#!/usr/bin/env python3
"""Capture one amy-bench run from a board's serial port, non-interactively.

Usage:
    capture.py --port /dev/ttyACM0 --out runA.log [--timeout 180]

Replaces `idf.py monitor | tee run.log`, which is a TTY tool the operator has to
Ctrl-] out of and so cannot be scripted. This resets the board, then copies raw
bytes to the log until the firmware's run footer appears:

    {"schema":1,"run_end":true}

No parsing beyond spotting that footer - abcompare.py's iter_bench_objects()
already reassembles records out of raw monitor noise, so the log wants to stay
exactly as noisy as a monitor capture.

Exits non-zero if the run never completes, so a driver can tell "the board is
wedged" apart from "the run was slow".
"""

import argparse
import sys
import time

# Matched against the log with whitespace stripped out, because the firmware
# pretty-prints records across lines (a newline after every top-level comma).
RUN_END_NEEDLE = b'{"schema":1,"run_end":true}'


def reset_board(ser):
    """Reset into normal run mode: DTR deasserted (not download mode), RTS pulse.

    Same dance tools/arduino_loadsweep/measure.py uses on the same silicon.
    """
    ser.dtr = False
    ser.rts = True
    time.sleep(0.15)
    ser.rts = False


def capture(port, baud, timeout, out_path, quiet=False):
    import serial

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0.2
    # Set both lines before open() so opening the port cannot itself strobe a
    # reset into download mode on boards that wire DTR/RTS to EN/IO0.
    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.reset_input_buffer()
    reset_board(ser)

    t0 = time.time()
    # Everything seen so far, whitespace removed, so the footer matches whether
    # the firmware emitted it on one line or split across several.
    seen = bytearray()
    done = False

    with open(out_path, "wb") as f:
        while time.time() - t0 < timeout:
            data = ser.read(4096)
            if not data:
                continue
            f.write(data)
            f.flush()
            if not quiet:
                sys.stderr.write(data.decode("utf-8", "replace"))
                sys.stderr.flush()
            seen += bytes(c for c in data if c not in b" \t\r\n")
            if RUN_END_NEEDLE in seen:
                done = True
                break
            # The footer is all we scan for; keep only enough tail to span it.
            if len(seen) > 4096:
                del seen[:-len(RUN_END_NEEDLE)]

    ser.close()
    elapsed = time.time() - t0
    if not done:
        print(f"\n[capture] TIMEOUT after {elapsed:.0f}s with no run_end - "
              f"partial log in {out_path}", file=sys.stderr)
        return False
    print(f"\n[capture] run_end after {elapsed:.0f}s -> {out_path}",
          file=sys.stderr)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial port, e.g. /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", required=True, help="path to write the capture to")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="seconds to wait for run_end (default: 180)")
    ap.add_argument("--quiet", action="store_true",
                    help="do not mirror the serial stream to stderr")
    args = ap.parse_args()

    try:
        ok = capture(args.port, args.baud, args.timeout, args.out, args.quiet)
    except ImportError:
        sys.exit("capture.py needs pyserial: pip install pyserial")
    except Exception as e:  # serial.SerialException and friends
        sys.exit(f"[capture] {type(e).__name__}: {e}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
