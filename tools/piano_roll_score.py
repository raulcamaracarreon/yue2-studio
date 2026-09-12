"""Modelo intermedio ABC ↔ eventos para el piano roll de YuE2 Studio 4.

El módulo trabaja únicamente con el dialecto ABC nativo de dos voces que ya
valida :mod:`tools.abc_tools`. La rejilla interna usa 256 ticks por negra para
mantener tiempos enteros y facilitar la edición visual.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from fractions import Fraction

from . import abc_tools

PPQ = 256
VOICES = abc_tools.VOICES


def _ticks(value: Fraction) -> int:
    ticks = value * PPQ
    if ticks.denominator != 1:
        raise abc_tools.AbcError(f"El tiempo {value} no cabe exactamente en la rejilla de {PPQ} ticks por negra.")
    return int(ticks)


def _expand_bar_text(line: str) -> list[str]:
    if not line.endswith("|"):
        raise abc_tools.AbcError("La línea musical debe terminar con |")
    result: list[str] = []
    for raw in line[:-1].split("|"):
        raw = raw.strip()
        if not raw:
            raise abc_tools.AbcError("Compás vacío en el ABC")
        rest = re.fullmatch(r"Z([2-4])?", raw)
        result.extend(["Z"] * int(rest.group(1) or 1) if rest else [raw])
    return result


def _sections_from_text(text: str) -> list[dict]:
    """Reconstruye secciones y número de compases a partir de los grupos Vocal/Ins."""
    lines = text.splitlines()
    i = 8
    sections: list[dict] = []
    current: dict | None = None
    bar_cursor = 0

    while i < len(lines):
        while i < len(lines) and lines[i].startswith("% "):
            current = {"name": lines[i][2:].strip() or "section", "bars": 0, "start": bar_cursor}
            sections.append(current)
            i += 1
        if i >= len(lines):
            break
        if current is None:
            current = {"name": "section", "bars": 0, "start": bar_cursor}
            sections.append(current)

        counts = []
        for voice in VOICES:
            if i >= len(lines) or lines[i] != f"V: {voice}":
                raise abc_tools.AbcError(f"Se esperaba V: {voice} al reconstruir secciones")
            i += 1
            while i < len(lines) and lines[i].startswith(("M:", "K:")):
                i += 1
            if i >= len(lines):
                raise abc_tools.AbcError("Falta línea musical al reconstruir secciones")
            bars = _expand_bar_text(lines[i])
            counts.append(len(bars))
            i += 1
        if counts[0] != counts[1]:
            raise abc_tools.AbcError("Las voces tienen diferente número de compases")
        current["bars"] += counts[0]
        bar_cursor += counts[0]

    return sections


def inspect_score(text: str) -> dict:
    """Convierte ABC nativo validado a un JSON serializable para el piano roll."""
    if not isinstance(text, str) or len(text) > 200_000:
        raise abc_tools.AbcError("El score ABC debe ser texto de hasta 200,000 caracteres.")
    text = text.replace("\r\n", "\n").strip() + "\n"
    score = abc_tools.parse(text)
    lines = text.splitlines()

    # En Studio 4 MVP, cambios internos de tonalidad/compás se mantienen en ABC,
    # pero deshabilitan la edición por rejilla hasta implementar ese caso.
    grid_available = not any(
        line.startswith(("M:", "K:")) or "[K:" in line
        for line in lines[8:]
    )

    sections = _sections_from_text(text)
    vocal = score.voices["Vocal"]
    bar_ticks = _ticks(vocal.bars[0][1]) if vocal.bars else 0
    total_ticks = _ticks(vocal.time)

    tracks = {
        name: [
            {"start": _ticks(start), "duration": _ticks(duration), "pitch": int(pitch)}
            for start, pitch, duration in score.voices[name].notes
        ]
        for name in VOICES
    }
    chords = [
        {"start": _ticks(start), "symbol": symbol}
        for start, symbol in vocal.chords
    ]

    meter = lines[2][2:]
    key = lines[7][2:]
    seconds = float(vocal.time * 60 / score.bpm)
    return {
        "abc": text,
        "bpm": score.bpm,
        "meter": meter,
        "unit": lines[3][2:],
        "key": key,
        "bars": len(vocal.bars),
        "seconds": seconds,
        "grid_available": grid_available,
        "keys": list(abc_tools.MAJOR_KEYS + abc_tools.MINOR_KEYS),
        "roll": {
            "ppq": PPQ,
            "tracks": tracks,
            "chords": chords,
            "sections": sections,
            "bar_ticks": bar_ticks,
            "total_ticks": total_ticks,
        },
    }


def _integer(value, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise abc_tools.AbcError(f"{label} debe ser un entero entre {minimum} y {maximum}.")
    return value


def _pitch_text(pitch: int) -> str:
    # Alteraciones explícitas: la altura sonora no depende de la armadura.
    names = ["=C", "^C", "=D", "^D", "=E", "=F", "^F", "=G", "^G", "=A", "^A", "=B"]
    text = names[pitch % 12]
    octave = pitch // 12 - 1
    if octave >= 5:
        return text.lower() + "'" * (octave - 5)
    return text + "," * (4 - octave)


def build_score(data: dict) -> dict:
    """Reconstruye ABC nativo desde el modelo del piano roll y vuelve a validarlo."""
    if not isinstance(data, dict):
        raise abc_tools.AbcError("Se esperaba un objeto de score.")
    data = deepcopy(data)

    bpm = _integer(data["bpm"], "Tempo", 1, 1000)
    key = data["key"]
    if key not in abc_tools.KEY_SIG:
        raise abc_tools.AbcError("Selecciona una tonalidad mayor o menor soportada.")
    meter = data["meter"]
    n, d = abc_tools._meter(meter)
    if 1024 * n % d:
        raise abc_tools.AbcError("El compás no cabe exactamente en la rejilla del editor.")
    bar_ticks = 1024 * n // d

    roll = data["roll"]
    if int(roll.get("ppq", PPQ)) != PPQ:
        raise abc_tools.AbcError(f"La rejilla debe usar {PPQ} ticks por negra.")

    sections = roll["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 256:
        raise abc_tools.AbcError("El score debe contener entre 1 y 256 secciones.")
    bars = sum(_integer(section["bars"], "Compases de sección", 1, 256) for section in sections)
    if bars > 1024:
        raise abc_tools.AbcError("El piano roll admite como máximo 1024 compases.")
    total = bars * bar_ticks

    tracks: dict[str, list[dict]] = {}
    grid = bar_ticks
    for name in VOICES:
        notes = roll["tracks"][name]
        if not isinstance(notes, list) or len(notes) > 20_000:
            raise abc_tools.AbcError(f"{name}: demasiadas notas.")
        notes = sorted(notes, key=lambda note: (note["start"], note["pitch"]))
        end = 0
        for note in notes:
            start = _integer(note["start"], "Posición de nota", 0, max(0, total - 1))
            duration = _integer(note["duration"], "Duración de nota", 1, total)
            _integer(note["pitch"], "Altura MIDI", 0, 127)
            if start < end:
                raise abc_tools.AbcError(f"{name}: las notas se solapan; cada pista es monofónica.")
            end = start + duration
            if end > total:
                raise abc_tools.AbcError(f"{name}: una nota rebasa el final de la canción.")
            grid = math.gcd(grid, math.gcd(start, duration))
        tracks[name] = notes

    chord_map: dict[int, str] = {}
    for chord in roll["chords"]:
        start = _integer(chord["start"], "Posición de acorde", 0, max(0, total - 1))
        symbol = chord["symbol"]
        if not isinstance(symbol, str) or abc_tools.CHORD_RE.fullmatch(symbol) is None:
            raise abc_tools.AbcError(f"Acorde no soportado: {symbol!r}")
        if start in chord_map:
            raise abc_tools.AbcError("Solo puede comenzar un acorde en cada posición.")
        chord_map[start] = symbol
        grid = math.gcd(grid, start)

    # El denominador de L: se adapta a la resolución real del score. El mínimo
    # 1/16 mantiene una notación compacta para los casos habituales de YuE2.
    denominator = max(16, 1024 // (grid & -grid))
    unit_ticks = 1024 // denominator

    def music_bar(name: str, index: int) -> str:
        start, end = index * bar_ticks, (index + 1) * bar_ticks
        notes = [
            note for note in tracks[name]
            if note["start"] < end and note["start"] + note["duration"] > start
        ]
        boundaries = {start, end}
        for note in notes:
            boundaries.add(max(start, note["start"]))
            boundaries.add(min(end, note["start"] + note["duration"]))
        if name == "Vocal":
            boundaries.update(t for t in chord_map if start <= t < end)
        boundaries = sorted(boundaries)
        parts: list[str] = []
        for a, b in zip(boundaries, boundaries[1:]):
            if name == "Vocal" and a in chord_map:
                parts.append(f'"{chord_map[a]}"')
            note = next(
                (item for item in notes if item["start"] <= a < item["start"] + item["duration"]),
                None,
            )
            span = b - a
            if span % unit_ticks:
                raise abc_tools.AbcError(
                    f"El intervalo {span} ticks no puede serializarse con L:1/{denominator}."
                )
            remaining = span // unit_ticks
            for length in sorted(abc_tools.DURATIONS, reverse=True):
                while remaining >= length:
                    remaining -= length
                    tied = note is not None and (
                        remaining > 0 or b < note["start"] + note["duration"]
                    )
                    token = _pitch_text(note["pitch"]) if note else "z"
                    parts.append(token + str(length) + ("-" if tied else ""))
            if remaining:
                raise abc_tools.AbcError("No fue posible representar exactamente una duración del piano roll.")
        return "".join(parts)

    lines = [
        "X:1",
        "T:",
        f"M:{meter}",
        f"L:1/{denominator}",
        f"Q:1/4={bpm}",
        'V: Vocal clef=treble name="Vocal Melody" snm="Vocal"',
        'V: Ins clef=treble name="Ins Melody" snm="Inst."',
        f"K:{key}",
    ]

    offset = 0
    for section in sections:
        name = section["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise abc_tools.AbcError("Cada sección debe tener un nombre de 1 a 100 caracteres.")
        section_bars = _integer(section["bars"], "Compases de sección", 1, 256)
        lines.append("% " + name.replace("\n", " ").replace("\r", " ").strip())
        for start in range(offset, offset + section_bars, 4):
            group_end = min(start + 4, offset + section_bars)
            for voice in VOICES:
                lines.append(f"V: {voice}")
                lines.append("|".join(music_bar(voice, i) for i in range(start, group_end)) + "|")
        offset += section_bars

    return inspect_score("\n".join(lines))
