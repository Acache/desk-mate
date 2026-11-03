/* Exhaustive native test of the voice page state machine (no hardware).
 *
 * Every state, transition and timeout that the ESP32 runs on screen 3 is
 * exercised here with a fake clock and fake IO. The firmware FSM is the same
 * source file compiled for the device.
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../desk-mate-app/src/bili_voice/voice_contracts.h"

/* Implemented in voice_fsm.c (compiled into this target). */
void voice_fsm_init(voice_fsm_t *fsm, const voice_fsm_io_t *io);
void voice_fsm_handle(voice_fsm_t *fsm, const voice_event_t *event, int64_t now_us);
void voice_fsm_tick(voice_fsm_t *fsm, int64_t now_us);
bool voice_fsm_is_capturing(const voice_fsm_t *fsm);

static voice_fsm_t g_fsm;
static int64_t g_now_us;
static voice_action_t g_last;
static int g_actions;
static bool g_audio;
static const char *g_uuid = "11111111-2222-4333-8444-555555555555";

static void on_action(void *ctx, const voice_action_t *action) {
    (void)ctx;
    g_last = *action;
    g_actions++;
}
static void on_audio(void *ctx, bool start) { (void)ctx; g_audio = start; }
static void on_uuid(void *ctx, char out[37]) {
    (void)ctx;
    snprintf(out, 37, "%s", g_uuid);
}

static void reset(void) {
    voice_fsm_io_t io;
    memset(&io, 0, sizeof(io));
    io.emit_action = on_action;
    io.emit_audio = on_audio;
    io.uuid4 = on_uuid;
    voice_fsm_init(&g_fsm, &io);
    g_now_us = 0;
    memset(&g_last, 0, sizeof(g_last));
    g_actions = 0;
    g_audio = false;
}

static void at(int64_t ms) { g_now_us = ms * 1000; }
static void tick_to(int64_t ms) {
    g_now_us = ms * 1000;
    voice_fsm_tick(&g_fsm, g_now_us);
}
static void event(voice_event_type_t type) {
    voice_event_t ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = type;
    ev.raw_us = g_now_us;
    voice_fsm_handle(&g_fsm, &ev, g_now_us);
}
static void result(voice_result_t r, bool replayable) {
    voice_event_t ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = VOICE_EV_RESULT;
    ev.raw_us = g_now_us;
    ev.result = r;
    ev.replayable = replayable;
    snprintf(ev.command_id, sizeof(ev.command_id), "%s", g_fsm.command_id);
    snprintf(ev.summary, sizeof(ev.summary), "test");
    voice_fsm_handle(&g_fsm, &ev, g_now_us);
}
static void ready(void) {
    reset();
    event(VOICE_EV_TRANSPORT_READY);
    assert(g_fsm.state == VOICE_STATE_READY);
    assert(g_fsm.connected);
}

/* Press, qualify, release and commit handshake shared by several scenarios. */
static void start_and_release(int64_t start_ms, int64_t hold_ms) {
    at(start_ms);
    event(VOICE_EV_A_DOWN);
    assert(g_fsm.state == VOICE_STATE_ARMING);
    assert(g_audio);
    tick_to(start_ms + VOICE_QUALIFY_MS);
    assert(g_fsm.state == VOICE_STATE_RECORDING);
    assert(g_last.type == VOICE_ACT_BEGIN);
    at(start_ms + VOICE_QUALIFY_MS + hold_ms);
    event(VOICE_EV_A_UP);
    assert(g_fsm.state == VOICE_STATE_CANCEL_WINDOW);
    assert(!g_audio);
    assert(voice_fsm_is_capturing(&g_fsm) == true); /* cancel window still owns input */
}

static void to_committing(int64_t start_ms, int64_t hold_ms) {
    start_and_release(start_ms, hold_ms);
    event(VOICE_EV_BEGIN_ACK);
    assert(g_last.type == VOICE_ACT_SEAL);
    event(VOICE_EV_SEAL_ACK);
    assert(g_fsm.sha256[0] == 0); /* set by the transport before the event */
    int64_t release_ms = start_ms + VOICE_QUALIFY_MS + hold_ms;
    tick_to(release_ms + VOICE_COMMIT_EARLIEST_MS);
    assert(g_fsm.state == VOICE_STATE_COMMITTING);
    assert(g_last.type == VOICE_ACT_COMMIT);
}

