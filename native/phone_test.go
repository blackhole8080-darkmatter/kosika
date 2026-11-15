package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// answers is a confirmer that gives set answers and remembers the questions.
type answers struct {
	said  []string
	asked []string
}

func (a *answers) confirm(question string, allowAlways bool) string {
	a.asked = append(a.asked, question)
	if len(a.said) == 0 {
		return "no"
	}
	next := a.said[0]
	a.said = a.said[1:]
	if next == "always" && !allowAlways {
		return "no"
	}
	return next
}

// twoDevices makes a phone node and a laptop, each with its own home.
func twoDevices(t *testing.T) (laptopHome, phoneHome string, node *phoneNode, server *httptest.Server, asker *answers) {
	t.Helper()
	laptopHome, phoneHome = t.TempDir(), t.TempDir()
	t.Setenv("KOSIKA_PHONE_HOME", phoneHome)
	me, err := loadIdentity("pixel")
	if err != nil {
		t.Fatal(err)
	}
	asker = &answers{}
	node = &phoneNode{me: me, asker: asker, simulate: true, say: func(string, ...any) {},
		code: newPairCode(), codeUntil: time.Now().Add(time.Minute)}
	// Each request runs with the phone's home; the test body uses the laptop's.
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		os.Setenv("KOSIKA_PHONE_HOME", phoneHome)
		defer os.Setenv("KOSIKA_PHONE_HOME", laptopHome)
		node.routes().ServeHTTP(w, r)
	}))
	t.Cleanup(server.Close)
	t.Setenv("KOSIKA_PHONE_HOME", laptopHome)
	return
}

func address(server *httptest.Server) string {
	return strings.TrimPrefix(server.URL, "http://")
}

func TestPhonePairAskDoAndLog(t *testing.T) {
	_, phoneHome, node, server, asker := twoDevices(t)
	if err := phonePair([]string{address(server), node.code}); err != nil {
		t.Fatal(err)
	}
	me, _ := loadIdentity("")
	peer, err := loadPeer("pixel")
	if err != nil {
		t.Fatal(err)
	}

	asker.said = []string{"yes"}
	reply, err := phoneSend(me, peer, phoneBody{Kind: "ask", What: "battery"})
	if err != nil || reply.Status != "done" || !reply.Simulated {
		t.Fatalf("battery: %v %+v", err, reply)
	}

	asker.said = []string{"no"}
	peer, _ = loadPeer("pixel")
	reply, _ = phoneSend(me, peer, phoneBody{Kind: "ask", What: "location"})
	if reply.Status != "refused" || reply.Simulated {
		t.Fatalf("a no must refuse, and a refusal is not simulated: %+v", reply)
	}

	// An action cannot be granted ahead: "always" counts as no.
	asker.said = []string{"always"}
	peer, _ = loadPeer("pixel")
	reply, _ = phoneSend(me, peer, phoneBody{Kind: "do", Action: "vibrate", Args: map[string]string{"ms": "200"}})
	if reply.Status != "refused" {
		t.Fatalf("always must not grant an action: %+v", reply)
	}

	// A sense can: asked once, then not again.
	asker.said = []string{"always"}
	for i := 0; i < 2; i++ {
		peer, _ = loadPeer("pixel")
		if reply, _ = phoneSend(me, peer, phoneBody{Kind: "ask", What: "wifi"}); reply.Status != "done" {
			t.Fatalf("wifi %d: %+v", i, reply)
		}
	}
	if len(asker.asked) != 4 {
		t.Fatalf("expected 4 questions, got %d: %v", len(asker.asked), asker.asked)
	}

	lines, _ := readLog("pixel")
	peer, _ = loadPeer("pixel")
	if err := checkLog(me, peer, lines); err != nil {
		t.Fatalf("laptop log: %v", err)
	}
	t.Setenv("KOSIKA_PHONE_HOME", phoneHome)
	phoneMe, _ := loadIdentity("")
	hub, err := loadPeer(peerNameOf(t, me))
	if err != nil {
		t.Fatal(err)
	}
	phoneLines, _ := readLog(hub.Name)
	if err := checkLog(phoneMe, hub, phoneLines); err != nil {
		t.Fatalf("phone log: %v", err)
	}
	if len(phoneLines) != len(lines) {
		t.Fatalf("both logs should hold the same %d lines, phone has %d", len(lines), len(phoneLines))
	}
}

