#pragma once

#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * render_clock - GPTimer-backed block clock for paced benchmark mode.
 *
 * Emits exactly one "render now" signal per audio block period
 * (AMY_BLOCK_SIZE / sample rate), independent of the FreeRTOS tick rate.
 * The alarm ISR is pinned to the caller's core and wakes it via a counting
 * task notification, mirroring the production S3-Amysynth render clock.
 */

/**
 * @brief Create and start the render clock.
 *
 * MUST be called from the task that will call render_clock_wait(): the alarm
 * ISR is registered on the calling core and notifies the calling task.
 *
 * @param block_frames    Audio block size in sample frames (AMY_BLOCK_SIZE).
 * @param sample_rate_hz  Audio sample rate in Hz (AMY_SAMPLE_RATE).
 * @return ESP_OK on success.
 */
esp_err_t render_clock_start(uint32_t block_frames, uint32_t sample_rate_hz);

/**
 * @brief Block until the next render tick.
 *
 * @return Accumulated tick count since the last wait. Normally 1; a value >1
 *         means ticks elapsed while the previous block was still rendering
 *         (overrun). The caller renders exactly ONE block regardless and
 *         treats the excess as a diagnostic signal.
 */
uint32_t render_clock_wait(void);

/**
 * @brief Stop and delete the render clock.
 */
void render_clock_stop(void);

#ifdef __cplusplus
}
#endif
