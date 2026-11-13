package main

// The phone's side: `phone node` listens on the Wi-Fi, pairs once with a
// one-time code, and answers what a paired device asks, after asking its
// own person yes or no. It runs only the senses and actions named below,
// each a fixed Termux:API command with checked arguments; there is no way
// to send it a command line of one's own.

import (
	"bufio"
	"context"
	"crypto/ecdh"
	"crypto/hmac"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"time"
)

// phoneSense is something a phone can be asked to read.
type phoneSense struct {
	about   string
	command []string
	json    bool
	fake    any
	// read, when set, reads the sense in Go instead of running a command,
	// for what any phone can answer, an iPhone in iSH too.
	read func() (any, error)
}

var phoneSenses = map[string]phoneSense{
	"battery":   {"its battery level and charging", []string{"termux-battery-status"}, true, map[string]any{"percentage": 87, "status": "CHARGING", "health": "GOOD"}, nil},
	"location":  {"where it is (from the network)", []string{"termux-location", "-p", "network", "-r", "once"}, true, map[string]any{"latitude": 28.61, "longitude": 77.21, "accuracy": 40.0}, nil},
	"clipboard": {"what is on its clipboard", []string{"termux-clipboard-get"}, false, "hello from the phone's clipboard", nil},
	"wifi":      {"the Wi-Fi it is on", []string{"termux-wifi-connectioninfo"}, true, map[string]any{"ssid": "home", "rssi": -48}, nil},
	"device":    {"its model and system", nil, false, "Pixel (simulated)", readDevice},
	"files":     {"the names of its Kosika programs (in ~/kosika)", nil, false, []string{"discrete_maths.kos"}, readFiles},
}

// phoneAction is something a phone can be asked to do. Params names the
// arguments in the order the command line gives them.
type phoneAction struct {
	about  string
	params []string
	check  func(map[string]string) error
	build  func(map[string]string) []string
	// local, when set, does the action in Go instead of running a command:
	// what works on any phone, an iPhone in iSH too.
	local func(map[string]string) (any, error)
}

func textUnder(limit int) func(string) error {
	return func(text string) error {
		if strings.TrimSpace(text) == "" {
			return errors.New("is empty")
		}
		if len(text) > limit {
			return fmt.Errorf("is longer than %d letters", limit)
		}
		return nil
	}
}

func checkEach(rules map[string]func(string) error) func(map[string]string) error {
	return func(args map[string]string) error {
		for name, rule := range rules {
			if err := rule(args[name]); err != nil {
				return fmt.Errorf("%s %v", name, err)
			}
		}
		return nil
	}
}

var phoneActions = map[string]phoneAction{
	"notify": {"show a notification", []string{"title", "text"},
		checkEach(map[string]func(string) error{"title": textUnder(80), "text": textUnder(500)}),
		func(a map[string]string) []string {
			return []string{"termux-notification", "--title", a["title"], "--content", a["text"]}
		}, nil},
	"toast": {"show a short message on screen", []string{"text"},
		checkEach(map[string]func(string) error{"text": textUnder(200)}),
		func(a map[string]string) []string { return []string{"termux-toast", a["text"]} }, nil},
	"speak": {"say something aloud", []string{"text"},
		checkEach(map[string]func(string) error{"text": textUnder(500)}),
		func(a map[string]string) []string { return []string{"termux-tts-speak", a["text"]} }, nil},
	"vibrate": {"vibrate", []string{"ms"},
		checkEach(map[string]func(string) error{"ms": func(text string) error {
			ms, err := strconv.Atoi(text)
			if err != nil || ms < 1 || ms > 5000 {
				return errors.New("must be a whole number from 1 to 5000")
			}
			return nil
		}}),
		func(a map[string]string) []string { return []string{"termux-vibrate", "-d", a["ms"]} }, nil},
	"open-url": {"open a web page", []string{"url"},
		checkEach(map[string]func(string) error{"url": func(text string) error {
			parsed, err := url.Parse(text)
			if err != nil || (parsed.Scheme != "https" && parsed.Scheme != "http") || parsed.Host == "" {
				return errors.New("must be an http or https address")
			}
			return nil
		}}),
		func(a map[string]string) []string { return []string{"termux-open-url", a["url"]} }, nil},
	"torch": {"turn the torch on or off", []string{"state"},
		checkEach(map[string]func(string) error{"state": func(text string) error {
			if text != "on" && text != "off" {
				return errors.New("must be on or off")
			}
			return nil
		}}),
		func(a map[string]string) []string { return []string{"termux-torch", a["state"]} }, nil},
	"clipboard": {"put text on its clipboard", []string{"text"},
		checkEach(map[string]func(string) error{"text": textUnder(4000)}),
		func(a map[string]string) []string { return []string{"termux-clipboard-set", a["text"]} }, nil},
}

