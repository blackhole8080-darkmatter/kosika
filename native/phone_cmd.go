package main

// `kosika-native phone ...`: the command line for phones as Kosika nodes.

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
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const phoneUsage = `kosika-native phone: phones as Kosika nodes

On the phone (Android, in Termux; or any computer):
  phone node [--relay] [--port 8794] [--name NAME] [--pair] [--confirm terminal|dialog] [--simulate]
        answer paired devices; --pair shows a one-time code (5 minutes);
        --relay calls out to a relay (ntfy.sh, or KOSIKA_RELAY) so it works on any network

On the laptop:
  phone pair ADDRESS CODE [--name NAME]   pair with a phone showing CODE
  phone list                              paired devices
  phone ping NAME                         is it there, and what can it do
  phone ask NAME SENSE                    battery | location | clipboard | wifi | device | files
  phone do NAME ACTION [ARGS...]          show TEXT | save NAME.kos TEXT | test NAME.kos |
                                          notify TITLE TEXT | toast TEXT | speak TEXT |
                                          vibrate MS | open-url URL | torch on|off |
                                          clipboard TEXT
  phone log NAME [--check]                what was asked and answered; --check proves it whole
  phone unpair NAME                       forget a device
  phone id [--name NAME]                  this device's name and id
  phone relay [--port 8795]               run a relay of your own (ntfy's protocol)

Real control of an Android phone over USB or Wi-Fi debugging (asks yes each time):
  phone adb devices | tap X Y | swipe X1 Y1 X2 Y2 [MS] | type TEXT |
            key home|back|enter|recent|volup|voldown | open PACKAGE |
            screenshot FILE.png   [--serial SERIAL]

Every request is a signed, encrypted, hash-chained log line on both devices.
The phone asks its person yes or no before each one.`

func phoneCommand(args []string) int {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		fmt.Println(phoneUsage)
		return 0
	}
	command, rest := args[0], args[1:]
	var err error
	switch command {
	case "id":
		err = phoneID(rest)
	case "node":
		err = phoneNodeCommand(rest)
	case "pair":
		err = phonePair(rest)
	case "list":
		err = phoneList()
	case "ping":
		err = phoneRequest(rest, 1, func(a []string) (phoneBody, error) { return phoneBody{Kind: "ping"}, nil })
	case "ask":
		err = phoneRequest(rest, 2, func(a []string) (phoneBody, error) {
			if _, ok := phoneSenses[a[1]]; !ok {
				return phoneBody{}, fmt.Errorf("no sense called %q (there are: %s)", a[1], strings.Join(sortedKeys(phoneSenses), ", "))
			}
			return phoneBody{Kind: "ask", What: a[1]}, nil
		})
	case "do":
		err = phoneRequest(rest, 2, phoneDoBody)
	case "log":
		err = phoneLog(rest)
	case "unpair":
		err = phoneUnpair(rest)
	case "adb":
		err = phoneADB(rest, os.Stdin)
	case "relay":
		err = phoneRelayCommand(rest)
	default:
		err = fmt.Errorf("no phone command %q\n\n%s", command, phoneUsage)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "phone:", err)
		return 1
	}
	return 0
}

// flags parses flags that may come before or after the positional words.
func flags(set *flag.FlagSet, args []string) ([]string, error) {
	set.SetOutput(io.Discard)
	positional := []string{}
	for len(args) > 0 {
		if err := set.Parse(args); err != nil {
			return nil, err
		}
		args = set.Args()
		if len(args) > 0 {
			positional = append(positional, args[0])
			args = args[1:]
		}
	}
	return positional, nil
}

func phoneID(args []string) error {
	set := flag.NewFlagSet("id", flag.ContinueOnError)
	name := set.String("name", "", "")
	if _, err := flags(set, args); err != nil {
		return err
	}
	me, err := loadIdentity(*name)
	if err != nil {
		return err
	}
	fmt.Printf("%s  id %s\nkept in %s\n", me.Name, deviceID(me.public()), phoneHome())
	return nil
}