static void test_happy_path(void) {
    ready();
    to_committing(1000, 800);
    event(VOICE_EV_COMMIT_ACK);
    assert(g_fsm.state == VOICE_STATE_WAITING);
    at(4000);
    result(VOICE_RESULT_SUCCESS, true);
    assert(g_fsm.state == VOICE_STATE_RESULT);
    assert(g_fsm.result == VOICE_RESULT_SUCCESS);
    assert(g_fsm.last_replayable);
    assert(strcmp(g_fsm.last_replay_id, g_fsm.command_id) == 0);
    tick_to(4000 + VOICE_RESULT_HOLD_MS);
    assert(g_fsm.state == VOICE_STATE_READY);
    assert(g_fsm.result == VOICE_RESULT_NONE);
}

static void test_short_press_never_sends(void) {
    ready();
    at(1000);
    event(VOICE_EV_A_DOWN);
    at(1400); /* < VOICE_MIN_HOLD_MS */
    event(VOICE_EV_A_UP);
    assert(g_fsm.state == VOICE_STATE_READY);
    assert(g_last.type == VOICE_ACT_AUDIO_RESET);
    assert(g_last.reason == VOICE_REASON_SHORT_PRESS);
}

static void test_release_between_min_and_qualify(void) {
    ready();
    at(1000);
    event(VOICE_EV_A_DOWN);
    at(1000 + 510); /* >= MIN_HOLD, < QUALIFY */
    event(VOICE_EV_A_UP);
    assert(g_fsm.state == VOICE_STATE_ARMING);
    assert(g_fsm.release_before_qualify);
    tick_to(1000 + VOICE_QUALIFY_MS);
    assert(g_fsm.state == VOICE_STATE_CANCEL_WINDOW);
    assert(g_fsm.seal_requested);
    assert(g_fsm.begin_sent);
}

static void test_second_key_cancels_within_window(void) {
    ready();
    start_and_release(1000, 700);
    event(VOICE_EV_BEGIN_ACK);
    at(1000 + VOICE_QUALIFY_MS + 700 + 400); /* inside the 810 ms window */
    event(VOICE_EV_A_DOWN);
    assert(g_fsm.state == VOICE_STATE_WAIT_RELEASE);
    assert(g_last.type == VOICE_ACT_CANCEL);
    assert(g_fsm.result == VOICE_RESULT_CANCELED);
    at(g_now_us / 1000 + 10);
    event(VOICE_EV_A_UP);
    assert(g_fsm.state == VOICE_STATE_READY);
}

static void test_missing_commit_ack_queries_then_succeeds(void) {
    ready();
    to_committing(1000, 800);
    int before = g_actions;
    tick_to(g_now_us / 1000 + VOICE_COMMIT_ACK_MS - 1);
    assert(g_fsm.state == VOICE_STATE_COMMITTING);
    assert(g_actions == before);
    tick_to(g_now_us / 1000 + 2);
    assert(g_fsm.state == VOICE_STATE_QUERYING);
    assert(g_last.type == VOICE_ACT_STATUS);
    at(g_now_us / 1000 + 50);
    result(VOICE_RESULT_SUCCESS, false);
    assert(g_fsm.state == VOICE_STATE_RESULT);
}

static void test_query_timeout_becomes_unknown(void) {
    ready();
    to_committing(1000, 800);
    tick_to(g_now_us / 1000 + VOICE_COMMIT_ACK_MS + 1);
    assert(g_fsm.state == VOICE_STATE_QUERYING);
    tick_to(g_now_us / 1000 + VOICE_STATUS_TOTAL_MS + 1);
    assert(g_fsm.state == VOICE_STATE_OUTCOME_UNKNOWN_ACK);
    assert(g_fsm.result == VOICE_RESULT_UNKNOWN);
    at(g_now_us / 1000 + 1);
    event(VOICE_EV_A_DOWN);
    assert(g_fsm.state == VOICE_STATE_READY);
}

