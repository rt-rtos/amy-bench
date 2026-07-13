#include "serial_proto.h"

#include <stdio.h>
#include <inttypes.h>

#include "sdkconfig.h"
#include "amy.h"

#ifndef BENCH_GIT_REV
#define BENCH_GIT_REV "unknown"
#endif

#if CONFIG_BENCH_PACING_PACED
#define PACING_STR "paced"
#else
#define PACING_STR "free"
#endif

#ifdef AMY_USE_FIXEDPOINT
#define FP_STR "fixed"
#else
#define FP_STR "float"
#endif

#ifdef AMY_DEBUG
#define PROFILE_INT 1
#else
#define PROFILE_INT 0
#endif

void proto_run_header(void)
{
    printf("{\"schema\":1,\n\"run\":\"amy-bench\",\n\"rev\":\"%s\",\n"
           "\"sr\":%d,\n\"block\":%d,\n\"block_us\":%" PRIu32 ",\n"
           "\"pacing\":\"%s\",\n\"fp\":\"%s\",\n\"profile\":%d,\n"
           "\"passes\":%d,\n\"cpu_mhz\":%d}\n",
           BENCH_GIT_REV,
           AMY_SAMPLE_RATE, AMY_BLOCK_SIZE, (uint32_t)AMY_BLOCK_US,
           PACING_STR, FP_STR, PROFILE_INT,
           CONFIG_BENCH_PASSES, CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ);
}

void proto_block(const char *scene, int pass, uint32_t idx,
                 uint32_t us, uint32_t cycles)
{
    printf("{\"schema\":1,\n\"scene\":\"%s\",\n\"pass\":%d,\n\"block\":%" PRIu32 ",\n"
           "\"us\":%" PRIu32 ",\n\"cycles\":%" PRIu32 "}\n",
           scene, pass, idx, us, cycles);
}

void proto_scene_summary(const char *scene, int pass, uint32_t blocks,
                         const bench_stats_t *us, const bench_stats_t *cycles,
                         uint32_t crc32, uint32_t overruns)
{
    printf("{\"schema\":1,\n\"scene\":\"%s\",\n\"pass\":%d,\n\"summary\":true,\n"
           "\"blocks\":%" PRIu32 ",\n"
           "\"min_us\":%" PRIu32 ",\n\"median_us\":%" PRIu32 ",\n\"mean_us\":%" PRIu32
           ",\n\"p99_us\":%" PRIu32 ",\n\"max_us\":%" PRIu32 ",\n"
           "\"min_cyc\":%" PRIu32 ",\n\"median_cyc\":%" PRIu32 ",\n\"mean_cyc\":%" PRIu32
           ",\n\"p99_cyc\":%" PRIu32 ",\n\"max_cyc\":%" PRIu32 ",\n"
           "\"crc32\":\"%08" PRIx32 "\",\n\"overruns\":%" PRIu32 "}\n",
           scene, pass, blocks,
           us->min, us->median, us->mean, us->p99, us->max,
           cycles->min, cycles->median, cycles->mean, cycles->p99, cycles->max,
           crc32, overruns);
}

#ifdef AMY_DEBUG
extern const char *profile_tag_name(enum itags tag);
#endif

void proto_profile_dump(const char *scene, int pass)
{
#ifdef AMY_DEBUG
    for (int tag = 0; tag < NO_TAG; tag++) {
        if (profiles[tag].calls == 0) {
            continue;
        }
        printf("{\"schema\":1,\n\"scene\":\"%s\",\n\"pass\":%d,\n"
               "\"tag\":\"%s\",\n\"calls\":%" PRIu32 ",\n\"us_total\":%" PRIu64 "}\n",
               scene, pass,
               profile_tag_name((enum itags)tag),
               profiles[tag].calls, profiles[tag].us_total);
    }
#else
    (void)scene;
    (void)pass;
#endif
}

void proto_run_footer(void)
{
    printf("{\"schema\":1,\"run_end\":true}\n");
}