// describe says a request in words, for the yes-or-no question.
func describe(body phoneBody) string {
	switch body.Kind {
	case "ask":
		if sense, ok := phoneSenses[body.What]; ok {
			return "read " + sense.about
		}
	case "do":
		if action, ok := phoneActions[body.Action]; ok {
			parts := []string{}
			for _, name := range action.params {
				parts = append(parts, fmt.Sprintf("%s=%q", name, body.Args[name]))
			}
			return action.about + " (" + strings.Join(parts, ", ") + ")"
		}
	}
	return body.Kind
}

// ---- asking the person ----

type confirmer interface {
	confirm(question string, allowAlways bool) (answer string) // yes, always, no
}

// terminalConfirm asks at the terminal the node runs in. One reader feeds
// all questions, so a question that timed out does not swallow the next
// answer.
type terminalConfirm struct {
	lines   chan string
	timeout time.Duration
}

func newTerminalConfirm(in io.Reader, timeout time.Duration) *terminalConfirm {
	t := &terminalConfirm{lines: make(chan string, 8), timeout: timeout}
	go func() {
		scanner := bufio.NewScanner(in)
		for scanner.Scan() {
			t.lines <- scanner.Text()
		}
		close(t.lines)
	}()
	return t
}

func (t *terminalConfirm) confirm(question string, allowAlways bool) string {
	for drained := false; !drained; {
		select {
		case <-t.lines:
		default:
			drained = true
		}
	}
	choices := "[y/N]"
	if allowAlways {
		choices = "[y/N/always]"
	}
	fmt.Printf("\n  ? %s %s ", question, choices)
	select {
	case line, ok := <-t.lines:
		if !ok {
			return "no"
		}
		switch strings.ToLower(strings.TrimSpace(line)) {
		case "y", "yes":
			return "yes"
		case "always", "a":
			if allowAlways {
				return "always"
			}
		}
		return "no"
	case <-time.After(t.timeout):
		fmt.Println("(no answer in time: refused)")
		return "no"
	}
}

// dialogConfirm asks with a Termux:API dialog on the phone's screen.
type dialogConfirm struct{}

func (dialogConfirm) confirm(question string, allowAlways bool) string {
	args := []string{"-t", "Kosika", "-i", question}
	command := "confirm"
	if allowAlways {
		command = "radio"
		args = append(args, "-v", "Yes,Always,No")
	}
	out, err := exec.Command("termux-dialog", append([]string{command}, args...)...).Output()
	if err != nil {
		return "no"
	}
	var said struct {
		Code int    `json:"code"`
		Text string `json:"text"`
	}
	if json.Unmarshal(out, &said) != nil || said.Code != -1 {
		return "no"
	}
	switch strings.ToLower(said.Text) {
	case "yes":
		return "yes"
	case "always":
		return "always"
	}
	return "no"
}

// ---- running ----

type phoneNode struct {
	mu        sync.Mutex
	me        phoneIdentity
	code      string
	codeUntil time.Time
	pending   map[string]pendingPair
	asker     confirmer
	simulate  bool
	run       func(ctx context.Context, argv []string) ([]byte, error)
	say       func(format string, args ...any)
}

type pendingPair struct {
	hub    pairHello
	node   pairHello
	secret *ecdh.PrivateKey
	at     time.Time
}

func runCommandLine(ctx context.Context, argv []string) ([]byte, error) {
	if _, err := exec.LookPath(argv[0]); err != nil {
		return nil, fmt.Errorf("%s is not installed here (on Android: install the Termux:API app, then `pkg install termux-api`)", argv[0])
	}
	out, err := exec.CommandContext(ctx, argv[0], argv[1:]...).Output()
	if err != nil {
		return out, fmt.Errorf("%s failed: %v", argv[0], err)
	}
	return out, nil
}