func phoneNodeCommand(args []string) error {
	set := flag.NewFlagSet("node", flag.ContinueOnError)
	port := set.Int("port", phoneDefaultPort, "")
	name := set.String("name", "", "")
	pair := set.Bool("pair", false, "")
	confirm := set.String("confirm", "terminal", "")
	simulate := set.Bool("simulate", false, "")
	listen := set.String("listen", "0.0.0.0", "")
	relay := set.Bool("relay", false, "")
	if _, err := flags(set, args); err != nil {
		return err
	}
	me, err := loadIdentity(*name)
	if err != nil {
		return err
	}
	node := &phoneNode{me: me, simulate: *simulate, run: runCommandLine,
		say: func(format string, a ...any) { fmt.Printf(format+"\n", a...) }}
	switch *confirm {
	case "terminal":
		node.asker = newTerminalConfirm(os.Stdin, 60*time.Second)
	case "dialog":
		node.asker = dialogConfirm{}
	default:
		return fmt.Errorf("--confirm is terminal or dialog, not %q", *confirm)
	}
	hubs := 0
	for _, peer := range listPeers() {
		if peer.Role == "hub" {
			hubs++
		}
	}
	where := ""
	var listener net.Listener
	if *relay {
		topic, err := nodeTopic(&me)
		if err != nil {
			return err
		}
		node.me = me
		where = relayAddressOf(relayBase(), topic)
		fmt.Printf("Kosika phone node %q (id %s), reached through the relay %s\n", me.Name, deviceID(me.public()), relayBase())
		fmt.Printf("  address: %s\n", where)
		fmt.Println("  It calls out, so it works on any network. Keep this screen open.")
	} else {
		listener, err = net.Listen("tcp", fmt.Sprintf("%s:%d", *listen, *port))
		if err != nil {
			return err
		}
		fmt.Printf("Kosika phone node %q (id %s) listening on port %d\n", me.Name, deviceID(me.public()), *port)
		for _, address := range localAddresses() {
			fmt.Printf("  reachable at %s:%d\n", address, *port)
		}
		where = "<address above>"
	}
	if *simulate {
		fmt.Println("  SIMULATING: senses give made-up values and actions do nothing; replies say so.")
	}
	if *pair || hubs == 0 {
		node.code, node.codeUntil = newPairCode(), time.Now().Add(5*time.Minute)
		fmt.Printf("\n  Pairing code: %s   (works once, for 5 minutes)\n", node.code)
		fmt.Printf("  On the laptop: kos phone pair %s %s --name %s\n\n", where, node.code, me.Name)
	}
	if *relay {
		base, topic, _ := parseRelayAddress(where)
		return node.serveRelay(base, topic, nil)
	}
	server := &http.Server{Handler: node.routes(), ReadHeaderTimeout: 10 * time.Second}
	return server.Serve(listener)
}

// ---- the laptop side ----

func postJSON(address, path string, value any, into any) error {
	if strings.HasPrefix(address, "relay:") {
		return relayCall(address, path, value, into)
	}
	data, _ := json.Marshal(value)
	client := &http.Client{Timeout: 90 * time.Second} // the person may take a minute to answer
	response, err := client.Post("http://"+address+path, "application/json", bytes.NewReader(data))
	if err != nil {
		return fmt.Errorf("cannot reach %s: %v", address, err)
	}
	defer response.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if response.StatusCode != 200 {
		var said struct {
			Error string `json:"error"`
		}
		if json.Unmarshal(body, &said) == nil && said.Error != "" {
			return errors.New(said.Error)
		}
		return fmt.Errorf("%s answered %s", address, response.Status)
	}
	return json.Unmarshal(body, into)
}

func withPort(address string) string {
	if strings.HasPrefix(address, "relay:") {
		return address
	}
	if _, _, err := net.SplitHostPort(address); err != nil {
		return net.JoinHostPort(address, strconv.Itoa(phoneDefaultPort))
	}
	return address
}

