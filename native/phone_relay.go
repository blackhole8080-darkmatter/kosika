package main

// The relay: so a phone can be reached from anywhere.
//
// A phone on mobile data or someone else's Wi-Fi cannot be called; it can
// only call out. So with `phone node --relay` it calls out to a mailbox,
// a relay, and picks up what was left there for it; replies go back to the
// asking device's own mailbox the same way. The relay speaks the protocol
// of ntfy (https://ntfy.sh, free, no account), so the public ntfy.sh works,
// and so does any ntfy server, or `kosika-native phone relay` run anywhere.
//
// The relay is trusted with nothing. What it carries are the same signed,
// sealed envelopes as on the Wi-Fi, and the same pairing (a wrong code is
// used up); it sees only which mailbox a blob went to, and when. A mailbox
// name is long and random, and a message nobody signed is refused.

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

const defaultRelay = "https://ntfy.sh"

// relayFrameLimit keeps a frame under ntfy's 4,096-byte message size, past
// which ntfy would turn it into an attachment.
const relayFrameLimit = 3900

var topicShape = regexp.MustCompile(`^[A-Za-z0-9_-]{16,64}$`)

func relayBase() string {
	if base := os.Getenv("KOSIKA_RELAY"); base != "" {
		return strings.TrimRight(base, "/")
	}
	return defaultRelay
}

// newTopic is a mailbox name: 18 random letters and digits after "kos-",
// about 93 bits, so it cannot be guessed, yet short enough to type.
func newTopic() string {
	const letters = "abcdefghijklmnopqrstuvwxyz0123456789"
	raw := make([]byte, 18)
	if _, err := randRead(raw); err != nil {
		panic(err)
	}
	out := []byte("kos-")
	for _, b := range raw {
		out = append(out, letters[int(b)%len(letters)])
	}
	return string(out)
}

// relayAddress is how a device reached through a relay is written:
// relay:https://ntfy.sh/kos-... (or relay:kos-... for the default relay).
func parseRelayAddress(address string) (base, topic string, err error) {
	rest := strings.TrimPrefix(address, "relay:")
	if strings.HasPrefix(rest, "http://") || strings.HasPrefix(rest, "https://") {
		parsed, perr := url.Parse(rest)
		if perr != nil {
			return "", "", fmt.Errorf("%q is not a relay address", address)
		}
		cut := strings.LastIndex(parsed.Path, "/")
		topic = parsed.Path[cut+1:]
		parsed.Path = strings.TrimRight(parsed.Path[:cut], "/")
		base = strings.TrimRight(parsed.String(), "/")
	} else {
		base, topic = relayBase(), rest
	}
	if !topicShape.MatchString(topic) {
		return "", "", fmt.Errorf("%q is not a relay mailbox name", topic)
	}
	return base, topic, nil
}

func relayAddressOf(base, topic string) string {
	if base == defaultRelay {
		return "relay:" + topic
	}
	return "relay:" + base + "/" + topic
}

// relayFrame is what goes in a mailbox: a request (to a node's mailbox) or
// a response (to the asker's), carrying the same JSON the Wi-Fi would.
type relayFrame struct {
	Kind   string          `json:"k"`            // req or res
	ID     string          `json:"id"`           // pairs a response with its request
	Reply  string          `json:"re,omitempty"` // req: the asker's mailbox
	Path   string          `json:"p,omitempty"`  // req: /pair/hello, /pair/finish or /msg
	Status int             `json:"s,omitempty"`  // res: as HTTP's
	Data   json.RawMessage `json:"d"`
}

var relayHTTP = &http.Client{Timeout: 30 * time.Second}

// reachError says why the relay could not be reached, and on a phone with
// no certificates (a fresh iSH), how to fix it.
func reachError(base string, err error) error {
	if strings.Contains(err.Error(), "x509") || strings.Contains(err.Error(), "certificate") {
		return fmt.Errorf("cannot check the relay's certificate (%v); in iSH run: apk add ca-certificates", err)
	}
	return fmt.Errorf("cannot reach the relay %s: %v", base, err)
}

func relayPublish(base, topic string, frame relayFrame) error {
	data, _ := json.Marshal(frame)
	if len(data) > relayFrameLimit {
		return fmt.Errorf("message is %d bytes, more than the relay carries (%d)", len(data), relayFrameLimit)
	}
	request, _ := http.NewRequest(http.MethodPost, base+"/"+topic, bytes.NewReader(data))
	request.Header.Set("Content-Type", "text/plain")
	response, err := relayHTTP.Do(request)
	if err != nil {
		return reachError(base, err)
	}
	defer response.Body.Close()
	io.Copy(io.Discard, response.Body)
	if response.StatusCode/100 != 2 {
		return fmt.Errorf("the relay %s answered %s", base, response.Status)
	}
	return nil
}

