/* Run the actual callback code with only the Wayland proxy destructor mocked. */
#define main benchmark_main
#include "../benchmarks/wayland_interaction/benchmark.c"
#undef main
#include <assert.h>

static unsigned destroyed;
static char proxy_token;
void wl_proxy_destroy(struct wl_proxy *proxy)
{
    assert(proxy == (struct wl_proxy *)&proxy_token);
    destroyed++;
}

static struct feedback_ctx *pending(struct app *app)
{
    struct feedback_ctx *ctx = calloc(1, sizeof(*ctx));
    assert(ctx);
    ctx->app = app;
    ctx->feedback = (struct wp_presentation_feedback *)&proxy_token;
    ctx->input_ns = ctx->work_start_ns = ctx->commit_ns = clock_ns(CLOCK_MONOTONIC) - 10000000;
    ctx->interaction_id = 1;
    return app->pending = ctx;
}

int main(void)
{
    struct app app = {.feedback_timeout_ms=1};
    struct feedback_ctx *ctx = pending(&app);
    uint64_t now = clock_ns(CLOCK_MONOTONIC);
    feedback_presented(ctx, ctx->feedback, 0, now / 1000000000, now % 1000000000,
                       16666666, 0, 1, 7);
    assert(!app.pending && app.completed_trials == 1 && destroyed == 1);
    ctx = pending(&app);
    feedback_discarded(ctx, ctx->feedback);
    assert(!app.pending && app.completed_trials == 2 && destroyed == 2);
    uint32_t state_data[] = {XDG_TOPLEVEL_STATE_ACTIVATED, XDG_TOPLEVEL_STATE_SUSPENDED};
    struct wl_array states = {.size=sizeof(state_data), .data=state_data};
    app.xdg_version = 6;
    toplevel_configure(&app, NULL, 400, 300, &states);
    assert(app.surface_activated && app.surface_suspended && app.surface_state_received_ns);
    pending(&app);
    maybe_timeout(&app);
    maybe_timeout(&app); /* must not revisit freed callback data */
    assert(!app.pending && app.completed_trials == 3 && destroyed == 3);
    states.size = 0;
    app.xdg_version = 5;
    toplevel_configure(&app, NULL, 400, 300, &states);
    assert(!app.surface_activated && !app.surface_suspended);

    app.pulse.skel = pulsesched__open(); /* open without loading; map fd is invalid */
    assert(app.pulse.skel);
    app.pulse.enabled = 1;
    ctx = pending(&app);
    feedback_presented(ctx, ctx->feedback, 0, now / 1000000000, now % 1000000000, 0, 0, 0, 0);
    assert(app.protocol_error && !app.pending && destroyed == 4);
    pulsesched__destroy(app.pulse.skel);

    app.buffers[0].busy = app.buffers[1].busy = 1;
    assert(!free_buffer(&app));
    buffer_release(&app.buffers[1], NULL);
    assert(free_buffer(&app) == &app.buffers[1]);
#ifdef PULSE_TRANSITION
    app.protocol_error = 0;
    pointer_button(&app, NULL, 0, 0, 0, WL_POINTER_BUTTON_STATE_PRESSED);
    assert(app.protocol_error);
    app.protocol_error = 0;
    keyboard_key(&app, NULL, 0, 0, 0, WL_KEYBOARD_KEY_STATE_PRESSED);
    assert(app.protocol_error);
#endif
    puts("feedback lifecycle tests passed: presented, discarded, timeout, observation error, buffer release");
    return 0;
}
