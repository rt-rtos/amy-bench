#pragma once

#include <stdint.h>

#include "metrics.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * JSONL emitter: one JSON object per stdout line, schema-versioned, parsed by
 * bench/tools/abcompare.py. Emission happens only outside timed regions.
 * See bench/tools/schema.md for the field reference.
 */

void proto_run_header(void);

void proto_block(const char *scene, int pass, uint32_t idx,
                 uint32_t us, uint32_t cycles);

void proto_scene_summary(const char *scene, int pass, uint32_t blocks,
                         const bench_stats_t *us, const bench_stats_t *cycles,
                         uint32_t crc32, uint32_t overruns);

/* Profile builds only (CONFIG_BENCH_PROFILE): one line per active AMY tag,
 * reading the accumulated counters since the last amy_profiles_init(). */
void proto_profile_dump(const char *scene, int pass);

void proto_run_footer(void);

#ifdef __cplusplus
}
#endif
