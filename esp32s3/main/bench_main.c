/*
 * AMY on-target A/B benchmark.
 *
 * Runs deterministic synth scenes headless (no audio output) and measures
 * the cost of each rendered block. Everything - render and reporting - runs
 * in the main task on one core; nothing else competes for it. Emission
 * happens strictly outside the timed loop.
 */

#include <string.h>
#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_cpu.h"
#include "esp_log.h"
#include "esp_rom_crc.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "amy.h"

#include "metrics.h"
#include "render_clock.h"
#include "scenes.h"
#include "serial_proto.h"

static const char *TAG = "bench";

// Per-block sample buffers for one scene measurement window.
#define BENCH_MAX_BLOCKS 2000

static uint32_t s_us[BENCH_MAX_BLOCKS];
static uint32_t s_cyc[BENCH_MAX_BLOCKS];

// amy_add_message() takes a mutable char* and may tokenize in place;
// wire strings live in .rodata, so inject through a scratch copy.
static void inject_messages(const char *const *msgs)
{
    static char buf[256];
    if (msgs == NULL) {
        return;
    }
    for (; *msgs != NULL; msgs++) {
        strlcpy(buf, *msgs, sizeof(buf));
        amy_add_message(buf);
    }
}

static void render_untimed_blocks(uint32_t n)
{
    for (uint32_t i = 0; i < n; i++) {
#if CONFIG_BENCH_PACING_PACED
        render_clock_wait();
#endif
        (void)amy_simple_fill_buffer();
    }
}

static void run_scene(const bench_scene_t *scene, int pass)
{
    uint32_t blocks = scene->measure_blocks;
    if (blocks > BENCH_MAX_BLOCKS) {
        blocks = BENCH_MAX_BLOCKS;
    }

    inject_messages(scene->setup);
    render_untimed_blocks(scene->warmup_blocks);

#ifdef AMY_DEBUG
    amy_profiles_init();
#endif

    uint32_t crc = 0;
    uint32_t overruns = 0;

    for (uint32_t i = 0; i < blocks; i++) {
        // Re-trigger injection stays outside the timed bracket; the delta
        // execution it causes lands inside the next block, which is the
        // workload under test.
        if (scene->tick != NULL && scene->tick_period_blocks != 0 &&
            i != 0 && (i % scene->tick_period_blocks) == 0) {
            inject_messages(scene->tick);
        }

#if CONFIG_BENCH_PACING_PACED
        uint32_t ticks = render_clock_wait();
        if (ticks > 1) {
            overruns += ticks - 1;
        }
#endif

        uint32_t c0 = esp_cpu_get_cycle_count();
        int64_t t0 = esp_timer_get_time();

        int16_t *block = amy_simple_fill_buffer();

        int64_t t1 = esp_timer_get_time();
        uint32_t c1 = esp_cpu_get_cycle_count();

        s_us[i] = (uint32_t)(t1 - t0);
        s_cyc[i] = c1 - c0;
        crc = esp_rom_crc32_le(crc, (const uint8_t *)block,
                               AMY_BLOCK_SIZE * AMY_NCHANS * sizeof(int16_t));
    }

#if CONFIG_BENCH_EMIT_PER_BLOCK
    for (uint32_t i = 0; i < blocks; i++) {
        proto_block(scene->name, pass, i, s_us[i], s_cyc[i]);
    }
#endif

#ifdef AMY_DEBUG
    proto_profile_dump(scene->name, pass);
#endif

    bench_stats_t us_stats;
    bench_stats_t cyc_stats;
    bench_stats_compute(s_us, blocks, &us_stats);
    bench_stats_compute(s_cyc, blocks, &cyc_stats);
    proto_scene_summary(scene->name, pass, blocks, &us_stats, &cyc_stats,
                        crc, overruns);

    inject_messages(scene->teardown);
    render_untimed_blocks(50);  // flush releases/FX tails before the next scene
}

void app_main(void)
{
    amy_config_t config = amy_default_config();
    config.audio = AMY_AUDIO_IS_NONE;
    config.midi = AMY_MIDI_IS_NONE;
    // Single-task, single-core: the bench loop drives amy_simple_fill_buffer()
    // itself, matching the production firmware's synchronous render model.
    config.platform.multicore = 0;
    config.platform.multithread = 0;
    // The overload failsafe swaps in a warning sound when the smoothed render
    // load stays high - that would silently change heavy scenes. Disable it.
    config.overload_threshold = 0;

    amy_start(config);

    ESP_LOGI(TAG, "amy started: sr=%d block=%d scenes=%d passes=%d",
             AMY_SAMPLE_RATE, AMY_BLOCK_SIZE, bench_num_scenes,
             CONFIG_BENCH_PASSES);

    proto_run_header();

#if CONFIG_BENCH_PACING_PACED
    ESP_ERROR_CHECK(render_clock_start(AMY_BLOCK_SIZE, AMY_SAMPLE_RATE));
#endif

    for (int pass = 0; pass < CONFIG_BENCH_PASSES; pass++) {
        for (int s = 0; s < bench_num_scenes; s++) {
            if (CONFIG_BENCH_SCENE_SELECT >= 0 &&
                s != CONFIG_BENCH_SCENE_SELECT) {
                continue;
            }
            run_scene(&bench_scenes[s], pass);
        }
    }

    proto_run_footer();
    ESP_LOGI(TAG, "bench complete");

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
