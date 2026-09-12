#!/usr/bin/env python3
"""Compatibility checker for the native two-voice ABC dialect used by YuE2.

This utility validates the score structure used by YuE2/SheetSage2 and compares
sounding note events before/after symbolic edits. It intentionally supports only
the restricted dialect needed by YuE2 Studio; it is not a general ABC parser.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

VOICES = ("Vocal", "Ins")
DURATIONS = {1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48}
QUALITIES = ("", "m", "dim", "aug", "7", "maj7", "m7", "dim7", "m7b5",
             "sus4", "sus2", "6", "m6", "7sus4", "m(maj7)")
PITCH_NAME = r"[A-G](?:bb|##|b|#)?"
CHORD_RE = re.compile(PITCH_NAME + "(?:" + "|".join(re.escape(q) for q in QUALITIES) + ")(?:/" + PITCH_NAME + ")?")
TOKEN_RE = re.compile(
    r'"(?P<chord>[^"\n]*)"|\[K:(?P<key>[^\]\n]+)\]|'
    r"(?P<acc>\^\^|__|\^|_|=)?(?P<note>[A-Ga-gz])"
    r"(?P<oct>[,']*)(?P<duration>[0-9]*)(?P<tie>-?)"
)
NATURAL = dict(zip("CDEFGAB", (0, 2, 4, 5, 7, 9, 11)))
MAJOR_KEYS = ("Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#")
MINOR_KEYS = ("Abm", "Ebm", "Bbm", "Fm", "Cm", "Gm", "Dm", "Am", "Em", "Bm", "F#m", "C#m", "G#m", "D#m", "A#m")
KEY_SIG = {**dict(zip(MAJOR_KEYS, range(-7, 8))), **dict(zip(MINOR_KEYS, range(-7, 8)))}


class AbcError(ValueError):
    pass


def _fail(condition, message):
    if condition:
        raise AbcError(message)


def _meter(text):
    m = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", text)
    _fail(m is None, f"Unsupported meter {text!r}")
    n, d = map(int, m.groups())
    _fail(d & (d - 1) != 0, f"Meter denominator must be a power of two: {d}")
    return n, d


def _key_accidentals(key):
    _fail(key not in KEY_SIG, f"Unsupported key {key!r}")
    count = KEY_SIG[key]
    result = {x: 0 for x in NATURAL}
    order = "FCGDAEB" if count > 0 else "BEADGCF"
    for letter in order[:abs(count)]:
        result[letter] = 1 if count > 0 else -1
    return result


@dataclass
class Voice:
    meter: tuple[int, int]
    key: str
    time: Fraction = Fraction(0)
    notes: list = field(default_factory=list)
    bars: list = field(default_factory=list)
    chords: list = field(default_factory=list)
    keys: list = field(default_factory=list)
    pending: tuple | None = None


@dataclass
class Score:
    unit: Fraction
    bpm: int
    voices: dict[str, Voice]


def _parse_bar(body, voice, unit, context):
    n, d = voice.meter
    bar_len = Fraction(4 * n, d)
    bar_start = voice.time
    offset = Fraction(0)
    accidentals = {}
    if body == "Z":
        _fail(voice.pending is not None, f"{context}: tie enters a full-measure rest")
        offset = bar_len
    else:
        pos = 0
        while pos < len(body):
            if body[pos].isspace():
                pos += 1
                continue
            m = TOKEN_RE.match(body, pos)
            _fail(m is None, f"{context}: unsupported token near {body[pos:pos+24]!r}")
            pos = m.end()
            if m.group("chord") is not None:
                chord = m.group("chord")
                _fail(CHORD_RE.fullmatch(chord) is None, f"{context}: unsupported chord {chord!r}")
                voice.chords.append((bar_start + offset, chord))
                continue
            if m.group("key") is not None:
                key = m.group("key")
                _key_accidentals(key)
                voice.key = key
                voice.keys.append((bar_start + offset, key))
                accidentals = {}
                continue

            note = m.group("note")
            units = int(m.group("duration") or "1")
            _fail(units not in DURATIONS, f"{context}: unsupported duration {units}")
            duration = units * unit * 4
            _fail(offset + duration > bar_len, f"{context}: note/rest exceeds measure")
            acc = m.group("acc")
            octaves = m.group("oct")
            tie = m.group("tie")
            _fail("," in octaves and "'" in octaves, f"{context}: mixed octave marks")

            if note == "z":
                _fail(bool(acc or octaves or tie), f"{context}: invalid rest decoration")
                _fail(voice.pending is not None, f"{context}: tie enters a rest")
            else:
                letter = note.upper()
                written = 60 + NATURAL[letter] + (12 if note.islower() else 0)
                written += 12 * (octaves.count("'") - octaves.count(","))
                alteration = accidentals.get(letter, _key_accidentals(voice.key)[letter])
                if acc:
                    alteration = {"=": 0, "_": -1, "__": -2, "^": 1, "^^": 2}[acc]
                    accidentals[letter] = alteration
                pitch = written + alteration
                _fail(not 0 <= pitch <= 127, f"{context}: MIDI pitch out of range: {pitch}")
                if voice.pending is not None:
                    old_pitch, old_written = voice.pending
                    if not acc and written == old_written:
                        pitch = old_pitch
                    _fail(pitch != old_pitch, f"{context}: tie changes pitch")
                    voice.notes[-1][2] += duration
                else:
                    voice.notes.append([bar_start + offset, pitch, duration])
                voice.pending = (pitch, written) if tie else None
            offset += duration

    _fail(offset != bar_len, f"{context}: measure duration {offset} != {bar_len}")
    voice.bars.append((bar_start, bar_len, voice.meter))
    voice.time += bar_len


def parse(text: str) -> Score:
    lines = text.splitlines()
    _fail(len(lines) < 12, "Incomplete YuE2 ABC")
    _fail(lines[:2] != ["X:1", "T:"], "Expected X:1 and a blank T: field")
    _fail(not lines[2].startswith("M:"), "Missing M: header")
    meter = _meter(lines[2][2:])
    lm = re.fullmatch(r"L:1/([1-9][0-9]*)", lines[3])
    _fail(lm is None, "Expected L:1/<power-of-two>")
    denom = int(lm.group(1))
    _fail(denom & (denom - 1) != 0, "L: denominator must be a power of two")
    unit = Fraction(1, denom)
    qm = re.fullmatch(r"Q:1/4=([1-9][0-9]*)", lines[4])
    _fail(qm is None, "Expected Q:1/4=<BPM>")
    expected = ['V: Vocal clef=treble name="Vocal Melody" snm="Vocal"',
                'V: Ins clef=treble name="Ins Melody" snm="Inst."']
    _fail(lines[5:7] != expected, "Preserve native Vocal/Ins voice declarations")
    _fail(not lines[7].startswith("K:"), "Missing K: header")
    key = lines[7][2:]
    _key_accidentals(key)
    voices = {name: Voice(meter, key, keys=[(Fraction(0), key)]) for name in VOICES}

    i = 8
    group = 0
    while i < len(lines):
        while i < len(lines) and lines[i].startswith("% "):
            i += 1
        _fail(i >= len(lines), "Section comment without following music")
        group += 1
        bar_counts = []
        for name in VOICES:
            _fail(i >= len(lines) or lines[i] != f"V: {name}", f"group {group}: expected V: {name}")
            i += 1
            voice = voices[name]
            seen = set()
            while i < len(lines) and lines[i].startswith(("M:", "K:")):
                field, value = lines[i].split(":", 1)
                _fail(field in seen, f"group {group}: duplicate {field}: field")
                seen.add(field)
                if field == "M":
                    voice.meter = _meter(value)
                else:
                    _key_accidentals(value)
                    voice.key = value
                    voice.keys.append((voice.time, value))
                i += 1
            _fail(i >= len(lines), f"group {group}: missing music line")
            music = lines[i]
            i += 1
            _fail(not music.endswith("|"), f"group {group}: music line must end with |")
            bars = []
            for raw in music[:-1].split("|"):
                raw = raw.strip()
                _fail(not raw, f"group {group}: empty measure")
                z = re.fullmatch(r"Z([2-4])?", raw)
                bars.extend(["Z"] * int(z.group(1) or "1") if z else [raw])
            _fail(not 1 <= len(bars) <= 4, f"group {group}: expected 1-4 measures")
            bar_counts.append(len(bars))
            for n, bar in enumerate(bars, 1):
                _parse_bar(bar, voice, unit, f"group {group}, {name}, bar {n}")
        _fail(bar_counts[0] != bar_counts[1], f"group {group}: voice measure counts differ")

    for name, voice in voices.items():
        _fail(voice.pending is not None, f"{name}: unresolved final tie")
    _fail(bool(voices["Ins"].chords), "Chord symbols must be in Vocal, not Ins")
    _fail(voices["Vocal"].bars != voices["Ins"].bars, "Voice time grids differ")
    _fail(voices["Vocal"].keys != voices["Ins"].keys, "Voice key timelines differ")
    return Score(unit, int(qm.group(1)), voices)


def _jsonable(v):
    if isinstance(v, Fraction):
        return str(v)
    raise TypeError(type(v).__name__)


def report(score):
    return {
        "scope": "YuE2 Studio restricted-dialect structural check",
        "bpm": score.bpm,
        "unit_length": str(score.unit),
        "voices": {
            name: {
                "sounding_notes": len(v.notes),
                "measures": len(v.bars),
                "notes": [{"onset_quarters": str(t), "midi_pitch": p, "duration_quarters": str(d)} for t, p, d in v.notes],
                "chords": v.chords,
                "keys": v.keys,
                "bars": v.bars,
            }
            for name, v in score.voices.items()
        },
    }


def compare(before, after, voices=VOICES, allow_tempo_change=False):
    differences = []
    if before.bpm != after.bpm and not allow_tempo_change:
        differences.append("quarter-note tempo differs")
    for name in voices:
        a, b = before.voices[name], after.voices[name]
        if a.bars != b.bars:
            differences.append(f"{name}: bar meter/time grid differs")
        if a.notes != b.notes:
            differences.append(f"{name}: sounding pitch/onset/duration differs")
    return {
        "match": not differences,
        "compared_voices": list(voices),
        "tempo_change_allowed": allow_tempo_change,
        "differences": differences,
        "scope": "exact symbolic note events and meter grid",
    }


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    ins = sub.add_parser("inspect")
    ins.add_argument("score", type=Path)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("before", type=Path)
    cmp_.add_argument("after", type=Path)
    cmp_.add_argument("--voices", choices=("both", *VOICES), default="both")
    cmp_.add_argument("--allow-tempo-change", action="store_true")
    args = ap.parse_args()
    try:
        if args.command == "inspect":
            data = report(parse(args.score.read_text(encoding="utf-8")))
            rc = 0
        else:
            names = VOICES if args.voices == "both" else (args.voices,)
            data = compare(parse(args.before.read_text(encoding="utf-8")), parse(args.after.read_text(encoding="utf-8")), names, args.allow_tempo_change)
            rc = 0 if data["match"] else 1
        print(json.dumps(data, indent=2, ensure_ascii=False, default=_jsonable))
        return rc
    except (AbcError, OSError) as exc:
        print(f"ABC check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