static void test_replay_flow(void) {
    ready();
    to_committing(1000, 800);
    event(VOICE_EV_COMMIT_ACK);
    result(VOICE_RESULT_SUCCESS, true);
    assert(g_fsm.state == VOICE_STATE_RESULT);
    at(g_now_us / 1000 + VOICE_RESULT_HOLD_MS + 1);
    tick_to(g_now_us / 1000);
    assert(g_fsm.state == VOICE_STATE_READY);
    event(VOICE_EV_B_DOWN);
    assert(g_fsm.state == VOICE_STATE_REPLAY_PREPARE);
    assert(g_last.type == VOICE_ACT_REPLAY_PREPARE);
    event(VOICE_EV_REPLAY_READY);
    assert(g_fsm.state == VOICE_STATE_REPLAY_ARMED);
    event(VOICE_EV_B_DOWN);
    assert(g_fsm.state == VOICE_STATE_COMMITTING);
    assert(g_last.type == VOICE_ACT_REPLAY_COMMIT);
    event(VOICE_EV_COMMIT_ACK);
    assert(g_fsm.state == VOICE_STATE_WAITING);
    result(VOICE_RESULT_SUCCESS, true);
    assert(g_fsm.state == VOICE_STATE_RESULT);
}

static void test_replay_requires_success(void) {
    ready();
    to_committing(1000, 800);
    event(VOICE_EV_COMMIT_ACK);
    result(VOICE_RESULT_FAILED, false);
    at(g_now_us / 1000 + VOICE_RESULT_HOLD_MS + 1);
    tick_to(g_now_us / 1000);
    assert(g_fsm.state == VOICE_STATE_READY);
    event(VOICE_EV_B_DOWN);
    assert(g_fsm.state == VOICE_STATE_READY); /* nothing to replay */
}

static void test_transport_lost_while_recording(void) {
    ready();
    at(1000);
    event(VOICE_EV_A_DOWN);
    tick_to(1000 + VOICE_QUALIFY_MS);
    assert(g_fsm.state == VOICE_STATE_RECORDING);
    event(VOICE_EV_TRANSPORT_LOST);
    assert(!g_fsm.connected);
    assert(g_fsm.result == VOICE_RESULT_FAILED || g_fsm.state == VOICE_STATE_WAIT_RELEASE);
    at(g_now_us / 1000 + 10);
    event(VOICE_EV_A_UP);
    assert(g_fsm.state == VOICE_STATE_READY);
}

static void test_new_ptt_from_result_is_immediate(void) {
    ready();
    to_committing(1000, 800);
    event(VOICE_EV_COMMIT_ACK);
    result(VOICE_RESULT_SUCCESS, false);
    assert(g_fsm.state == VOICE_STATE_RESULT);
    at(g_now_us / 1000 + 10); /* well before the 2 s hold */
    event(VOICE_EV_A_DOWN);
    assert(g_fsm.state == VOICE_STATE_ARMING);
    assert(g_fsm.result == VOICE_RESULT_NONE);
}

static void test_key_ignored_while_waiting(void) {
    ready();
    to_committing(1000, 800);
    event(VOICE_EV_COMMIT_ACK);
    assert(g_fsm.state == VOICE_STATE_WAITING);
    at(g_now_us / 1000 + 10);
    event(VOICE_EV_A_DOWN);
    assert(g_fsm.state == VOICE_STATE_WAITING);
    event(VOICE_EV_A_UP);
    assert(g_fsm.state == VOICE_STATE_WAITING);
}

static void test_max_capture_times_out(void) {
    ready();
    at(1000);
    event(VOICE_EV_A_DOWN);
    tick_to(1000 + VOICE_QUALIFY_MS);
    tick_to(1000 + VOICE_MAX_CAPTURE_MS + 1);
    assert(g_fsm.result == VOICE_RESULT_FAILED);
    assert(g_last.type == VOICE_ACT_CANCEL);
}

static void test_page_exit_cancels_capture(void) {
    ready();
    at(1000);
    event(VOICE_EV_A_DOWN);
    tick_to(1000 + VOICE_QUALIFY_MS);
    event(VOICE_EV_C_SHORT);
    assert(g_last.type == VOICE_ACT_CANCEL);
    assert(g_fsm.result == VOICE_RESULT_CANCELED);
}

int main(void) {
    test_happy_path();
    test_short_press_never_sends();
    test_release_between_min_and_qualify();
    test_second_key_cancels_within_window();
    test_missing_commit_ack_queries_then_succeeds();
    test_query_timeout_becomes_unknown();
    test_replay_flow();
    test_replay_requires_success();
    test_transport_lost_while_recording();
    test_new_ptt_from_result_is_immediate();
    test_key_ignored_while_waiting();
    test_max_capture_times_out();
    test_page_exit_cancels_capture();
    puts("voice_fsm: all state/timing scenarios passed");
    return 0;
}
