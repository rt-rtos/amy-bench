#include "metrics.h"

#include <stdlib.h>

static int cmp_u32(const void *a, const void *b)
{
    const uint32_t ua = *(const uint32_t *)a;
    const uint32_t ub = *(const uint32_t *)b;
    if (ua < ub) return -1;
    if (ua > ub) return 1;
    return 0;
}

void bench_stats_compute(uint32_t *samples, uint32_t n, bench_stats_t *out)
{
    qsort(samples, n, sizeof(uint32_t), cmp_u32);

    uint64_t sum = 0;
    for (uint32_t i = 0; i < n; i++) {
        sum += samples[i];
    }

    out->min = samples[0];
    out->max = samples[n - 1];
    out->mean = (uint32_t)(sum / n);
    out->median = samples[n / 2];
    // Index clamped so small series still return a defined value.
    uint32_t p99_idx = (uint32_t)(((uint64_t)n * 99) / 100);
    if (p99_idx >= n) p99_idx = n - 1;
    out->p99 = samples[p99_idx];
}