// handle answers one request a paired device sent. It is the whole of
// what the node will do for anyone.
func (n *phoneNode) handle(peer phonePeer, body phoneBody) phoneBody {
	reply := phoneBody{Kind: "reply"}
	switch body.Kind {
	case "ping":
		reply.Status, reply.Result = "done", map[string]any{"name": n.me.Name, "senses": sortedKeys(phoneSenses), "actions": sortedKeys(phoneActions)}
		return reply
	case "ask":
		sense, ok := phoneSenses[body.What]
		if !ok {
			reply.Status, reply.Note = "refused", fmt.Sprintf("no sense called %q (there are: %s)", body.What, strings.Join(sortedKeys(phoneSenses), ", "))
			return reply
		}
		grant := "ask:" + body.What
		if !peer.granted(grant) {
			answer := n.asker.confirm(fmt.Sprintf("%s asks to %s. Allow?", peer.Name, describe(body)), true)
			if answer == "no" {
				reply.Status, reply.Note = "refused", "the phone's person said no"
				return reply
			}
			if answer == "always" {
				peer.Grants = append(peer.Grants, grant)
				if err := savePeer(peer); err != nil {
					n.say("could not keep the grant: %v", err)
				}
			}
		}
		if n.simulate {
			reply.Status, reply.Result, reply.Note, reply.Simulated = "done", sense.fake, "simulated: nothing was read from a real phone", true
			return reply
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if sense.read != nil {
			result, err := sense.read()
			if err != nil {
				reply.Status, reply.Note = "failed", err.Error()
				return reply
			}
			reply.Status, reply.Result = "done", result
			return reply
		}
		out, err := n.run(ctx, sense.command)
		if err != nil {
			reply.Status, reply.Note = "failed", err.Error()
			return reply
		}
		reply.Status = "done"
		var parsed any
		if sense.json && json.Unmarshal(out, &parsed) == nil {
			reply.Result = parsed
		} else {
			reply.Result = strings.TrimSpace(string(out))
		}
		return reply
	case "do":
		action, ok := phoneActions[body.Action]
		if !ok {
			reply.Status, reply.Note = "refused", fmt.Sprintf("no action called %q (there are: %s)", body.Action, strings.Join(sortedKeys(phoneActions), ", "))
			return reply
		}
		if err := action.check(body.Args); err != nil {
			reply.Status, reply.Note = "refused", err.Error()
			return reply
		}
		// An action is asked about every time: it cannot be granted ahead.
		if n.asker.confirm(fmt.Sprintf("%s asks to %s. Allow?", peer.Name, describe(body)), false) != "yes" {
			reply.Status, reply.Note = "refused", "the phone's person said no"
			return reply
		}
		if n.simulate {
			reply.Status, reply.Note, reply.Simulated = "done", "simulated: nothing was done on a real phone", true
			return reply
		}
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if action.local != nil {
			result, err := action.local(body.Args)
			if err != nil {
				reply.Status, reply.Note = "failed", err.Error()
				return reply
			}
			reply.Status, reply.Result = "done", result
			return reply
		}
		if _, err := n.run(ctx, action.build(body.Args)); err != nil {
			reply.Status, reply.Note = "failed", err.Error()
			return reply
		}
		reply.Status = "done"
		return reply
	}
	reply.Status, reply.Note = "refused", fmt.Sprintf("cannot %q", body.Kind)
	return reply
}

func (n *phoneNode) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/pair/hello", n.pairHello)
	mux.HandleFunc("/pair/finish", n.pairFinish)
	mux.HandleFunc("/msg", n.message)
	return mux
}

func readJSON(r *http.Request, into any) error {
	if r.Method != http.MethodPost {
		return errors.New("POST only")
	}
	decoder := json.NewDecoder(io.LimitReader(r.Body, 64<<10))
	decoder.DisallowUnknownFields()
	return decoder.Decode(into)
}

func sendJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func (n *phoneNode) pairHello(w http.ResponseWriter, r *http.Request) {
	var hub pairHello
	if err := readJSON(r, &hub); err != nil {
		sendJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.code == "" || time.Now().After(n.codeUntil) {
		sendJSON(w, 403, map[string]string{"error": "this phone is not pairing now; start it with `phone node --pair` for a new code"})
		return
	}
	share, err := newShare()
	if err != nil {
		sendJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	node := pairHello{Name: n.me.Name, Public: b64.EncodeToString(n.me.public()), Share: b64.EncodeToString(share.PublicKey().Bytes()), Nonce: randomText(16)}
	// One pairing at a time: a new hello replaces an unfinished one.
	n.pending = map[string]pendingPair{hub.Nonce: {hub: hub, node: node, secret: share, at: time.Now()}}
	sendJSON(w, 200, node)
}

func (n *phoneNode) pairFinish(w http.ResponseWriter, r *http.Request) {
	var finish struct {
		Nonce string `json:"nonce"`
		Proof string `json:"proof"`
	}
	if err := readJSON(r, &finish); err != nil {
		sendJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	pending, ok := n.pending[finish.Nonce]
	code := n.code
	// The code is used up by any attempt, right or wrong: a guesser gets
	// one try, then needs the person to make a new code.
	n.code, n.pending = "", nil
	if !ok || code == "" {
		sendJSON(w, 403, map[string]string{"error": "no pairing in progress"})
		return
	}
	transcript := transcriptHash(pending.hub, pending.node)
	key, err := pairKey(pending.secret, pending.hub.Share, code, transcript)
	if err != nil || !hmac.Equal([]byte(finish.Proof), []byte(pairProof(key, "hub", transcript))) {
		n.say("A pairing attempt from %s used the wrong code. The code is used up; run `phone node --pair` for a new one.", pending.hub.Name)
		sendJSON(w, 403, map[string]string{"error": "wrong code; the phone needs a new one"})
		return
	}
	name := cleanName(pending.hub.Name)
	if existing, err := loadPeer(name); err == nil && existing.Public != pending.hub.Public {
		name = cleanName(name + "-" + deviceID([]byte(pending.hub.Public))[:4])
	}
	peer := phonePeer{Name: name, Role: "hub", Public: pending.hub.Public, Key: b64.EncodeToString(key), PairedAt: time.Now().UTC().Format(time.RFC3339)}
	if err := savePeer(peer); err != nil {
		sendJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	_ = appendLog(peer.Name, "note", nil, phoneBody{Kind: "paired", Note: "paired with " + pending.hub.Name + "; fingerprint " + fingerprint(pending.hub.Public, pending.node.Public)})
	n.say("Paired with %s. Fingerprint %s (the laptop shows the same).", peer.Name, fingerprint(pending.hub.Public, pending.node.Public))
	sendJSON(w, 200, map[string]string{"proof": pairProof(key, "node", transcript)})
}

// message takes one signed, sealed request, answers it, and sends back a
// signed, sealed reply. Both are written to this device's log.
func (n *phoneNode) message(w http.ResponseWriter, r *http.Request) {
	var envelope phoneEnvelope
	if err := readJSON(r, &envelope); err != nil {
		sendJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	var peer phonePeer
	found := false
	for _, candidate := range listPeers() {
		if deviceID(candidate.public()) == envelope.From {
			peer, found = candidate, true
		}
	}
	if !found {
		sendJSON(w, 403, map[string]string{"error": "this phone is not paired with that device"})
		return
	}
	body, err := open(n.me, peer, envelope)
	if err != nil {
		sendJSON(w, 403, map[string]string{"error": err.Error()})
		return
	}
	if envelope.Seq <= peer.SeqIn {
		sendJSON(w, 403, map[string]string{"error": "message was already seen (replayed)"})
		return
	}
	peer.SeqIn = envelope.Seq
	if err := savePeer(peer); err != nil {
		sendJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	_ = appendLog(peer.Name, "in", &envelope, body)
	n.say("%s asks: %s", peer.Name, describe(body))
	reply := n.handle(peer, body)
	reply.Re = envelope.Seq
	peer, _ = loadPeer(peer.Name) // handle may have added a grant
	peer.SeqOut++
	if err := savePeer(peer); err != nil {
		sendJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	sealed, err := seal(n.me, peer, peer.SeqOut, reply)
	if err != nil {
		sendJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	_ = appendLog(peer.Name, "out", &sealed, reply)
	n.say("  -> %s%s", reply.Status, map[bool]string{true: ": " + reply.Note, false: ""}[reply.Note != ""])
	sendJSON(w, 200, sealed)
}

func localAddresses() []string {
	found := []string{}
	interfaces, _ := net.Interfaces()
	for _, face := range interfaces {
		if face.Flags&net.FlagUp == 0 || face.Flags&net.FlagLoopback != 0 {
			continue
		}
		addresses, _ := face.Addrs()
		for _, address := range addresses {
			if ip, ok := address.(*net.IPNet); ok && ip.IP.To4() != nil {
				found = append(found, ip.IP.String())
			}
		}
	}
	return found
}