func peerNameOf(t *testing.T, id phoneIdentity) string {
	return cleanName(id.Name)
}

func TestPhoneWrongCodeUsesItUp(t *testing.T) {
	_, _, node, server, _ := twoDevices(t)
	code := node.code
	if err := phonePair([]string{address(server), "AAAA-AAAA"}); err == nil {
		t.Fatal("a wrong code paired")
	}
	if err := phonePair([]string{address(server), code}); err == nil {
		t.Fatal("the right code should be used up after one wrong try")
	}
}

func TestPhoneRefusesReplayAndTampering(t *testing.T) {
	_, _, node, server, asker := twoDevices(t)
	if err := phonePair([]string{address(server), node.code}); err != nil {
		t.Fatal(err)
	}
	me, _ := loadIdentity("")
	peer, _ := loadPeer("pixel")
	envelope, _ := seal(me, peer, 1, phoneBody{Kind: "ping"})
	post := func(e phoneEnvelope) int {
		data, _ := json.Marshal(e)
		response, err := http.Post(server.URL+"/msg", "application/json", bytes.NewReader(data))
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		return response.StatusCode
	}
	if status := post(envelope); status != 200 {
		t.Fatalf("first ping: %d", status)
	}
	if status := post(envelope); status != 403 {
		t.Fatalf("a replayed message must be refused, got %d", status)
	}
	changed := envelope
	changed.Seq = 9
	if status := post(changed); status != 403 {
		t.Fatalf("a changed message must be refused, got %d", status)
	}

	// Changing a written log line is found, even with its hash made again.
	peer, _ = loadPeer("pixel")
	peer.SeqOut = 10
	asker.said = []string{"yes"}
	if _, err := phoneSend(me, peer, phoneBody{Kind: "ask", What: "battery"}); err != nil {
		t.Fatal(err)
	}
	peer, _ = loadPeer("pixel")
	lines, _ := readLog("pixel")
	if err := checkLog(me, peer, lines); err != nil {
		t.Fatalf("untouched log: %v", err)
	}
	lines[1].Body.What = "location"
	if err := checkLog(me, peer, lines); err == nil {
		t.Fatal("a changed log line was not found")
	}
	lines[1].Hash = lineHash(lines[1])
	lines[2].Prev = lines[1].Hash
	lines[2].Hash = lineHash(lines[2])
	if err := checkLog(me, peer, lines); err == nil || !strings.Contains(err.Error(), "says something its message does not") {
		t.Fatalf("a rewritten chain must still be caught by the signed message: %v", err)
	}
	if err := checkLog(me, peer, lines); err == nil {
		t.Fatal("a changed log line was not found")
	}
}

func TestPhoneChecksArguments(t *testing.T) {
	if _, err := phoneDoBody([]string{"pixel", "open-url", "file:///etc/passwd"}); err == nil {
		t.Fatal("file: URL accepted")
	}
	if _, err := phoneDoBody([]string{"pixel", "vibrate", "999999"}); err == nil {
		t.Fatal("huge vibrate accepted")
	}
	if _, err := phoneDoBody([]string{"pixel", "shell", "rm -rf /"}); err == nil {
		t.Fatal("unknown action accepted")
	}
	if _, argv, err := adbPlan([]string{"tap", "540", "1200"}); err != nil || strings.Join(argv, " ") != "shell input tap 540 1200" {
		t.Fatalf("tap: %v %v", argv, err)
	}
	if _, _, err := adbPlan([]string{"tap", "1;reboot", "2"}); err == nil {
		t.Fatal("a tap with a command in it was accepted")
	}
	if _, _, err := adbPlan([]string{"open", "com.x; reboot"}); err == nil {
		t.Fatal("a bad package was accepted")
	}
	if got := adbText("hi there; rm"); got != `hi%sthere\;%srm` {
		t.Fatalf("adbText: %q", got)
	}
}