func phonePair(args []string) error {
	set := flag.NewFlagSet("pair", flag.ContinueOnError)
	name := set.String("name", "", "")
	words, err := flags(set, args)
	if err != nil {
		return err
	}
	if len(words) != 2 {
		return errors.New("usage: phone pair ADDRESS CODE [--name NAME]")
	}
	address, code := withPort(words[0]), words[1]
	me, err := loadIdentity("")
	if err != nil {
		return err
	}
	share, err := newShare()
	if err != nil {
		return err
	}
	hub := pairHello{Name: me.Name, Public: b64.EncodeToString(me.public()), Share: b64.EncodeToString(share.PublicKey().Bytes()), Nonce: randomText(16)}
	var node pairHello
	if err := postJSON(address, "/pair/hello", hub, &node); err != nil {
		return err
	}
	transcript := transcriptHash(hub, node)
	key, err := pairKey(share, node.Share, code, transcript)
	if err != nil {
		return err
	}
	var finished struct {
		Proof string `json:"proof"`
	}
	if err := postJSON(address, "/pair/finish", map[string]string{"nonce": hub.Nonce, "proof": pairProof(key, "hub", transcript)}, &finished); err != nil {
		return err
	}
	if finished.Proof != pairProof(key, "node", transcript) {
		return errors.New("the phone could not prove it knows the code: not paired")
	}
	label := cleanName(node.Name)
	if *name != "" {
		label = cleanName(*name)
	}
	peer := phonePeer{Name: label, Role: "node", Address: address, Public: node.Public, Key: b64.EncodeToString(key), PairedAt: time.Now().UTC().Format(time.RFC3339)}
	if err := savePeer(peer); err != nil {
		return err
	}
	_ = appendLog(label, "note", nil, phoneBody{Kind: "paired", Note: "paired with " + node.Name + " at " + address + "; fingerprint " + fingerprint(hub.Public, node.Public)})
	fmt.Printf("Paired with %s (id %s) at %s, as %q.\nFingerprint %s: the phone shows the same.\n", node.Name, deviceID(peer.public()), address, label, fingerprint(hub.Public, node.Public))
	return nil
}

func phoneList() error {
	peers := listPeers()
	if len(peers) == 0 {
		fmt.Println("No paired devices. On a phone run `kosika-native phone node`, then `phone pair` here.")
		return nil
	}
	for _, peer := range peers {
		where := peer.Address
		if peer.Role == "hub" {
			where = "(asks this device)"
		}
		grants := ""
		if len(peer.Grants) > 0 {
			grants = "  granted: " + strings.Join(peer.Grants, ", ")
		}
		fmt.Printf("%-16s %-5s id %s  %s%s\n", peer.Name, peer.Role, deviceID(peer.public()), where, grants)
	}
	return nil
}

func phoneDoBody(words []string) (phoneBody, error) {
	action, ok := phoneActions[words[1]]
	if !ok {
		return phoneBody{}, fmt.Errorf("no action called %q (there are: %s)", words[1], strings.Join(sortedKeys(phoneActions), ", "))
	}
	given := words[2:]
	if len(given) != len(action.params) {
		return phoneBody{}, fmt.Errorf("%s takes %s", words[1], strings.ToUpper(strings.Join(action.params, " ")))
	}
	args := map[string]string{}
	for i, name := range action.params {
		args[name] = given[i]
	}
	if err := action.check(args); err != nil {
		return phoneBody{}, err
	}
	return phoneBody{Kind: "do", Action: words[1], Args: args}, nil
}

// phoneRequest sends one request to a paired phone and shows its reply.
func phoneRequest(args []string, least int, build func([]string) (phoneBody, error)) error {
	if len(args) < least {
		return errors.New("name the paired device first: see `kosika-native phone help`")
	}
	me, err := loadIdentity("")
	if err != nil {
		return err
	}
	peer, err := loadPeer(args[0])
	if err != nil {
		return err
	}
	if peer.Role != "node" {
		return fmt.Errorf("%s asks this device; it does not take requests from here", peer.Name)
	}
	body, err := build(args)
	if err != nil {
		return err
	}
	reply, err := phoneSend(me, peer, body)
	if err != nil {
		return err
	}
	fmt.Println(replyText(peer.Name, body, reply))
	if reply.Status != "done" {
		return errors.New(reply.Status)
	}
	return nil
}

func phoneSend(me phoneIdentity, peer phonePeer, body phoneBody) (phoneBody, error) {
	peer.SeqOut++
	if err := savePeer(peer); err != nil {
		return phoneBody{}, err
	}
	envelope, err := seal(me, peer, peer.SeqOut, body)
	if err != nil {
		return phoneBody{}, err
	}
	if err := appendLog(peer.Name, "out", &envelope, body); err != nil {
		return phoneBody{}, err
	}
	var answer phoneEnvelope
	if err := postJSON(peer.Address, "/msg", envelope, &answer); err != nil {
		return phoneBody{}, err
	}
	reply, err := open(me, peer, answer)
	if err != nil {
		return phoneBody{}, fmt.Errorf("the reply did not check out: %v", err)
	}
	if answer.Seq <= peer.SeqIn {
		return phoneBody{}, errors.New("the reply was one already seen (replayed)")
	}
	if reply.Kind != "reply" || reply.Re != envelope.Seq {
		return phoneBody{}, errors.New("the reply does not answer this request")
	}
	peer.SeqIn = answer.Seq
	if err := savePeer(peer); err != nil {
		return phoneBody{}, err
	}
	return reply, appendLog(peer.Name, "in", &answer, reply)
}

