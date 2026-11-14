package main

// Approvals asked in a folder, so the person can answer from anywhere.
// The same files as the reference's kosika/ask_folder.py:
//
//	FOLDER/<id>.ask.json      written by the run: what would be done
//	FOLDER/<id>.answer.json   written by the person: {"answer": "yes", "by": "..."}
//
// The id is new for every ask, so a left-over answer never approves a later
// one, and the approval is bound to the ask's own hashes.

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const askSuffix, answerSuffix = ".ask.json", ".answer.json"

func asksays(request *Record) string {
	var steps []string
	for _, part := range request.Fields["step"].([]Value) {
		steps = append(steps, showText(part))
	}
	return strings.TrimSpace(strings.Join(steps, " / ") + ": " + showText(request.Fields["tool"]) + " " + pyJSON(request.Fields["arguments"], false))
}

func writeAtomically(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	partial := path + ".partial"
	if err := os.WriteFile(partial, data, 0o600); err != nil {
		return err
	}
	return os.Rename(partial, path)
}

func readAnswer(path string) (bool, string) {
	data, err := os.ReadFile(path)
	if err != nil {
		return false, ""
	}
	var value map[string]any
	if json.Unmarshal(data, &value) != nil {
		return false, ""
	}
	said, _ := value["answer"].(string)
	by, _ := value["by"].(string)
	said = strings.ToLower(strings.TrimSpace(said))
	return said == "y" || said == "yes", strings.TrimSpace(by)
}

// askInFolder writes one ask and waits for its answer; a wait that runs
// out, or an ask someone took away, is a no.
func askInFolder(folder string, request *Record, wait time.Duration) *approvalRecord {
	if err := os.MkdirAll(folder, 0o700); err != nil {
		fmt.Fprintf(os.Stderr, "Cannot ask in %s: %v\n", folder, err)
		return nil
	}
	random := make([]byte, 4)
	if _, err := rand.Read(random); err != nil {
		return nil
	}
	id := hex.EncodeToString(random)
	ask, answer := filepath.Join(folder, id+askSuffix), filepath.Join(folder, id+answerSuffix)
	var steps []string
	for _, part := range request.Fields["step"].([]Value) {
		steps = append(steps, showText(part))
	}
	var arguments any
	_ = json.Unmarshal([]byte(pyJSON(request.Fields["arguments"], false)), &arguments)
	says := asksays(request)
	err := writeAtomically(ask, map[string]any{
		"id": id, "says": says, "step": steps, "tool": showText(request.Fields["tool"]),
		"arguments": arguments, "plan_hash": showText(request.Fields["plan_hash"]),
		"action_hash": showText(request.Fields["action_hash"]),
		"asked_at":    time.Now().UTC().Format("2006-01-02T15:04:05+00:00"),
	})
	if err != nil {
		fmt.Fprintf(os.Stderr, "Cannot ask in %s: %v\n", folder, err)
		return nil
	}
	defer os.Remove(ask)
	defer os.Remove(answer)
	fmt.Fprintf(os.Stderr, "Waiting for an answer to ask %s in %s: %s\n  answer with: kosika answer %s %s yes\n", id, folder, says, folder, id)
	ends := time.Now().Add(wait)
	for time.Now().Before(ends) {
		if _, err := os.Stat(answer); err == nil {
			yes, by := readAnswer(answer)
			if !yes {
				break
			}
			if by == "" {
				by = "someone who could write to the folder"
			}
			return &approvalRecord{showText(request.Fields["plan_hash"]), showText(request.Fields["action_hash"]), by, "approved in " + folder}
		}
		if _, err := os.Stat(ask); err != nil {
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	fmt.Fprintln(os.Stderr, "Refused, or no answer came. Nothing was run.")
	return nil
}

func approveWait(option string) time.Duration {
	seconds, err := strconv.ParseFloat(option, 64)
	if err != nil || seconds <= 0 {
		seconds = 600
	}
	return time.Duration(seconds * float64(time.Second))
}

type waitingAsk struct {
	ID      string `json:"id"`
	Says    string `json:"says"`
	AskedAt string `json:"asked_at"`
}

func waitingAsks(folder string) []waitingAsk {
	paths, _ := filepath.Glob(filepath.Join(folder, "*"+askSuffix))
	var asks []waitingAsk
	for _, path := range paths {
		if _, err := os.Stat(strings.TrimSuffix(path, askSuffix) + answerSuffix); err == nil {
			continue
		}
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var ask waitingAsk
		if json.Unmarshal(data, &ask) == nil && ask.ID != "" {
			asks = append(asks, ask)
		}
	}
	sort.Slice(asks, func(i, j int) bool {
		if asks[i].AskedAt != asks[j].AskedAt {
			return asks[i].AskedAt < asks[j].AskedAt
		}
		return asks[i].ID < asks[j].ID
	})
	return asks
}

// answerCommand is `kosika-native answer FOLDER [ID] [yes|no] [--by NAME]`.
func answerCommand(args []string) int {
	by := ""
	var words []string
	for i := 0; i < len(args); i++ {
		if args[i] == "--by" && i+1 < len(args) {
			by, i = args[i+1], i+1
			continue
		}
		words = append(words, args[i])
	}
	if len(words) == 0 || len(words) > 3 {
		fmt.Fprintln(os.Stderr, "usage: kosika-native answer FOLDER [ID] [yes|no] [--by NAME]")
		return 2
	}
	folder, words := words[0], words[1:]
	asks := waitingAsks(folder)
	if len(words) == 0 {
		if len(asks) == 0 {
			fmt.Println("Nothing is waiting.")
		}
		for _, ask := range asks {
			fmt.Printf("%s  %s\n", ask.ID, ask.Says)
		}
		return 0
	}
	reply := strings.ToLower(strings.TrimSpace(words[len(words)-1]))
	if reply != "yes" && reply != "y" && reply != "no" && reply != "n" {
		fmt.Fprintln(os.Stderr, "Not answered: answer yes or no")
		return 2
	}
	id := ""
	if len(words) == 2 {
		id = words[0]
	} else if len(asks) == 1 {
		id = asks[0].ID
	} else {
		var ids []string
		for _, ask := range asks {
			ids = append(ids, ask.ID)
		}
		if len(ids) == 0 {
			fmt.Fprintln(os.Stderr, "Not answered: nothing is waiting")
		} else {
			fmt.Fprintf(os.Stderr, "Not answered: %d asks are waiting; name one: %s\n", len(ids), strings.Join(ids, ", "))
		}
		return 2
	}
	var found *waitingAsk
	for i := range asks {
		if asks[i].ID == id {
			found = &asks[i]
		}
	}
	if found == nil {
		fmt.Fprintf(os.Stderr, "Not answered: no ask %q is waiting in %s\n", id, folder)
		return 2
	}
	if by == "" {
		if who, err := user.Current(); err == nil {
			by = who.Username
		}
	}
	yes := reply == "yes" || reply == "y"
	said := map[bool]string{true: "yes", false: "no"}[yes]
	if err := writeAtomically(filepath.Join(folder, id+answerSuffix), map[string]string{"answer": said, "by": by}); err != nil {
		fmt.Fprintf(os.Stderr, "Not answered: %v\n", err)
		return 2
	}
	fmt.Printf("%s %s: %s\n", map[bool]string{true: "approved", false: "refused"}[yes], id, found.Says)
	return 0
}
