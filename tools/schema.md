# amy-bench JSONL schema (v1)

The firmware prints one JSON object per record to stdout, interleaved with
normal ESP-IDF log lines. Each object's top-level fields are broken across
multiple lines (a newline after every comma) for readability; a record ends
where its braces balance. Parsers reassemble a record starting at a line
that begins with `{` and keep only records that carry `"schema": 1`.

## Run header (once, before the first scene)

| field | meaning |
|---|---|
| `run` | always `"amy-bench"` |
| `rev` | `git describe --always --dirty --tags` at configure time |
| `sr` | `AMY_SAMPLE_RATE` compiled in |
| `block` | `AMY_BLOCK_SIZE` (frames per block) |
| `block_us` | real-time budget per block in microseconds |
| `pacing` | `"free"` (back-to-back) or `"paced"` (GPTimer block clock) |
| `fp` | `"fixed"` or `"float"` arithmetic build |
| `profile` | 1 if built with `AMY_DEBUG` (per-tag lines present) |
| `passes` | scene-list repetitions |
| `cpu_mhz` | CPU clock |

## Scene summary (one per scene per pass; `"summary": true`)

`scene`, `pass`, `blocks`, then `min/median/mean/p99/max` for both `_us`
(esp_timer wall microseconds) and `_cyc` (CPU cycle counter) per rendered
block, `crc32` of all rendered audio in the measurement window (hex string),
and `overruns` (paced mode: ticks that elapsed while a block was still
rendering; always 0 in free-running mode).

## Per-block record (only with `CONFIG_BENCH_EMIT_PER_BLOCK=y`)

`scene`, `pass`, `block` (index), `us`, `cycles`.

## Profiler tag (only in profile builds)

`scene`, `pass`, `tag` (upstream AMY tag name, e.g. `FILTER_PROCESS`),
`calls`, `us_total` - accumulated over that scene's measurement window.

## Run footer

`{"schema":1,"run_end":true}` - capture is complete.
