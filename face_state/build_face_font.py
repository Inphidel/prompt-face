"""Build the Face Text font the painter uses.

fonts/FaceText-Regular.ttf already contains the characters the default
faces need, so a new install does not have to run this. Run it again when
a face uses a character the file does not have yet.

The painter reads that file directly. Gallery shapes come from the Noto
fonts. The CP437 ANSI-art set, including the VGA smiles and box drawing,
comes from FreeMono so the line stays on one monospaced alphabet. Both
families have to be installed and visible to fontconfig before a rebuild.
See THIRD_PARTY.md for the font licenses.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unicodedata
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, TTFont

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "face_state" / "text_faces.json"
BINDINGS = ROOT / "textfaces.json"
OUTPUT = ROOT / "fonts" / "FaceText-Regular.ttf"
TARGET_UPM = 2048

# Earlier families win, so the plain Noto Sans shapes stay in place.
PREFER = (
    "Noto Sans",
    "Noto Sans Symbols",
    "Noto Sans Symbols2",
    "Noto Sans Mono",
    "Noto Sans CJK JP",
    "Noto Sans Math",
    "Noto Sans Mono CJK JP",
)
# CP437 is the ANSI-art alphabet. FreeMono is monospaced and has the whole set.
# The first 31 bytes are the VGA smiles, suits, and arrows. Python's cp437
# codec leaves those as controls, so they are listed as their Unicode faces.
ANSI_FAMILY = "FreeMono"
HAND_FAMILY = "Noto Sans Symbols2"
HAND_CHARS = "👍👈👉✌✋"
ANSI_GRAPHICS = "☺☻♥♦♣♠•◘○◙♂♀♪♫☼►◄↕‼¶§▬↨↑↓→←∟↔▲▼"


def strings_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found = []
        for item in value.values():
            found.extend(strings_in(item))
        return found
    return []


def codepoints() -> tuple[list[int], set[int], set[int]]:
    catalog = json.loads(CATALOG.read_text())
    texts = [face["text"] for face in catalog["faces"]]
    if BINDINGS.exists():
        bindings = json.loads(BINDINGS.read_text())
        texts.extend(strings_in(bindings.get("slots", {})))
        texts.extend(strings_in(bindings.get("rules", {})))
    ansi = {ord(char) for char in bytes(range(32, 255)).decode("cp437") if char != "\x7f"}
    ansi.update(ord(char) for char in ANSI_GRAPHICS)
    needed = {ord(char) for text in texts for char in text}
    needed.update(ansi)
    hands = {ord(char) for char in HAND_CHARS}
    needed.update(hands)
    return sorted(needed), ansi, hands


def font_index() -> list[tuple[str, str, list[tuple[int, int]]]]:
    raw = subprocess.check_output(
        ["fc-list", "-f", "%{family}\t%{file}\t%{charset}\n"],
        text=True,
        errors="replace",
    )
    fonts = []
    for line in raw.splitlines():
        family, path, charset = line.split("\t", 2)
        family = family.split(",")[0]
        lowered = path.lower()
        if any(token in lowered for token in ("bold", "italic", "black", "medium", "light", "thin", "condensed")):
            continue
        ranges = []
        for token in charset.split():
            if "-" in token:
                start, end = token.split("-", 1)
                ranges.append((int(start, 16), int(end, 16)))
            elif token:
                value = int(token, 16)
                ranges.append((value, value))
        ranges.sort()
        fonts.append((family, path, ranges))
    return fonts


def covers(ranges: list[tuple[int, int]], codepoint: int) -> bool:
    low, high = 0, len(ranges) - 1
    while low <= high:
        mid = (low + high) // 2
        start, end = ranges[mid]
        if codepoint < start:
            high = mid - 1
        elif codepoint > end:
            low = mid + 1
        else:
            return True
    return False


def open_face(path: str, family: str) -> TTFont:
    if path.lower().endswith(".ttc"):
        collection = TTCollection(path)
        for font in collection.fonts:
            names = set()
            for record in font["name"].names:
                if record.nameID in (1, 16):
                    try:
                        names.add(record.toUnicode())
                    except Exception:
                        continue
            if family in names:
                return font
        raise RuntimeError(f"{family} was not in {path}")
    return TTFont(path)


def draw_glyph(font: TTFont, glyph_name: str, factor: float):
    glyph_set = font.getGlyphSet()
    recorder = DecomposingRecordingPen(glyph_set)
    glyph_set[glyph_name].draw(recorder)
    pen = TTGlyphPen(None)
    target = Cu2QuPen(pen, 1) if ("CFF " in font or "CFF2" in font) else pen
    recorder.replay(target)
    glyph = pen.glyph()
    if factor != 1 and glyph.coordinates:
        for index, (x_pos, y_pos) in enumerate(glyph.coordinates):
            glyph.coordinates[index] = (round(x_pos * factor), round(y_pos * factor))
    width, left = font["hmtx"].metrics[glyph_name]
    return glyph, int(round(width * factor)), int(round(left * factor))


def main() -> None:
    needed, ansi, hands = codepoints()
    fonts = font_index()
    by_family: dict[str, list[tuple[str, list[tuple[int, int]]]]] = {}
    for family, path, ranges in fonts:
        by_family.setdefault(family, []).append((path, ranges))
    order = [name for name in PREFER if name in by_family]
    order.extend(name for name in by_family if name.startswith("Noto ") and name not in order)
    order.extend(name for name in by_family if name not in order)
    if ANSI_FAMILY not in by_family:
        raise SystemExit(f"{ANSI_FAMILY} is not installed")

    opened: dict[tuple[str, str], TTFont | None] = {}

    def face_for(family: str, path: str) -> TTFont | None:
        key = (family, path)
        if key not in opened:
            try:
                opened[key] = open_face(path, family)
            except Exception as error:
                print(f"skip {family} {path}: {error}", file=sys.stderr)
                opened[key] = None
        return opened[key]

    glyphs = {}
    metrics = {}
    cmap = {}
    sources: dict[str, int] = {}
    missing = []
    empty = []
    y_min = 0
    y_max = 0
    width_max = 0

    noto = face_for("Noto Sans", by_family["Noto Sans"][0][0])
    if noto is None:
        raise SystemExit("Noto Sans Regular is not installed")
    base_factor = TARGET_UPM / noto["head"].unitsPerEm
    glyphs[".notdef"], width, left = draw_glyph(noto, ".notdef", base_factor)
    metrics[".notdef"] = (max(width, 1), left)

    for codepoint in needed:
        drawn = None
        chosen = None
        families = order
        if codepoint in hands:
            families = [HAND_FAMILY, *[name for name in order if name != HAND_FAMILY]]
        elif codepoint in ansi:
            families = [ANSI_FAMILY, *[name for name in order if name != ANSI_FAMILY]]
        for family in families:
            for path, ranges in by_family[family]:
                if not covers(ranges, codepoint):
                    continue
                font = face_for(family, path)
                if font is None:
                    continue
                source_cmap = font.getBestCmap() or {}
                glyph_name = source_cmap.get(codepoint)
                if not glyph_name:
                    continue
                factor = TARGET_UPM / font["head"].unitsPerEm
                try:
                    drawn = draw_glyph(font, glyph_name, factor)
                except Exception as error:
                    print(f"skip U+{codepoint:04X} from {family}: {error}", file=sys.stderr)
                    continue
                chosen = family
                break
            if drawn is not None:
                break
        name = f"uni{codepoint:04X}" if codepoint <= 0xFFFF else f"u{codepoint:05X}"
        if drawn is None:
            missing.append(codepoint)
            continue
        glyph, width, left = drawn
        glyphs[name] = glyph
        metrics[name] = (max(width, 0), left)
        cmap[codepoint] = name
        sources[chosen] = sources.get(chosen, 0) + 1
        width_max = max(width_max, width)
        if glyph.coordinates:
            ys = [point[1] for point in glyph.coordinates]
            y_min = min(y_min, min(ys))
            y_max = max(y_max, max(ys))
        elif unicodedata.category(chr(codepoint))[0] not in ("Z", "C", "M"):
            empty.append(codepoint)

    if missing:
        sample = " ".join(f"U+{codepoint:04X}" for codepoint in missing[:20])
        raise SystemExit(f"missing {len(missing)} glyphs: {sample}")

    builder = FontBuilder(TARGET_UPM, isTTF=True)
    order_names = [".notdef", *[cmap[codepoint] for codepoint in needed if codepoint in cmap]]
    builder.setupGlyphOrder(order_names)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf({name: glyphs[name] for name in order_names})
    builder.setupHorizontalMetrics({name: metrics[name] for name in order_names})
    ascent = max(y_max, 1)
    descent = min(y_min, 0)
    builder.setupHorizontalHeader(
        ascent=ascent,
        descent=descent,
        lineGap=0,
        advanceWidthMax=max(width_max, 1),
    )
    builder.setupOS2(
        fsSelection=0x40 | 0x80,
        sTypoAscender=ascent,
        sTypoDescender=descent,
        sTypoLineGap=0,
        usWinAscent=ascent,
        usWinDescent=abs(descent),
        achVendID="FACE",
    )
    builder.setupPost()
    builder.setupNameTable({
        "familyName": "Face Text",
        "styleName": "Regular",
        "uniqueFontIdentifier": "Face Text Regular",
        "fullName": "Face Text Regular",
        "psName": "FaceText-Regular",
        "version": "Version 1.000",
        "typographicFamily": "Face Text",
        "typographicSubfamily": "Regular",
    })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    builder.save(str(OUTPUT))
    print(f"wrote {OUTPUT} glyphs={len(cmap)} ascent={ascent} descent={descent}")
    for family, count in sorted(sources.items(), key=lambda item: -item[1]):
        print(f"  {count:4} {family}")
    if empty:
        print("no outline:", " ".join(f"U+{codepoint:04X}" for codepoint in empty[:30]))


if __name__ == "__main__":
    main()
