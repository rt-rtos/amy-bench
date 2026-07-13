#include "render_clock.h"

#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/gptimer.h"
#include "esp_attr.h"
#include "esp_log.h"

static const char *TAG = "render_clock";

static gptimer_handle_t s_timer = NULL;
static TaskHandle_t s_render_task = NULL;

// GPTimer alarm ISR. Runs on the core that called gptimer_enable() (i.e. the
// bench task's core), so the notify + wake stay core-local. Kept in IRAM and
// minimal: just a counting task notification.
static bool IRAM_ATTR render_clock_on_alarm(gptimer_handle_t timer,
                                            const gptimer_alarm_event_data_t *edata,
                                            void *user_ctx)
{
    (void)timer;
    (void)edata;
    (void)user_ctx;
    BaseType_t higher_prio_woken = pdFALSE;
    // Counting give: each missed/queued tick increments the notification value,
    // so render_clock_wait() can report a backlog (>1) as an overrun signal.
    vTaskNotifyGiveFromISR(s_render_task, &higher_prio_woken);
    return higher_prio_woken == pdTRUE;
}

esp_err_t render_clock_start(uint32_t block_frames, uint32_t sample_rate_hz)
{
    if (s_timer != NULL) {
        return ESP_OK;  // already started
    }

    // 3 MHz is the requested resolution: it would give an exact tick count for
    // 256 frames @ 48 kHz (256 * 3,000,000 / 48,000 = 16,000, zero remainder),
    // vs. 1 MHz's non-integer 5333.33 ticks. The GPTimer clock divider is an
    // integer, though, so the requested rate isn't always exactly achievable
    // (e.g. 80 MHz APB / 26 = 3,076,923 Hz, ~2.56% off 3 MHz on the S3) - query
    // the resolution actually granted and derive period_ticks from that, or
    // the alarm period silently drifts from the real block period.
    s_render_task = xTaskGetCurrentTaskHandle();

    const gptimer_config_t timer_config = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = 3 * 1000 * 1000,
        .intr_priority = 0,
    };
    esp_err_t err = gptimer_new_timer(&timer_config, &s_timer);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "gptimer_new_timer failed: %s", esp_err_to_name(err));
        s_timer = NULL;
        return err;
    }

    uint32_t real_resolution_hz = timer_config.resolution_hz;
    err = gptimer_get_resolution(s_timer, &real_resolution_hz);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "gptimer_get_resolution failed: %s", esp_err_to_name(err));
        goto fail;
    }
    if (real_resolution_hz != timer_config.resolution_hz) {
        ESP_LOGW(TAG, "requested %" PRIu32 " Hz, granted %" PRIu32 " Hz - "
                 "deriving period from the granted rate",
                 timer_config.resolution_hz, real_resolution_hz);
    }
    const uint32_t period_ticks =
        (uint32_t)(((uint64_t)block_frames * real_resolution_hz) / (uint64_t)sample_rate_hz);

    const gptimer_event_callbacks_t cbs = {
        .on_alarm = render_clock_on_alarm,
    };
    err = gptimer_register_event_callbacks(s_timer, &cbs, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "gptimer_register_event_callbacks failed: %s", esp_err_to_name(err));
        goto fail;
    }

    // Enabling from the bench task's context registers the ISR on this core.
    err = gptimer_enable(s_timer);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "gptimer_enable failed: %s", esp_err_to_name(err));
        goto fail;
    }

    const gptimer_alarm_config_t alarm_config = {
        .alarm_count = period_ticks,
        .reload_count = 0,
        .flags.auto_reload_on_alarm = true,
    };
    err = gptimer_set_alarm_action(s_timer, &alarm_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "gptimer_set_alarm_action failed: %s", esp_err_to_name(err));
        gptimer_disable(s_timer);
        goto fail;
    }

    err = gptimer_start(s_timer);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "gptimer_start failed: %s", esp_err_to_name(err));
        gptimer_disable(s_timer);
        goto fail;
    }

    ESP_LOGI(TAG, "render clock started: %u ticks period (%" PRIu32 " Hz) on core %d",
             (unsigned)period_ticks, real_resolution_hz, xPortGetCoreID());
    return ESP_OK;

fail:
    gptimer_del_timer(s_timer);
    s_timer = NULL;
    return err;
}

uint32_t render_clock_wait(void)
{
    // Block until >=1 tick, return the accumulated count and clear it to 0.
    return ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
}

void render_clock_stop(void)
{
    if (s_timer == NULL) {
        return;
    }
    gptimer_stop(s_timer);
    gptimer_disable(s_timer);
    gptimer_del_timer(s_timer);
    s_timer = NULL;
    s_render_task = NULL;
}
