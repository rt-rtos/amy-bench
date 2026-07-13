#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t min;
    uint32_t max;
    uint32_t mean;
    uint32_t median;
    uint32_t p99;
} bench_stats_t;

/**
 * @brief Compute summary statistics over a sample series.
 *
 * Sorts the series in place (call after the measurement loop is done).
 *
 * @param samples  Sample values; reordered by this call.
 * @param n        Number of samples (must be >= 1).
 * @param out      Filled with min/max/mean/median/p99.
 */
void bench_stats_compute(uint32_t *samples, uint32_t n, bench_stats_t *out);

#ifdef __cplusplus
}
#endif