// relayPoll gives the frames in a mailbox after `since` (a message id, or a
// Unix time), and the new `since`.
func relayPoll(base, topic, since string) ([]relayFrame, string, error) {
	response, err := relayHTTP.Get(base + "/" + topic + "/json?poll=1&since=" + url.QueryEscape(since))
	if err != nil {
		return nil, since, reachError(base, err)
	}
	defer response.Body.Close()
	if response.StatusCode/100 != 2 {
		return nil, since, fmt.Errorf("the relay %s answered %s", base, response.Status)
	}
	frames := []relayFrame{}
	scanner := bufio.NewScanner(io.LimitReader(response.Body, 4<<20))
	scanner.Buffer(make([]byte, 64<<10), 64<<10)
	for scanner.Scan() {
		var event struct {
			ID      string `json:"id"`
			Event   string `json:"event"`
			Message string `json:"message"`
		}
		if json.Unmarshal(scanner.Bytes(), &event) != nil || event.Event != "message" {
			continue
		}
		since = event.ID
		var frame relayFrame
		if json.Unmarshal([]byte(event.Message), &frame) == nil && (frame.Kind == "req" || frame.Kind == "res") {
			frames = append(frames, frame)
		}
	}
	return frames, since, scanner.Err()
}

// relayCall sends one request through the relay and waits for its answer,
// as postJSON does over the Wi-Fi.
func relayCall(address, path string, value any, into any) error {
	base, topic, err := parseRelayAddress(address)
	if err != nil {
		return err
	}
	me, err := loadIdentity("")
	if err != nil {
		return err
	}
	inbox, err := myInbox(&me)
	if err != nil {
		return err
	}
	data, _ := json.Marshal(value)
	since := strconv.FormatInt(time.Now().Add(-2*time.Second).Unix(), 10)
	frame := relayFrame{Kind: "req", ID: newTopic()[4:], Reply: inbox, Path: path, Data: data}
	if err := relayPublish(base, topic, frame); err != nil {
		return err
	}
	deadline := time.Now().Add(relayWait)
	for time.Now().Before(deadline) {
		frames, next, err := relayPoll(base, inbox, since)
		if err == nil {
			since = next
			for _, got := range frames {
				if got.Kind != "res" || got.ID != frame.ID {
					continue
				}
				if got.Status != 200 {
					var said struct {
						Error string `json:"error"`
					}
					if json.Unmarshal(got.Data, &said) == nil && said.Error != "" {
						return errors.New(said.Error)
					}
					return fmt.Errorf("the phone answered %d", got.Status)
				}
				return json.Unmarshal(got.Data, into)
			}
		}
		time.Sleep(relayPollEvery)
	}
	return errors.New("no answer through the relay in time: is `phone node --relay` running on the phone?")
}

var relayWait = 120 * time.Second
var relayPollEvery = 2 * time.Second

// myInbox is this device's own mailbox, made once and kept.
func myInbox(me *phoneIdentity) (string, error) {
	if me.Inbox != "" {
		return me.Inbox, nil
	}
	me.Inbox = newTopic()
	return me.Inbox, writePrivate(identityPath(), *me)
}

// nodeTopic is the mailbox a node listens on: kept, so paired devices can
// still find it after the node restarts.
func nodeTopic(me *phoneIdentity) (string, error) {
	if me.NodeTopic != "" {
		return me.NodeTopic, nil
	}
	me.NodeTopic = newTopic()
	return me.NodeTopic, writePrivate(identityPath(), *me)
}

