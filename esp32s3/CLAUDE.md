# amy-bench (ESP32-S3) - Claude Code Instructions

On-target A/B benchmark for AMY. The sources under test live in a separate AMY
checkout, not in this repo - see `../README.md` for the workflow and
`../AMY-EDITS.md` for the only permitted `src/` deviations.

## Toolchain / build

- ESP-IDF 6.0, target `esp32s3`, board: ESP32-S3 with 16 MB flash + octal PSRAM.
- Environment: source the ESP-IDF `export.sh` for a v6.0 install, and make sure
  `IDF_PYTHON_ENV_PATH` points at that install's python env (the capture stage
  needs its pyserial).
- AMY sources: `AMY_REPO=/path/to/amy`, or a sibling `../amy` clone, or an
  explicit `-D AMY_SRC_DIR=/path/to/amy/src`.
- Build: `idf.py build` from this directory. LTO profile: see `../README.md`
  (needs the patched `gcc.cmake` copied into `managed_components/`).
- `flash` REPLACES whatever firmware is on the board (including its partition
  table) - always confirm with the user before flashing, and remind them the
  previous firmware needs a full reflash to restore.
- No destructive flash ops (`erase-flash` etc.) without explicit request.

## Hard rules

1. `src/` is upstream's - only the two `#ifndef` guards in `../AMY-EDITS.md`
   may differ from the upstream base. DSP experiments are commits on top,
   one experiment per branch/commit.
2. Never edit `sdkconfig` directly (generated). Defaults live in
   `sdkconfig.defaults`; bench options in `main/Kconfig.projbuild`.
3. Nothing may print or allocate inside the timed measurement bracket in
   `bench_main.c` - emission happens after the scene's loop.
4. Comparisons are only valid between runs with identical header config
   (`fp`, `sr`, `pacing`, `profile`) and the same build profile (LTO vs not).
   Profile-build (`AMY_DEBUG`) numbers are never compared to wall numbers.
5. Scenes must stay deterministic: no wall-clock-dependent messages, no
   noise/KS material unless its PRNG seeding is verified fixed. The test is
   that a *given pass* renders the same CRC on every boot - NOT that all passes
   agree with each other. Scenes carrying state between passes (`fx_sine8`, via
   AMY's un-resettable reverb/chorus buffers - see "Known AMY gap" in
   ../README.md) legitimately render different audio each pass and are still
   perfectly usable; `abcompare.py` compares the two sides pass by pass.
