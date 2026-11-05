#include <assert.h>
#include <atomic>
#include <stdio.h>
#include "../desk-mate-app/src/media_ble/ble_voice_subscription.h"
#include "../desk-mate-app/src/media_ble/voice_wire.h"

/* Exercise the subscription policy together with production wire assembly.
 * A companion can reconnect while the OS keeps the physical HID link alive. */
struct VoiceSession {
    std::atomic<uint32_t> subscribed{0};
    std::atomic<bool> reset{false};
    uint32_t epoch=100;
    BvAssembler assembly={};

    void notification_subscription(uint32_t generation, uint16_t cccd) {
        uint32_t next=(cccd&1)?generation:0;
        uint32_t previous=subscribed.exchange(next);
        if(bv_subscription_starts_session(previous,next))reset=true;
    }
    bool owner_poll() {
        if(!reset.exchange(false))return false;
        ++epoch;assembly={};return true;
    }
    int control(uint32_t sequence,uint32_t packet_epoch=0) {
        uint8_t packet[BV_HEADER+2];
        bv_header(packet,1,packet_epoch?packet_epoch:epoch,sequence,0,2,2);
        packet[BV_HEADER]='{';packet[BV_HEADER+1]='}';
        return bv_append(&assembly,packet,sizeof(packet),epoch);
    }
};

static void test_rearm_preserves_an_existing_stream() {
    VoiceSession s;s.notification_subscription(7,1);
    assert(s.owner_poll());assert(s.control(0)==1);
    uint32_t epoch=s.epoch;
    for(int i=0;i<4;i++)s.notification_subscription(7,1);
    assert(!s.owner_poll());assert(s.epoch==epoch);
    assert(s.control(1)==1&&s.assembly.next==2);
}

static void test_companion_resubscribe_starts_fresh_without_new_hid_generation() {
    VoiceSession s;s.notification_subscription(7,1);assert(s.owner_poll());
    assert(s.control(0)==1&&s.control(1)==1);
    uint32_t previous_epoch=s.epoch;
    s.notification_subscription(7,0);
    assert(!s.owner_poll());assert(s.assembly.next==2);
    /* Fresh host sequence zero is invalid if the old epoch survives. */
    assert(s.control(0)==-1);
    s.notification_subscription(7,1);assert(s.owner_poll());
    assert(s.epoch!=previous_epoch&&s.assembly.next==0);
    assert(s.control(0,previous_epoch)==-1);
    assert(s.control(0)==1);
}

static void test_new_physical_generation_starts_fresh_even_without_unsubscribe() {
    VoiceSession s;s.notification_subscription(7,1);assert(s.owner_poll());
    assert(s.control(0)==1);uint32_t epoch=s.epoch;
    s.notification_subscription(8,1);assert(s.owner_poll());
    assert(s.subscribed==8&&s.epoch!=epoch&&s.control(0)==1);
}

static void test_disable_and_resubscribe_and_pending_reset_are_not_lost() {
    VoiceSession s;
    s.notification_subscription(7,2); // Indications do not enable notifications.
    assert(!s.owner_poll()&&s.subscribed==0);
    s.notification_subscription(7,1);
    s.notification_subscription(7,1); // Rearm before owner consumes reset.
    assert(s.owner_poll());assert(!s.owner_poll());
    assert(s.control(0)==1);
    s.notification_subscription(7,0);
    s.notification_subscription(7,0);assert(!s.owner_poll());
    s.notification_subscription(7,1);
    s.notification_subscription(7,0); // Must not clear the pending new-session reset.
    assert(s.owner_poll()&&s.subscribed==0);
    s.notification_subscription(7,1);assert(s.owner_poll());assert(s.control(0)==1);
    s.notification_subscription(0,1); // Reserved generation cannot start a session.
    assert(!s.owner_poll()&&s.subscribed==0);
}

int main() {
    test_rearm_preserves_an_existing_stream();
    test_companion_resubscribe_starts_fresh_without_new_hid_generation();
    test_new_physical_generation_starts_fresh_even_without_unsubscribe();
    test_disable_and_resubscribe_and_pending_reset_are_not_lost();
    puts("BLE voice subscription: fresh companion epoch, rearm continuity, generation and disable PASS");
}