func replyText(name string, request, reply phoneBody) string {
	head := fmt.Sprintf("%s: %s", name, reply.Status)
	if reply.Simulated {
		head += " (simulated)"
	}
	if reply.Note != "" {
		head += " - " + reply.Note
	}
	if reply.Result == nil {
		return head
	}
	if text, ok := reply.Result.(string); ok {
		return head + "\n" + text
	}
	pretty, _ := json.MarshalIndent(reply.Result, "", "  ")
	return head + "\n" + string(pretty)
}

func phoneLog(args []string) error {
	set := flag.NewFlagSet("log", flag.ContinueOnError)
	check := set.Bool("check", false, "")
	words, err := flags(set, args)
	if err != nil {
		return err
	}
	if len(words) != 1 {
		return errors.New("usage: phone log NAME [--check]")
	}
	lines, err := readLog(words[0])
	if err != nil {
		return err
	}
	if *check {
		me, err := loadIdentity("")
		if err != nil {
			return err
		}
		if words[0] == "adb" {
			err = checkLog(me, phonePeer{Name: "adb"}, lines)
		} else {
			peer, perr := loadPeer(words[0])
			if perr != nil {
				return perr
			}
			err = checkLog(me, peer, lines)
		}
		if err != nil {
			return fmt.Errorf("log is NOT whole: %v", err)
		}
		fmt.Printf("%d lines, chain whole, every signature and sealed body checks out.\n", len(lines))
		return nil
	}
	for _, line := range lines {
		seq := "  "
		if line.Envelope != nil {
			seq = fmt.Sprintf("#%d", line.Envelope.Seq)
		}
		text := describe(line.Body)
		if line.Body.Kind == "reply" {
			text = fmt.Sprintf("reply to #%d: %s", line.Body.Re, line.Body.Status)
			if line.Body.Note != "" {
				text += " - " + line.Body.Note
			}
		} else if line.Body.Note != "" {
			text = line.Body.Note
		}
		fmt.Printf("%s  %-4s %-4s %s\n", line.At, line.Dir, seq, text)
	}
	return nil
}

func phoneUnpair(args []string) error {
	if len(args) != 1 {
		return errors.New("usage: phone unpair NAME")
	}
	if _, err := loadPeer(args[0]); err != nil {
		return err
	}
	_ = appendLog(args[0], "note", nil, phoneBody{Kind: "unpaired", Note: "unpaired"})
	if err := os.Remove(peerPath(args[0])); err != nil {
		return err
	}
	fmt.Printf("Forgot %s. Its log stays in %s.\n", args[0], logPath(args[0]))
	return nil
}

// ---- real control through ADB ----

