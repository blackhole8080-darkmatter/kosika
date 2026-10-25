#!/usr/bin/env python3
"""The score for "One Run, On Paper" — one room, a low bed, paper hits.

    python score.py --out score.wav

Everything is synthesised from the same event list the picture uses:
the dot's orbit, the wordmark landing, the clips arriving, the three
stamps, the dot wipe, the pull-back and the final period. Hits are
convolution-reverbed with a synthetic room, the bed is ducked under
them, and the mix is mastered to about -16 LUFS.

Requires numpy and the standard library only.
"""
import argparse
import wave

import numpy as np

SR = 48000
DUR = 18.0
N = int(SR * DUR)

# ── the room ───────────────────────────────────────────────────────────
def impulse(seconds=2.4, decay=3.0, seed=3):
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    t = np.arange(n) / SR
    ir = rng.standard_normal(n) * np.exp(-decay * t * 1.7)
    # a little damping: one-pole lowpass
    out, a = np.zeros(n), 0.28
    prev = 0.0
    for i in range(0, n, 1):
        prev = prev + a * (ir[i] - prev)
        out[i] = prev
    return out / np.max(np.abs(out))


IR = impulse()
IR_L = IR
IR_R = impulse(seed=11)


def reverb(x, wet=0.35):
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    n = len(x)
    a = np.convolve(x[:, 0], IR_L)[:n]
    b = np.convolve(x[:, 1], IR_R)[:n]
    return np.stack([x[:, 0] * (1 - wet) + a * wet, x[:, 1] * (1 - wet) + b * wet], axis=1)


def env(n, attack, release, curve=4.0):
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a) ** 1.5
    tail = np.linspace(1, 0, min(r, n)) ** curve
    e[-len(tail):] = tail
    return e


def place(buf, sig, t0, gain=1.0, pan=0.0):
    i = int(t0 * SR)
    if i >= N:
        return
    s = sig[: N - i]
    l = gain * (1.0 - max(0.0, pan)) ** 0.5
    r = gain * (1.0 + min(0.0, pan)) ** 0.5
    buf[i:i + len(s), 0] += s * l
    buf[i:i + len(s), 1] += s * r


def tone(freq, dur, shape="sine", detune=0.0):
    t = np.arange(int(dur * SR)) / SR
    ph = 2 * np.pi * freq * t
    if shape == "sine":
        s = np.sin(ph) + 0.32 * np.sin(ph * 2 + 0.4) + 0.12 * np.sin(ph * 3)
    else:
        s = np.sign(np.sin(ph)) * 0.6 + np.sin(ph) * 0.4
    if detune:
        s = s + np.sin(ph * (1 + detune) * 0.5) * 0.4
    return s


def noise(dur, seed=1):
    return np.random.default_rng(seed).standard_normal(int(dur * SR))


def lowpass(x, a=0.06):
    out = np.zeros_like(x)
    prev = 0.0
    for i in range(len(x)):
        prev += a * (x[i] - prev)
        out[i] = prev
    return out


def bandpass(x, lo=0.02, hi=0.2):
    return lowpass(x, hi) - lowpass(x, lo)


# ── materials ──────────────────────────────────────────────────────────
def thud(seconds=0.9, f0=92, seed=5):
    """a stamp on paper: a body thump, a paper slap, a short room"""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * (f0 - 26 * t) * t) * np.exp(-16 * t)
    slap = bandpass(noise(seconds, seed), 0.15, 0.55) * np.exp(-38 * t) * 0.5
    return (body * 0.9 + slap) * env(n, 0.002, 0.5)


def tick(f=2400, seconds=0.09, seed=9):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    return (bandpass(noise(seconds, seed), 0.25, 0.9) * np.exp(-70 * t) * 0.6
            + np.sin(2 * np.pi * f * t) * np.exp(-55 * t) * 0.25)


def swell(seconds=1.4, seed=2):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    s = bandpass(noise(seconds, seed), 0.02, 0.11)
    return s * np.sin(np.pi * t / seconds) ** 2


def bell(f=880, seconds=1.8):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    s = np.zeros(n)
    for ratio, amp, dec in [(1, 1.0, 3.2), (2.01, .5, 5.0), (3.02, .28, 7.0), (4.7, .12, 9.0)]:
        s += amp * np.sin(2 * np.pi * f * ratio * t) * np.exp(-dec * t)
    return s * 0.5


# ── the mix ────────────────────────────────────────────────────────────
mix = np.zeros((N, 2))
hits = np.zeros((N, 2))
bed = np.zeros((N, 2))

# the bed: one low chord, opening slowly, closing at the end
for f, g0 in [(55.0, .34), (82.5, .20), (110.0, .13), (164.8, .07)]:
    t = np.arange(N) / SR
    s = np.sin(2 * np.pi * f * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.07 * t + f))
    breath = np.clip(np.sin(np.pi * t / DUR) ** .6, 0, 1)
    bed[:, 0] += s * g0 * breath * .5
    bed[:, 1] += np.roll(s, 700) * g0 * breath * .5

# the dot: a soft airy pad that arrives with the first frame
place(bed, bandpass(noise(3.2, 21), .01, .05) * env(int(3.2 * SR), .9, 1.4) * .5, 0.05, .55)
place(bed, tone(220.0, 4.2) * env(int(4.2 * SR), 1.2, 2.0) * .10, 0.2)

# 1.0–2.2 the orbit draws itself: a rising air tone, panned with the dot
place(hits, bandpass(noise(1.5, 31), .03, .13) * env(int(1.5 * SR), .5, .7) * .28, 1.0, .5, -.3)
place(hits, tone(330.0, 1.4) * env(int(1.4 * SR), .7, .5) * .07, 1.05, 1.0, .3)

