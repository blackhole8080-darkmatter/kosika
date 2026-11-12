# Phones as Kosika nodes

`kosika-native phone` connects Kosika to phones. The same program runs on the
laptop and on the phone, because Go builds it for Android (Termux) and for the
Linux inside iSH on an iPhone. No app store, no Python, no paid service.

    laptop (hub)                                 phone (node)
    kosika-native phone ask pixel battery  --->  kosika-native phone node
                                                   ? aryan-laptop asks to read its
                                                     battery level. Allow? [y/N/always]
    pixel: done                            <---  y
    { "percentage": 87, "status": "CHARGING" }

## What keeps it safe

- **Pairing with a one-time code.** The phone shows a code like `KPF7-B28G`.
  It works once, for five minutes. A wrong try uses it up, so a guesser gets
  one guess. Pairing agrees a key with X25519, mixed with the code, so someone
  only listening on the Wi-Fi cannot make the key, and someone in the middle
  cannot either. Both screens show the same fingerprint afterwards.
- **Every message is signed and sealed.** Each message carries a sequence
  number and an Ed25519 signature, and its body is sealed with AES-GCM. A
  replayed or changed message is refused.
- **Both devices keep the same log.** `~/.kosika/phone/logs/NAME.jsonl` is
  hash-chained. `phone log NAME --check` proves no line was changed, every
  signature is the right device's, and every sealed message says what its line
  says.
- **The phone's person says yes.** Every request asks first. A *sense*
  (battery, location, ...) can be granted with `always`. An *action* (notify,
  vibrate, ...) is asked about every time and cannot be granted ahead.
- **A fixed list, never a shell.** The phone runs only the senses and actions
  listed below, each a fixed Termux:API command with checked arguments.
- **Simulated means simulated.** `phone node --simulate` gives made-up values,
  and each reply says `simulated`, so nothing claims an action that was not
  done.

## Android (Termux)

1. Install **Termux** and **Termux:API** from F-Droid (both free), then run
   `pkg install termux-api` in Termux.
2. Put the Android build of `kosika-native` in Termux and make it runnable
   (`chmod +x kosika-native`). To build it on the laptop:
   `GOOS=android GOARCH=arm64 CGO_ENABLED=0 go build -o kosika-native-android .`
3. On the phone: `./kosika-native phone node --name pixel`. It prints its
   address and a pairing code. Add `--confirm dialog` to be asked on screen
   instead of in Termux.
4. On the laptop: `kosika-native phone pair 192.168.1.23 KPF7-B28G --name pixel`.

Then:

    kosika-native phone ping pixel
    kosika-native phone ask pixel battery          # battery | location | clipboard | wifi | device
    kosika-native phone do pixel notify "Kosika" "Quiz at 6pm"
    kosika-native phone do pixel speak "Time to revise graphs"
    kosika-native phone do pixel vibrate 300       # also: toast, open-url, torch on|off, clipboard
    kosika-native phone log pixel --check

### Real control through ADB

Tapping, swiping, typing and opening apps go through Android's own debugging
bridge (free `platform-tools`), over USB or Wireless debugging. The laptop asks
yes before each one and logs it in `logs/adb.jsonl`.

    kosika-native phone adb devices
    kosika-native phone adb tap 540 1200
    kosika-native phone adb swipe 500 1500 500 400
    kosika-native phone adb type "hello from kosika"
    kosika-native phone adb key home               # back | enter | recent | volup | voldown
    kosika-native phone adb open com.android.chrome
    kosika-native phone adb screenshot screen.png

## iPhone (iSH)

Apple lets no app read the battery or location, tap the screen, or run in the
background, so an iPhone cannot be a full node. It can still run Kosika itself.

1. Install **iSH Shell** from the App Store (free). It is a small Linux.
2. Get the iSH build of `kosika-native` from the phone-preview pre-release:

       wget -O /usr/local/bin/kosika-native https://github.com/blackhole8080-darkmatter/kosika/releases/download/phone-preview/kosika-native-ish
       chmod +x /usr/local/bin/kosika-native
       kosika-native phone help

   It is built as `GOOS=linux GOARCH=386 GO386=softfloat CGO_ENABLED=0`.
   Or build it inside iSH, which is slow:
   `apk add go git && git clone https://github.com/blackhole8080-darkmatter/kosika && cd kosika/native && go build -o /usr/local/bin/kosika-native .`
3. Run and test Kosika programs on the phone:
   `kosika-native test discrete_maths.kos`
4. Ask a free cloud model from a program. Put the key in iSH yourself
   (`export KOSIKA_MODEL_API_KEY=...` in `~/.profile`), never in a chat:

       kosika-native run model_answer.kos --input question.json \
         --model llama-3.1-8b-instant \
         --model-url https://api.groq.com/openai/v1 --model-protocol openai

5. An iPhone can be the **hub**: `phone pair`, `phone ask` and `phone do` from
   iSH to an Android phone or a laptop running `phone node`.

## Commands

    phone node [--port 8794] [--name NAME] [--pair] [--confirm terminal|dialog] [--simulate]
    phone pair ADDRESS CODE [--name NAME]
    phone list | id | ping NAME | ask NAME SENSE | do NAME ACTION ARGS...
    phone log NAME [--check] | unpair NAME
    phone adb devices|tap|swipe|type|key|open|screenshot ... [--serial SERIAL]

`KOSIKA_PHONE_HOME` moves where a device keeps its key, peers and logs
(default `~/.kosika/phone`). The key file never leaves the device.

## Next

- Away from home: the same commands over Tailscale (free), by its addresses.
- The phone asking Kosika agents (Kosi, the study agent) on the laptop.
- A `phone` tool for Kosika programs, so agents can ask, with the same yes.
- A native Android app on the same protocol, and iPhone Shortcuts.
