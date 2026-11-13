package main

import (
	"encoding/json"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestRelayAddresses(t *testing.T) {
	t.Setenv("KOSIKA_RELAY", "")
	base, topic, err := parseRelayAddress("relay:kos-abcdefghijklmnopqrstuvwxyz")
	if err != nil || base != defaultRelay || topic != "kos-abcdefghijklmnopqrstuvwxyz" {
		t.Fatalf("short form: %q %q %v", base, topic, err)
	}
	base, topic, err = parseRelayAddress("relay:http://10.0.0.5:8795/kos-abcdefghijklmnopqrstuvwxyz")
	if err != nil || base != "http://10.0.0.5:8795" || topic != "kos-abcdefghijklmnopqrstuvwxyz" {
		t.Fatalf("long form: %q %q %v", base, topic, err)
	}
	if relayAddressOf(base, topic) != "relay:http://10.0.0.5:8795/kos-abcdefghijklmnopqrstuvwxyz" {
		t.Fatal("address does not round-trip")
	}
	for _, bad := range []string{"relay:short", "relay:https://ntfy.sh/../etc", "relay:kos-has spaces in it ok"} {
		if _, _, err := parseRelayAddress(bad); err == nil {
			t.Fatalf("%q accepted", bad)
		}
	}
	if a, b := newTopic(), newTopic(); a == b || !topicShape.MatchString(a) {
		t.Fatalf("mailbox names: %q %q", a, b)
	}
}

func TestRelayServerKeepsOrderAndSince(t *testing.T) {
	server := httptest.NewServer(newRelayServer())
	defer server.Close()
	topic := newTopic()
	start := strconv.FormatInt(time.Now().Unix(), 10)
	for i := 0; i < 3; i++ {
		data, _ := json.Marshal(map[string]int{"n": i})
		if err := relayPublish(server.URL, topic, relayFrame{Kind: "req", ID: strconv.Itoa(i), Reply: newTopic(), Path: "/msg", Data: data}); err != nil {
			t.Fatal(err)
		}
	}
	frames, since, err := relayPoll(server.URL, topic, start)
	if err != nil || len(frames) != 3 || frames[0].ID != "0" || frames[2].ID != "2" {
		t.Fatalf("poll: %v %+v", err, frames)
	}
	frames, _, _ = relayPoll(server.URL, topic, since)
	if len(frames) != 0 {
		t.Fatalf("since the last id there should be nothing new, got %d", len(frames))
	}
	other, _, _ := relayPoll(server.URL, newTopic(), "all")
	if len(other) != 0 {
		t.Fatal("another mailbox saw these messages")
	}
	huge := relayFrame{Kind: "req", ID: "x", Reply: newTopic(), Data: json.RawMessage(`"` + strings.Repeat("a", 5000) + `"`)}
	if err := relayPublish(server.URL, topic, huge); err == nil {
		t.Fatal("a frame too big for ntfy was sent")
	}
}

// A node on the relay answers by the same handlers as on the Wi-Fi, and
// ignores what is not a request for it.
func TestRelayNodeAnswersThroughMailboxes(t *testing.T) {
	t.Setenv("KOSIKA_PHONE_HOME", t.TempDir())
	relayPollEvery = 50 * time.Millisecond
	defer func() { relayPollEvery = 2 * time.Second }()
	server := httptest.NewServer(newRelayServer())
	defer server.Close()
	me, _ := loadIdentity("iphone")
	node := &phoneNode{me: me, asker: &answers{}, say: func(string, ...any) {}, code: newPairCode(), codeUntil: time.Now().Add(time.Minute)}
	topic, inbox := newTopic(), newTopic()
	stop := make(chan struct{})
	defer close(stop)
	go node.serveRelay(server.URL, topic, stop)
	time.Sleep(100 * time.Millisecond)

	share, _ := newShare()
	hello, _ := json.Marshal(pairHello{Name: "laptop", Public: b64.EncodeToString(me.public()), Share: b64.EncodeToString(share.PublicKey().Bytes()), Nonce: "n1"})
	_ = relayPublish(server.URL, topic, relayFrame{Kind: "req", ID: "a1", Reply: inbox, Path: "/pair/hello", Data: hello})
	_ = relayPublish(server.URL, topic, relayFrame{Kind: "req", ID: "a2", Reply: inbox, Path: "/elsewhere", Data: hello})
	_ = relayPublish(server.URL, topic, relayFrame{Kind: "req", ID: "a3", Reply: "../bad", Path: "/pair/hello", Data: hello})
	deadline := time.Now().Add(3 * time.Second)
	var got []relayFrame
	for time.Now().Before(deadline) && len(got) == 0 {
		got, _, _ = relayPoll(server.URL, inbox, "all")
		time.Sleep(50 * time.Millisecond)
	}
	time.Sleep(200 * time.Millisecond)
	got, _, _ = relayPoll(server.URL, inbox, "all")
	if len(got) != 1 || got[0].ID != "a1" || got[0].Status != 200 {
		t.Fatalf("expected one answer, to a1: %+v", got)
	}
	var answer pairHello
	if err := json.Unmarshal(got[0].Data, &answer); err != nil || answer.Name != "iphone" || answer.Share == "" {
		t.Fatalf("answer: %v %+v", err, answer)
	}
}
