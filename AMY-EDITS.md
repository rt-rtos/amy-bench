# src/ edits carried by the bench branch

The bench rule is that `src/` stays byte-identical to upstream so DSP experiment
diffs are 1:1 PR-able. Exactly two build-config exceptions exist, both
`#ifndef` guards that change nothing unless a compile definition is injected.
Both are upstream-PR candidates in their own right (config hygiene, no
behavior change for existing builds).

## 1. `AMY_SAMPLE_RATE` overridable (`src/amy.h`)

The platform cascade (`AMY_DAISY` / `__EMSCRIPTEN__` / fallback 44100) is
wrapped in `#ifndef AMY_SAMPLE_RATE`. Without this, a `-DAMY_SAMPLE_RATE=48000`
compile definition is silently clobbered by the header's later `#define`
(GCC keeps the last definition and only warns). The bench injects 48000 to
match the production S3-Amysynth firmware's USB-UAC rate.

## 2. Float mode selectable (`src/amy.h`)

`#define AMY_USE_FIXEDPOINT` is wrapped in `#ifndef AMY_USE_FLOAT`. Upstream
default (fixed-point) is unchanged; defining `AMY_USE_FLOAT` selects the float
DSP paths. The ESP32-S3 has a hardware FPU and the production firmware runs
float, so A/B results intended for that target should be measured in float
mode (`CONFIG_BENCH_AMY_FLOAT=y`). Results are only comparable between runs
built with the same arithmetic mode - the run header records it.