# 2.2 the wordmark lands
place(hits, thud(1.2, 72, 41), 2.24, .55)
for i, ch in enumerate("KOSIKA"):
    place(hits, tick(1800 + i * 160, .08, 60 + i), 2.22 + i * .055, .10, -.4 + i * .13)

# 3.5 the line draws
place(hits, bandpass(noise(.6, 71), .2, .6) * env(int(.6 * SR), .05, .5) * .10, 3.5, .6)

# 4.3 the ring opens: a filtered rise, then the run's air
place(hits, bandpass(noise(1.2, 81), .02, .3) * env(int(1.2 * SR), .7, .35) * .22, 4.35, .7)

# 5.05 the clips arrive: sparse ticks, not one per clip
for i, t0 in enumerate([5.08, 5.24, 5.37, 5.53, 5.69]):
    place(hits, tick(1500 + i * 210, .07, 90 + i), t0, .07, -.5 + i * .25)

# 5.25–6.55 the playhead sweeps: a slow rising shepard-ish tone
t = np.arange(int(1.4 * SR)) / SR
sweep = np.sin(2 * np.pi * (180 + 90 * t) * t) * np.sin(np.pi * t / 1.4) ** 2 * .08
place(hits, sweep, 5.25, .9, .15)

# 6.58 / 6.78 / 6.98 the three facts
for i, t0 in enumerate([6.58, 6.78, 6.98]):
    place(hits, tick(2600 + i * 300, .12, 120 + i), t0, .14, .2)
    place(hits, thud(.5, 120 + i * 20, 130 + i), t0, .22, .2)

# 7.20 the dot wipe: paper tearing across
place(hits, bandpass(noise(.55, 141), .25, .85) * env(int(.55 * SR), .14, .3) * .30, 7.2, .7, -.2)
place(hits, thud(.7, 98, 151), 7.42, .35, .1)

# 8.35 / 8.9 / 9.45 the cards slide in
for i, t0 in enumerate([8.4, 8.95, 9.5]):
    place(hits, bandpass(noise(.3, 160 + i), .08, .4) * env(int(.3 * SR), .05, .24) * .16, t0, .6, -.35 + i * .35)

# 9.15 / 10.55 / 11.95 the stamps — the loudest things in the film
for i, t0 in enumerate([9.15, 10.55, 11.95]):
    place(hits, thud(1.0, 88 - i * 4, 170 + i), t0, .95, -.25 + i * .25)
    place(hits, tick(1200, .16, 180 + i), t0 + .005, .12, -.25 + i * .25)

# 10.2–12.0 Kosi hops: two small paper steps
place(hits, thud(.28, 190, 191), 10.55, .16, .35)
place(hits, thud(.24, 210, 192), 11.3, .12, .3)

# 12.15 the bubble
place(hits, tick(3000, .09, 195), 12.17, .09, .2)

# 13.4 the pull-back: the room widens
place(hits, bandpass(noise(1.6, 201), .01, .06) * env(int(1.6 * SR), 1.0, .5) * .26, 13.4, .8)

# 13.85–14.2 the numbers
for i, t0 in enumerate([13.85, 13.94, 14.03, 14.12]):
    place(hits, tick(1400 + i * 180, .07, 210 + i), t0, .08, -.3 + i * .2)

# 15.2–15.6 the wipe closes
place(hits, bandpass(noise(.5, 221), .2, .8) * env(int(.5 * SR), .1, .3) * .24, 15.2, .7)

# 15.62 the wordmark drops again
for i, ch in enumerate("KOSIKA"):
    place(hits, tick(2000 + i * 120, .08, 230 + i), 15.62 + i * .05, .09, -.4 + i * .14)

# 16.72 the period: the dot that started the film ends the sentence
place(hits, bell(740, 2.2), 16.72, .30, .05)
place(hits, thud(.6, 110, 251), 16.72, .18, .05)

# ── mix down ───────────────────────────────────────────────────────────
mix = bed * .8 + reverb(hits, .34) * 1.0

# gentle high-shelf removal: one-pole lowpass over the top
def lowpass_st(x, a=0.55):
    out = np.zeros_like(x)
    prev = np.zeros(x.shape[1])
    for i in range(len(x)):
        prev = prev + a * (x[i] - prev)
        out[i] = prev
    return out


mix = mix * .7 + lowpass_st(mix, .5) * .3

# fade in/out
fade_in = int(.25 * SR)
mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
fade_out = int(1.2 * SR)
mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None] ** 2

# loudness: approximate -16 LUFS by RMS, then a soft ceiling at -1.6 dBFS
rms = np.sqrt(np.mean(mix ** 2))
target = 10 ** (-16 / 20)
mix = mix * (target / max(rms, 1e-9))
ceiling = 10 ** (-1.6 / 20)
mix = np.tanh(mix / ceiling) * ceiling

pcm = (np.clip(mix, -1, 1) * 32767).astype(np.int16)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="the score for One Run, On Paper")
    ap.add_argument("--out", default="score.wav")
    args = ap.parse_args()
    inter = np.empty(pcm.shape[0] * 2, dtype=np.int16)
    inter[0::2] = pcm[:, 0]
    inter[1::2] = pcm[:, 1]
    with wave.open(args.out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(inter.tobytes())
    peak = 20 * np.log10(np.max(np.abs(mix)) + 1e-9)
    print(f"wrote {args.out} · {DUR:.1f}s · {SR} Hz · peak {peak:.1f} dBFS")
