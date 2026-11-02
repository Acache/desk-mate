#ifndef DESKMATE_VOICE_CONTRACTS_H
#define DESKMATE_VOICE_CONTRACTS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Wire/audio constants shared by the FSM, ring and transport. */
#define VOICE_PCM_SAMPLE_RATE 16000
#define VOICE_PCM_CHANNELS 1
#define VOICE_PCM_BYTES_PER_MS 32  /* 16 kHz * 2 bytes / 1000 */
#define VOICE_FRAME_MS 20
#define VOICE_FRAME_BYTES 640
#define VOICE_RING_FRAMES 64
#define VOICE_RING_BYTES (VOICE_RING_FRAMES * VOICE_FRAME_BYTES)
#define VOICE_MAX_FRAMES 3000
#define VOICE_MAX_PCM_BYTES 1920000
#define VOICE_MAX_CAPTURE_MS 60000
#define VOICE_MIN_HOLD_MS 500
#define VOICE_QUALIFY_MS 530
#define VOICE_CANCEL_WINDOW_MS 800
#define VOICE_INPUT_UNCERTAINTY_MS 10
#define VOICE_COMMIT_GUARD_MS 30
#define VOICE_COMMIT_EARLIEST_MS (VOICE_CANCEL_WINDOW_MS + VOICE_INPUT_UNCERTAINTY_MS + VOICE_COMMIT_GUARD_MS)
#define VOICE_SEAL_TIMEOUT_MS 10000
#define VOICE_COMMIT_ACK_MS 2000
#define VOICE_STATUS_TOTAL_MS 120000
#define VOICE_RESULT_HOLD_MS 2000
#define VOICE_DEBOUNCE_MS 20
#define VOICE_PROTOCOL_VERSION 1
#define VOICE_MAX_CONTROL_BYTES 2048

typedef enum {
    VOICE_EV_NONE = 0,
    VOICE_EV_A_DOWN,
    VOICE_EV_A_UP,
    VOICE_EV_B_DOWN,
    VOICE_EV_B_UP,
    VOICE_EV_C_SHORT,
    VOICE_EV_TRANSPORT_READY,
    VOICE_EV_TRANSPORT_LOST,
    VOICE_EV_BEGIN_ACK,
    VOICE_EV_SEAL_ACK,
    VOICE_EV_COMMIT_ACK,
    VOICE_EV_CANCEL_ACK,
    VOICE_EV_REPLAY_READY,
    VOICE_EV_RESULT,
    VOICE_EV_ERROR,
    VOICE_EV_TICK,
    VOICE_EV_HOST_CHANGED
} voice_event_type_t;

typedef enum {
    VOICE_INPUT_KEY10 = 0,
    VOICE_INPUT_BOOT9 = 1,
    VOICE_INPUT_PMIC = 2,
    VOICE_INPUT_INTERNAL = 3
} voice_input_source_t;

typedef enum {
    VOICE_ACT_NONE = 0,
    VOICE_ACT_AUDIO_START,
    VOICE_ACT_AUDIO_STOP,
    VOICE_ACT_AUDIO_RESET,
    VOICE_ACT_BEGIN,
    VOICE_ACT_SEAL,
    VOICE_ACT_COMMIT,
    VOICE_ACT_CANCEL,
    VOICE_ACT_STATUS,
    VOICE_ACT_REPLAY_PREPARE,
    VOICE_ACT_REPLAY_COMMIT,
    VOICE_ACT_FEEDBACK
} voice_action_type_t;

typedef enum {
    VOICE_REASON_USER = 0,
    VOICE_REASON_SHORT_PRESS,
    VOICE_REASON_PAGE_EXIT,
    VOICE_REASON_TIMEOUT,
    VOICE_REASON_AUDIO_ERROR,
    VOICE_REASON_NETWORK_ERROR
} voice_cancel_reason_t;

typedef enum {
    VOICE_STATE_OFFLINE = 0,
    VOICE_STATE_CONNECTING,
    VOICE_STATE_READY,
    VOICE_STATE_ARMING,
    VOICE_STATE_RECORDING,
    VOICE_STATE_CANCEL_WINDOW,
    VOICE_STATE_COMMITTING,
    VOICE_STATE_WAITING,
    VOICE_STATE_QUERYING,
    VOICE_STATE_RESULT,
    VOICE_STATE_WAIT_RELEASE,
    VOICE_STATE_REPLAY_PREPARE,
    VOICE_STATE_REPLAY_ARMED,
    VOICE_STATE_OUTCOME_UNKNOWN_ACK
} voice_state_t;

typedef enum {
    VOICE_RESULT_NONE = 0,
    VOICE_RESULT_SUCCESS,
    VOICE_RESULT_PARTIAL,
    VOICE_RESULT_FAILED,
    VOICE_RESULT_CANCELED,
    VOICE_RESULT_UNKNOWN
} voice_result_t;

typedef struct {
    voice_event_type_t type;
    voice_input_source_t source;
    int64_t raw_us;
    int64_t stable_us;
    /* Optional response fields. */
    int code;
    voice_result_t result;
    bool replayable;
    char command_id[37];
    char sha256[65];
    char summary[129];
} voice_event_t;

typedef struct {
    voice_action_type_t type;
    voice_cancel_reason_t reason;
    char command_id[37];
    char replay_of[37];
    char sha256[65];
    char summary[129];
} voice_action_t;

typedef struct {
    voice_action_type_t type;
    char command_id[37];
    char replay_of[37];
} voice_commit_action_t;

typedef struct {
    void (*emit_action)(void *ctx, const voice_action_t *action);
    void (*emit_audio)(void *ctx, bool start);
    void (*uuid4)(void *ctx, char out[37]);
    void *ctx;
} voice_fsm_io_t;

typedef struct {
    voice_state_t state;
    voice_result_t result;
    int error_code;
    bool connected;
    bool begin_ack;
    bool seal_ack;
    bool begin_sent;
    bool seal_sent;
    int64_t down_us;
    int64_t release_us;
    int64_t commit_at_us;
    int64_t cancel_deadline_us;
    int64_t state_since_us;
    uint32_t command_id_counter;
    char command_id[37];
    char last_replay_id[37];
    char sha256[65];
    char summary[129];
    bool last_replayable;
    bool waiting_release;
    bool a_down;
    bool qualified;
    bool audio_active;
    bool seal_requested;
    bool release_before_qualify;
    bool short_press_pending;
    bool commit_sent;
    bool cancel_sent;
    bool replay_prepared;
    bool unknown_ack_required;
    int result_hold_until_us;
    int code;
    voice_fsm_io_t io;
} voice_fsm_t;

void voice_fsm_init(voice_fsm_t *fsm, const voice_fsm_io_t *io);
void voice_fsm_handle(voice_fsm_t *fsm, const voice_event_t *event, int64_t now_us);
void voice_fsm_tick(voice_fsm_t *fsm, int64_t now_us);
bool voice_fsm_is_capturing(const voice_fsm_t *fsm);
bool voice_fsm_should_commit(const voice_fsm_t *fsm);
void voice_fsm_uuid4_format(uint8_t bytes[16], char out[37]);

#ifdef __cplusplus
}
#endif

#endif
