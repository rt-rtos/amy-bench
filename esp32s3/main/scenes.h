#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * A benchmark scene is a deterministic AMY workload: a list of wire-format
 * messages that set it up, an optional periodic re-trigger (for one-shot
 * material like PCM drums or envelope-driven filter sweeps), and block
 * counts. Messages are plain AMY message strings (see docs/api.md) so a
 * scene reads like a patch dump and diffs cleanly.
 */
typedef struct {
    const char *name;
    const char *const *setup;     // NULL-terminated message list
    const char *const *teardown;  // NULL-terminated; may be NULL
    const char *const *tick;      // periodic re-injection; may be NULL
    uint16_t tick_period_blocks;  // blocks between tick injections (if tick)
    uint16_t warmup_blocks;       // rendered before measurement starts
    uint16_t measure_blocks;      // measured blocks (<= BENCH_MAX_BLOCKS)
} bench_scene_t;

extern const bench_scene_t bench_scenes[];
extern const int bench_num_scenes;

#ifdef __cplusplus
}
#endif