var packageShape = regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$`)

var adbKeys = map[string]string{
	"home": "KEYCODE_HOME", "back": "KEYCODE_BACK", "enter": "KEYCODE_ENTER",
	"recent": "KEYCODE_APP_SWITCH", "volup": "KEYCODE_VOLUME_UP", "voldown": "KEYCODE_VOLUME_DOWN",
}

func wholeNumbers(words []string, limit int) error {
	for _, word := range words {
		n, err := strconv.Atoi(word)
		if err != nil || n < 0 || n > limit {
			return fmt.Errorf("%q is not a whole number from 0 to %d", word, limit)
		}
	}
	return nil
}

// adbPlan turns a request into what it says and the adb arguments it runs.
func adbPlan(words []string) (about string, argv []string, err error) {
	if len(words) == 0 {
		return "", nil, errors.New("usage: phone adb devices|tap|swipe|type|key|open|screenshot ...")
	}
	need := func(n int) error {
		if len(words)-1 != n {
			return fmt.Errorf("adb %s takes %d values", words[0], n)
		}
		return nil
	}
	switch words[0] {
	case "devices":
		return "", []string{"devices", "-l"}, need(0)
	case "tap":
		if err := need(2); err != nil {
			return "", nil, err
		}
		if err := wholeNumbers(words[1:], 10000); err != nil {
			return "", nil, err
		}
		return fmt.Sprintf("tap the screen at %s,%s", words[1], words[2]), []string{"shell", "input", "tap", words[1], words[2]}, nil
	case "swipe":
		if len(words) != 5 && len(words) != 6 {
			return "", nil, errors.New("adb swipe takes X1 Y1 X2 Y2 [MS]")
		}
		if err := wholeNumbers(words[1:], 10000); err != nil {
			return "", nil, err
		}
		return fmt.Sprintf("swipe from %s,%s to %s,%s", words[1], words[2], words[3], words[4]), append([]string{"shell", "input", "swipe"}, words[1:]...), nil
	case "type":
		if err := need(1); err != nil {
			return "", nil, err
		}
		if err := textUnder(500)(words[1]); err != nil {
			return "", nil, fmt.Errorf("text %v", err)
		}
		return fmt.Sprintf("type %q", words[1]), []string{"shell", "input", "text", adbText(words[1])}, nil
	case "key":
		if err := need(1); err != nil {
			return "", nil, err
		}
		code, ok := adbKeys[words[1]]
		if !ok {
			return "", nil, fmt.Errorf("no key %q (there are: %s)", words[1], strings.Join(sortedKeys(adbKeys), ", "))
		}
		return "press " + words[1], []string{"shell", "input", "keyevent", code}, nil
	case "open":
		if err := need(1); err != nil {
			return "", nil, err
		}
		if !packageShape.MatchString(words[1]) {
			return "", nil, fmt.Errorf("%q is not an app package name like com.android.chrome", words[1])
		}
		return "open the app " + words[1], []string{"shell", "monkey", "-p", words[1], "-c", "android.intent.category.LAUNCHER", "1"}, nil
	case "screenshot":
		if err := need(1); err != nil {
			return "", nil, err
		}
		return "take a screenshot into " + words[1], []string{"exec-out", "screencap", "-p"}, nil
	}
	return "", nil, fmt.Errorf("no adb command %q", words[0])
}

// adbText escapes text for `input text`, which reads %s as a space and
// passes through the phone's shell.
func adbText(text string) string {
	var out strings.Builder
	for _, r := range text {
		switch {
		case r == ' ':
			out.WriteString("%s")
		case strings.ContainsRune(`\'"&|;<>()$`+"`"+`*?[]#~!{}`, r):
			out.WriteRune('\\')
			out.WriteRune(r)
		default:
			out.WriteRune(r)
		}
	}
	return out.String()
}

func phoneADB(args []string, in io.Reader) error {
	set := flag.NewFlagSet("adb", flag.ContinueOnError)
	serial := set.String("serial", "", "")
	words, err := flags(set, args)
	if err != nil {
		return err
	}
	about, argv, err := adbPlan(words)
	if err != nil {
		return err
	}
	if *serial != "" {
		argv = append([]string{"-s", *serial}, argv...)
	}
	if _, err := exec.LookPath("adb"); err != nil {
		return errors.New("adb is not installed (Android platform-tools, free from developer.android.com)")
	}
	if about == "" { // `devices` reads nothing from the phone and changes nothing
		out, err := exec.Command("adb", argv...).CombinedOutput()
		fmt.Print(string(out))
		return err
	}
	fmt.Printf("? Let Kosika %s on the phone? [y/N] ", about)
	answer, _ := bufio.NewReader(in).ReadString('\n')
	body := phoneBody{Kind: "adb", Action: words[0], Args: map[string]string{"argv": strings.Join(argv, " ")}, Note: about}
	if a := strings.ToLower(strings.TrimSpace(answer)); a != "y" && a != "yes" {
		body.Status = "refused"
		_ = appendLog("adb", "out", nil, body)
		return errors.New("refused: nothing was done")
	}
	command := exec.Command("adb", argv...)
	var out bytes.Buffer
	command.Stdout, command.Stderr = &out, &out
	err = command.Run()
	if err == nil && words[0] == "screenshot" {
		err = os.WriteFile(words[1], out.Bytes(), 0o600)
		out.Reset()
	}
	body.Status = "done"
	if err != nil {
		body.Status, body.Note = "failed", about+": "+strings.TrimSpace(out.String()+" "+err.Error())
	}
	_ = appendLog("adb", "out", nil, body)
	if err != nil {
		return errors.New(body.Note)
	}
	fmt.Print(out.String())
	fmt.Println("done:", about)
	return nil
}