// serveRelay is a node's loop through the relay: each request is answered
// by the same handlers as on the Wi-Fi, and the answer posted back.
func (n *phoneNode) serveRelay(base, topic string, stop <-chan struct{}) error {
	since := strconv.FormatInt(time.Now().Unix(), 10)
	handler := n.routes()
	failures := 0
	for {
		select {
		case <-stop:
			return nil
		default:
		}
		frames, next, err := relayPoll(base, topic, since)
		if err != nil {
			failures++
			if failures == 1 || failures%30 == 0 {
				n.say("relay: %v (retrying)", err)
			}
			time.Sleep(relayPollEvery * time.Duration(min(failures, 5)))
			continue
		}
		failures = 0
		since = next
		for _, frame := range frames {
			if frame.Kind != "req" || !topicShape.MatchString(frame.Reply) {
				continue
			}
			switch frame.Path {
			case "/pair/hello", "/pair/finish", "/msg":
			default:
				continue
			}
			request := httptest.NewRequest(http.MethodPost, frame.Path, bytes.NewReader(frame.Data))
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, request)
			answer := relayFrame{Kind: "res", ID: frame.ID, Status: recorder.Code, Data: json.RawMessage(bytes.TrimSpace(recorder.Body.Bytes()))}
			if len(answer.Data) == 0 {
				answer.Data = json.RawMessage("null")
			}
			if err := relayPublish(base, frame.Reply, answer); err != nil {
				n.say("relay: could not send the answer: %v", err)
				tooBig, _ := json.Marshal(map[string]string{"error": "the phone's answer was too big for the relay"})
				_ = relayPublish(base, frame.Reply, relayFrame{Kind: "res", ID: frame.ID, Status: 502, Data: tooBig})
			}
		}
		time.Sleep(relayPollEvery)
	}
}

// ---- a relay of one's own ----

// phoneRelayServer keeps each mailbox's messages for 12 hours, as ntfy.sh
// does, in memory. It speaks the part of ntfy's protocol Kosika uses.
type phoneRelayServer struct {
	mu     sync.Mutex
	boxes  map[string][]relayMessage
	serial int64
}

type relayMessage struct {
	ID      string `json:"id"`
	Time    int64  `json:"time"`
	Event   string `json:"event"`
	Topic   string `json:"topic"`
	Message string `json:"message"`
	order   int64
}

func newRelayServer() *phoneRelayServer {
	return &phoneRelayServer{boxes: map[string][]relayMessage{}}
}

func (s *phoneRelayServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	topic := parts[0]
	if !topicShape.MatchString(topic) {
		http.Error(w, "not a mailbox", http.StatusNotFound)
		return
	}
	switch {
	case r.Method == http.MethodPost && len(parts) == 1:
		body, err := io.ReadAll(io.LimitReader(r.Body, 4097))
		if err != nil || len(body) > 4096 {
			http.Error(w, "message too large", http.StatusRequestEntityTooLarge)
			return
		}
		s.mu.Lock()
		s.serial++
		message := relayMessage{ID: fmt.Sprintf("m%012d", s.serial), Time: time.Now().Unix(), Event: "message", Topic: topic, Message: string(body), order: s.serial}
		box := append(s.boxes[topic], message)
		cutoff := time.Now().Add(-12 * time.Hour).Unix()
		for len(box) > 0 && (box[0].Time < cutoff || len(box) > 500) {
			box = box[1:]
		}
		s.boxes[topic] = box
		s.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(message)
	case r.Method == http.MethodGet && len(parts) == 2 && parts[1] == "json":
		since := r.URL.Query().Get("since")
		s.mu.Lock()
		box := append([]relayMessage(nil), s.boxes[topic]...)
		s.mu.Unlock()
		after := func(m relayMessage) bool { return true }
		if since != "" && since != "all" {
			if when, err := strconv.ParseInt(since, 10, 64); err == nil {
				after = func(m relayMessage) bool { return m.Time >= when }
			} else {
				var from int64 = -1
				for _, m := range box {
					if m.ID == since {
						from = m.order
					}
				}
				after = func(m relayMessage) bool { return m.order > from }
			}
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		encoder := json.NewEncoder(w)
		for _, m := range box {
			if after(m) {
				_ = encoder.Encode(m)
			}
		}
	default:
		http.Error(w, "not here", http.StatusNotFound)
	}
}

func phoneRelayCommand(args []string) error {
	set := flag.NewFlagSet("relay", flag.ContinueOnError)
	port := set.Int("port", 8795, "")
	listen := set.String("listen", "0.0.0.0", "")
	if _, err := flags(set, args); err != nil {
		return err
	}
	listener, err := net.Listen("tcp", fmt.Sprintf("%s:%d", *listen, *port))
	if err != nil {
		return err
	}
	fmt.Printf("Kosika relay on port %d. It keeps sealed messages for 12 hours, in memory.\n", *port)
	for _, address := range localAddresses() {
		fmt.Printf("  devices use it with KOSIKA_RELAY=http://%s:%d\n", address, *port)
	}
	server := &http.Server{Handler: newRelayServer(), ReadHeaderTimeout: 10 * time.Second}
	return server.Serve(listener)
}
