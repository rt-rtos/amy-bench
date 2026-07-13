#include "scenes.h"

#include <stddef.h>

/*
 * Wave numbers: SINE 0, PULSE 1, SAW_DOWN 2, TRIANGLE 4, PCM 7 (amy.h).
 * Filters: LPF 1, BPF 2, HPF 3, LPF24 4. FX: h = reverb, k = chorus.
 * Messages end with the wire terminator 'Z'. All events are immediate (no
 * 't' timestamp): rendering is driven by the bench loop, so scenes are
 * deterministic regardless of wall-clock pacing.
 */

// Reset all oscillators between scenes.
static const char *const reset_teardown[] = {
    "S8192Z",
    NULL,
};

// --- idle: empty synth, measures the fixed per-block overhead ---------------

// --- sine8: 8 sustained sine oscillators ------------------------------------
static const char *const sine8_setup[] = {
    "v0w0f110l0.35Z",
    "v1w0f138.59l0.35Z",
    "v2w0f164.81l0.35Z",
    "v3w0f220l0.35Z",
    "v4w0f277.18l0.35Z",
    "v5w0f329.63l0.35Z",
    "v6w0f440l0.35Z",
    "v7w0f554.37l0.35Z",
    NULL,
};

// --- saw_lpf6: 6 saws through a 24 dB LPF with an envelope sweep ------------
// Filter freq coefs: const, note, vel, eg0, eg1 - eg0 sweeps 3 octaves over
// the breakpoint envelope. Re-triggered periodically so coefficient updates
// keep occurring throughout the measurement window.
static const char *const saw_lpf6_setup[] = {
    "v8w2f55G4F200,0,0,0,3B0,1,500,0,200,0l0.3Z",
    "v9w2f82.41G4F200,0,0,0,3B0,1,500,0,200,0l0.3Z",
    "v10w2f110G4F200,0,0,0,3B0,1,500,0,200,0l0.3Z",
    "v11w2f164.81G4F200,0,0,0,3B0,1,500,0,200,0l0.3Z",
    "v12w2f220G4F200,0,0,0,3B0,1,500,0,200,0l0.3Z",
    "v13w2f329.63G4F200,0,0,0,3B0,1,500,0,200,0l0.3Z",
    NULL,
};
static const char *const saw_lpf6_tick[] = {
    "v8l0.3Z", "v9l0.3Z", "v10l0.3Z", "v11l0.3Z", "v12l0.3Z", "v13l0.3Z",
    NULL,
};

// --- juno6 / dx76: patch-based polyphony via the synth API -------------------
// i<synth> iv<voices> K<patch>: Juno-6 patches are 0-127, DX7 128-255.
static const char *const juno6_setup[] = {
    "i1iv6K1Z",
    "i1n48l1Z",
    "i1n55l1Z",
    "i1n60l1Z",
    "i1n64l1Z",
    "i1n67l1Z",
    "i1n72l1Z",
    NULL,
};
static const char *const juno6_teardown[] = {
    "i1n48l0Z", "i1n55l0Z", "i1n60l0Z", "i1n64l0Z", "i1n67l0Z", "i1n72l0Z",
    "S8192Z",
    NULL,
};

static const char *const dx76_setup[] = {
    "i2iv6K149Z",
    "i2n40l1Z",
    "i2n47l1Z",
    "i2n52l1Z",
    "i2n56l1Z",
    "i2n59l1Z",
    "i2n64l1Z",
    NULL,
};
static const char *const dx76_teardown[] = {
    "i2n40l0Z", "i2n47l0Z", "i2n52l0Z", "i2n56l0Z", "i2n59l0Z", "i2n64l0Z",
    "S8192Z",
    NULL,
};

// --- fx_sine8: the sine8 material with reverb + chorus enabled ---------------
// Renders different audio on each pass, by design of AMY rather than of this
// scene: the reverb/chorus delay lines and filter states are zeroed only at
// allocation, and the teardown below can silence the effects but not drain them
// (see "Known AMY gap" in bench/README.md). Each pass is still bit-identical
// across boots, so abcompare.py lines the passes up and the A/B output check
// holds. Timing is unaffected. Do not "fix" this by dropping the scene.
static const char *const fx_sine8_setup[] = {
    "h1Z",
    "k0.5Z",
    "v0w0f110l0.35Z",
    "v1w0f138.59l0.35Z",
    "v2w0f164.81l0.35Z",
    "v3w0f220l0.35Z",
    "v4w0f277.18l0.35Z",
    "v5w0f329.63l0.35Z",
    "v6w0f440l0.35Z",
    "v7w0f554.37l0.35Z",
    NULL,
};
static const char *const fx_sine8_teardown[] = {
    "h0Z",
    "k0Z",
    "S8192Z",
    NULL,
};

// --- pcm4: 4 PCM one-shots, re-triggered every ~quarter second ---------------
static const char *const pcm4_setup[] = {
    "v20w7p1l1Z",
    "v21w7p5l1Z",
    "v22w7p8l1Z",
    "v23w7p10l1Z",
    NULL,
};
static const char *const pcm4_tick[] = {
    "v20l1Z", "v21l1Z", "v22l1Z", "v23l1Z",
    NULL,
};

const bench_scene_t bench_scenes[] = {
    { .name = "idle",     .setup = NULL,          .teardown = NULL,
      .tick = NULL,          .tick_period_blocks = 0,
      .warmup_blocks = 50,   .measure_blocks = 500 },
    { .name = "sine8",    .setup = sine8_setup,   .teardown = reset_teardown,
      .tick = NULL,          .tick_period_blocks = 0,
      .warmup_blocks = 100,  .measure_blocks = 500 },
    { .name = "saw_lpf6", .setup = saw_lpf6_setup, .teardown = reset_teardown,
      .tick = saw_lpf6_tick, .tick_period_blocks = 94,
      .warmup_blocks = 100,  .measure_blocks = 500 },
    { .name = "juno6",    .setup = juno6_setup,   .teardown = juno6_teardown,
      .tick = NULL,          .tick_period_blocks = 0,
      .warmup_blocks = 150,  .measure_blocks = 500 },
    { .name = "dx76",     .setup = dx76_setup,    .teardown = dx76_teardown,
      .tick = NULL,          .tick_period_blocks = 0,
      .warmup_blocks = 150,  .measure_blocks = 500 },
    { .name = "fx_sine8", .setup = fx_sine8_setup, .teardown = fx_sine8_teardown,
      .tick = NULL,          .tick_period_blocks = 0,
      .warmup_blocks = 100,  .measure_blocks = 500 },
    { .name = "pcm4",     .setup = pcm4_setup,    .teardown = reset_teardown,
      .tick = pcm4_tick,     .tick_period_blocks = 43,
      .warmup_blocks = 50,   .measure_blocks = 500 },
};

const int bench_num_scenes = sizeof(bench_scenes) / sizeof(bench_scenes[0]);
