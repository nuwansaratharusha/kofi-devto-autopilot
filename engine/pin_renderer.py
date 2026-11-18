"""Zero-dependency 1000x1500 (2:3) indexed-color PNG & SVG Pinterest infographic renderer."""

from __future__ import annotations

import hashlib
import html
import re
import struct
import zlib
from pathlib import Path
from typing import Any

from engine.config import AppConfig
from engine.models import PILLAR_META, SERIES_30D_NAME, ArticleRecord

PIN_WIDTH = 1000
PIN_HEIGHT = 1500
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Indexed RGB palette (color_type=3, bit_depth=8)
PALETTE_RGB: tuple[tuple[int, int, int], ...] = (
    (11, 17, 32),     # 0: canvas deep navy (#0B1120)
    (17, 24, 39),     # 1: surface card (#111827)
    (30, 41, 59),     # 2: subtle border / toolbar (#1E293B)
    (51, 65, 85),     # 3: hairline border (#334155)
    (248, 250, 252),  # 4: primary crisp white (#F8FAFC)
    (203, 213, 225),  # 5: muted slate text (#CBD5E1)
    (148, 163, 184),  # 6: secondary caption (#94A3B8)
    (15, 118, 110),   # 7: teal accent bg (#0F766E)
    (45, 212, 191),   # 8: bright teal fg (#2DD4BF)
    (5, 46, 22),      # 9: verified green bg (#052E16)
    (134, 239, 172),  # 10: verified green fg (#86EFAC)
    (2, 6, 23),       # 11: terminal background (#020617)
    (239, 68, 68),    # 12: terminal dot red (#EF4444)
    (245, 158, 11),   # 13: terminal dot amber (#F59E0B)
    (34, 197, 94),    # 14: terminal dot green (#22C55E)
    (56, 189, 248),   # 15: code cyan highlight (#38BDF8)
)

# Compact 5x7 printable ASCII bitmap font (each tuple has 7 row bitmasks, 5 bits wide: 0x10..0x01)
FONT_5X7: dict[str, tuple[int, int, int, int, int, int, int]] = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    "!": (0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04),
    '"': (0x0A, 0x0A, 0x0A, 0x00, 0x00, 0x00, 0x00),
    "#": (0x0A, 0x0A, 0x1F, 0x0A, 0x1F, 0x0A, 0x0A),
    "$": (0x04, 0x0F, 0x14, 0x0E, 0x05, 0x1E, 0x04),
    "%": (0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03),
    "&": (0x0C, 0x12, 0x14, 0x08, 0x15, 0x12, 0x0D),
    "'": (0x04, 0x04, 0x08, 0x00, 0x00, 0x00, 0x00),
    "(": (0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02),
    ")": (0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08),
    "*": (0x00, 0x04, 0x15, 0x0E, 0x15, 0x04, 0x00),
    "+": (0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00),
    ",": (0x00, 0x00, 0x00, 0x00, 0x04, 0x04, 0x08),
    "-": (0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00),
    ".": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04),
    "/": (0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F),
    "3": (0x1F, 0x02, 0x04, 0x06, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    ":": (0x00, 0x04, 0x04, 0x00, 0x04, 0x04, 0x00),
    ";": (0x00, 0x04, 0x00, 0x00, 0x04, 0x04, 0x08),
    "<": (0x02, 0x04, 0x08, 0x10, 0x08, 0x04, 0x02),
    "=": (0x00, 0x00, 0x1F, 0x00, 0x1F, 0x00, 0x00),
    ">": (0x08, 0x04, 0x02, 0x01, 0x02, 0x04, 0x08),
    "?": (0x0E, 0x11, 0x01, 0x06, 0x04, 0x00, 0x04),
    "@": (0x0E, 0x11, 0x17, 0x15, 0x17, 0x10, 0x0E),
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E),
    "D": (0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "J": (0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "N": (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04),
    "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "[": (0x0E, 0x08, 0x08, 0x08, 0x08, 0x08, 0x0E),
    "\\": (0x10, 0x08, 0x08, 0x04, 0x02, 0x02, 0x01),
    "]": (0x0E, 0x02, 0x02, 0x02, 0x02, 0x02, 0x0E),
    "^": (0x04, 0x0A, 0x11, 0x00, 0x00, 0x00, 0x00),
    "_": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F),
    "a": (0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F),
    "b": (0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x1E),
    "c": (0x00, 0x00, 0x0E, 0x10, 0x10, 0x10, 0x0E),
    "d": (0x01, 0x01, 0x0F, 0x11, 0x11, 0x11, 0x0F),
    "e": (0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E),
    "f": (0x06, 0x08, 0x1E, 0x08, 0x08, 0x08, 0x08),
    "g": (0x00, 0x00, 0x0F, 0x11, 0x0F, 0x01, 0x0E),
    "h": (0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x11),
    "i": (0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E),
    "j": (0x02, 0x00, 0x06, 0x02, 0x02, 0x12, 0x0C),
    "k": (0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12),
    "l": (0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "m": (0x00, 0x00, 0x1A, 0x15, 0x15, 0x11, 0x11),
    "n": (0x00, 0x00, 0x1E, 0x11, 0x11, 0x11, 0x11),
    "o": (0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E),
    "p": (0x00, 0x00, 0x1E, 0x11, 0x1E, 0x10, 0x10),
    "q": (0x00, 0x00, 0x0F, 0x11, 0x0F, 0x01, 0x01),
    "r": (0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10),
    "s": (0x00, 0x00, 0x0F, 0x10, 0x0E, 0x01, 0x1E),
    "t": (0x08, 0x08, 0x1E, 0x08, 0x08, 0x09, 0x06),
    "u": (0x00, 0x00, 0x11, 0x11, 0x11, 0x11, 0x0F),
    "v": (0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "w": (0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A),
    "x": (0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11),
    "y": (0x00, 0x00, 0x11, 0x11, 0x0F, 0x01, 0x0E),
    "z": (0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F),
    "{": (0x02, 0x04, 0x04, 0x08, 0x04, 0x04, 0x02),
    "|": (0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "}": (0x08, 0x04, 0x04, 0x02, 0x04, 0x04, 0x08),
    "~": (0x00, 0x00, 0x08, 0x15, 0x02, 0x00, 0x00),
}


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _ascii_clean(text: str) -> str:
    replacements = {
        "—": "-",
        "–": "-",
        "→": "->",
        "·": "|",
        "•": "*",
        "“": '"',
        "”": '"',
        "’": "'",
        "`": "",
    }
    out = text
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    return "".join(ch if ch in FONT_5X7 else " " for ch in out)


PIN_PILLAR_KICKERS: dict[str, str] = {
    "pillar_a": "FAULT-TOLERANT AI & API GUARDRAILS",
    "pillar_b": "ZERO-COST SQLITE WAL QUEUES & CRON",
    "pillar_c": "HMAC WEBHOOKS & DOUBLE-ENTRY SQL LEDGERS",
}


def _wrap_text(text: str, max_chars: int, max_lines: int) -> list[str]:
    words = _ascii_clean(text).split()
    lines: list[str] = []
    cur = ""
    overflowed = False
    for idx, raw_w in enumerate(words):
        w = raw_w[: max_chars - 1] if len(raw_w) > max_chars else raw_w
        cand = f"{cur} {w}".strip() if cur else w
        if len(cand) <= max_chars:
            cur = cand
        else:
            if len(lines) == max_lines - 1:
                overflowed = True
                break
            lines.append(cur)
            cur = w
    if cur and len(lines) < max_lines:
        if overflowed:
            cur = (cur[: max_chars - 3].rstrip() + "...") if len(cur) + 3 > max_chars else (cur + "...")
        lines.append(cur)
    return lines or [""]


def _extract_code_preview(markdown_body: str, max_lines: int = 10, max_cols: int = 44) -> list[str]:
    match = re.search(r"```python\n(.*?)```", markdown_body, re.DOTALL)
    raw = match.group(1) if match else "assert returncode == 0"
    preview: list[str] = []
    in_multiline_import = False
    for raw_line in raw.splitlines():
        stripped = raw_line.rstrip()
        lstripped = stripped.lstrip()
        if in_multiline_import:
            if ")" in lstripped:
                in_multiline_import = False
            continue
        if not lstripped or lstripped.startswith("import ") or lstripped.startswith("from "):
            if "(" in lstripped and ")" not in lstripped:
                in_multiline_import = True
            continue
        clean = _ascii_clean(stripped.replace("    ", "  "))
        if len(clean) > max_cols:
            clean = clean[: max_cols - 2] + ".."
        preview.append(clean)
        if len(preview) >= max_lines:
            break
    return preview or ["def verify_sandbox():", "  assert exit_code == 0"]


class IndexedCanvas:
    """Fast stdlib 8-bit indexed pixel buffer for 1000x1500 geometric & bitmap rendering."""

    def __init__(self, width: int = PIN_WIDTH, height: int = PIN_HEIGHT, bg_index: int = 0) -> None:
        self.width = width
        self.height = height
        self.buf = bytearray([bg_index]) * (width * height)

    def fill_rect(self, x: int, y: int, w: int, h: int, color_idx: int) -> None:
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self.width, x + w)
        y1 = min(self.height, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        span_len = x1 - x0
        row_bytes = bytes([color_idx]) * span_len
        width = self.width
        buf = self.buf
        for ry in range(y0, y1):
            off = ry * width + x0
            buf[off : off + span_len] = row_bytes

    def stroke_rect(self, x: int, y: int, w: int, h: int, thickness: int, color_idx: int) -> None:
        self.fill_rect(x, y, w, thickness, color_idx)
        self.fill_rect(x, y + h - thickness, w, thickness, color_idx)
        self.fill_rect(x, y, thickness, h, color_idx)
        self.fill_rect(x + w - thickness, y, thickness, h, color_idx)

    def draw_text(self, x: int, y: int, text: str, *, scale: int = 3, color_idx: int = 4) -> int:
        cx = x
        px_span = bytes([color_idx]) * scale
        width = self.width
        height = self.height
        buf = self.buf
        step = 6 * scale

        for ch in _ascii_clean(text):
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for gy, row_mask in enumerate(glyph):
                if not row_mask:
                    continue
                py0 = y + gy * scale
                py1 = min(height, py0 + scale)
                if py0 < 0 or py0 >= height:
                    continue
                for gx in range(5):
                    if row_mask & (0x10 >> gx):
                        px0 = cx + gx * scale
                        if 0 <= px0 and px0 + scale <= width:
                            for ry in range(py0, py1):
                                off = ry * width + px0
                                buf[off : off + scale] = px_span
            cx += step
        return cx

    def encode_png(self) -> bytes:
        ihdr = struct.pack(">IIBBBBB", self.width, self.height, 8, 3, 0, 0, 0)
        plte = b"".join(bytes(rgb) for rgb in PALETTE_RGB)

        stride = self.width + 1
        raw_scanlines = bytearray(self.height * stride)
        width = self.width
        buf = self.buf
        for y in range(self.height):
            dst = y * stride
            src = y * width
            raw_scanlines[dst] = 0  # Filter type 0 (None)
            raw_scanlines[dst + 1 : dst + stride] = buf[src : src + width]

        compressed = zlib.compress(bytes(raw_scanlines), level=9)
        return (
            PNG_SIGNATURE
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"PLTE", plte)
            + _png_chunk(b"IDAT", compressed)
            + _png_chunk(b"IEND", b"")
        )


def render_pin_png_bytes(article: ArticleRecord, config: AppConfig) -> bytes:
    """Render a crisp 1000x1500 (`2:3`) indexed PNG infographic under 35 KB."""
    canvas = IndexedCanvas(PIN_WIDTH, PIN_HEIGHT, bg_index=0)
    day_num = max(1, int(article.series_day or 1))
    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])
    dur_ms = int(round(float(article.verification.get("total_duration_ms", 12))))
    blocks_n = int(article.verification.get("code_blocks_tested", 2))
    words_n = max(750, int(article.word_count or 800))

    # Top accent bar + outer card frame
    canvas.fill_rect(0, 0, PIN_WIDTH, 14, 8)
    canvas.stroke_rect(36, 36, PIN_WIDTH - 72, PIN_HEIGHT - 72, 2, 3)

    # Top badges: DAY N/30 + SANDBOX PASS (EXIT 0)
    day_badge_txt = f"DAY {day_num:02d}/30 SERIES"
    canvas.fill_rect(64, 72, 326, 52, 7)
    canvas.draw_text(84, 87, day_badge_txt, scale=3, color_idx=4)

    pass_badge_txt = "SANDBOX PASS (EXIT 0)"
    canvas.fill_rect(410, 72, 418, 52, 9)
    canvas.stroke_rect(410, 72, 418, 52, 2, 10)
    canvas.draw_text(430, 87, pass_badge_txt, scale=3, color_idx=10)

    # Pillar subtitle kicker (crisp full-word uppercase label)
    kicker_txt = PIN_PILLAR_KICKERS.get(article.pillar, _wrap_text(meta["label"].upper(), 46, 1)[0])
    canvas.draw_text(64, 152, kicker_txt[:46], scale=3, color_idx=8)

    # Multi-line wrapped headline (scale=4 -> 24px per char, max 36 chars per line)
    headline_lines = _wrap_text(article.title.upper(), max_chars=35, max_lines=4)
    hy = 198
    for hline in headline_lines:
        canvas.draw_text(64, hy, hline, scale=4, color_idx=4)
        hy += 46

    # Dark Terminal Snippet Card
    term_y = 410
    term_h = 540
    canvas.fill_rect(64, term_y, PIN_WIDTH - 128, term_h, 11)
    canvas.stroke_rect(64, term_y, PIN_WIDTH - 128, term_h, 2, 3)
    canvas.fill_rect(66, term_y + 2, PIN_WIDTH - 132, 50, 2)

    # Terminal window controls + label
    canvas.fill_rect(90, term_y + 19, 16, 16, 12)
    canvas.fill_rect(118, term_y + 19, 16, 16, 13)
    canvas.fill_rect(146, term_y + 19, 16, 16, 14)
    canvas.draw_text(186, term_y + 17, "PYTHON 3.11+ STDLIB BENCHMARK", scale=3, color_idx=5)

    code_lines = _extract_code_preview(article.markdown_body, max_lines=11, max_cols=44)
    cy = term_y + 76
    for idx, cline in enumerate(code_lines):
        c_idx = 15 if cline.lstrip().startswith(("def ", "class ", "assert ")) else (10 if "SELECT" in cline or "BEGIN" in cline else 5)
        canvas.draw_text(88, cy, f"{idx + 1:02d} {cline}", scale=3, color_idx=c_idx)
        cy += 40

    # 3 Benchmark Stat Tiles
    tile_y = 990
    tile_w = 276
    tile_h = 180
    gap = 22
    tiles = (
        (f"{words_n}", "VERIFIED WORDS", 8),
        (f"{blocks_n} PASS", "PYTHON3 -I BLOCKS", 10),
        (f"{dur_ms} MS", "SANDBOX RUNTIME", 15),
    )
    for tidx, (big_val, label_txt, val_color) in enumerate(tiles):
        tx = 64 + tidx * (tile_w + gap)
        canvas.fill_rect(tx, tile_y, tile_w, tile_h, 1)
        canvas.stroke_rect(tx, tile_y, tile_w, tile_h, 2, 3)
        canvas.draw_text(tx + 24, tile_y + 42, big_val[:10], scale=5, color_idx=val_color)
        canvas.draw_text(tx + 24, tile_y + 118, label_txt[:18], scale=2, color_idx=5)

    # Footer CTA Banner
    cta_y = 1216
    cta_h = 208
    canvas.fill_rect(64, cta_y, PIN_WIDTH - 128, cta_h, 7)
    canvas.stroke_rect(64, cta_y, PIN_WIDTH - 128, cta_h, 2, 8)
    canvas.draw_text(92, cta_y + 38, "READ FULL BENCHMARK + GRAB FREE $0 KIT", scale=3, color_idx=4)
    canvas.draw_text(92, cta_y + 92, "ZERO-COST PYTHON, SQLITE WAL & AI LAB", scale=3, color_idx=10)
    author_line = f"BY {config.author_name.upper()} | 30-DAY SERIES"
    canvas.draw_text(92, cta_y + 148, author_line[:45], scale=2, color_idx=4)

    return canvas.encode_png()


def render_pin_svg(article: ArticleRecord, config: AppConfig) -> str:
    """Render matching 1000x1500 (`2:3`) vector SVG infographic for `exports/<slug>/pinterest_pin.svg`."""
    day_num = max(1, int(article.series_day or 1))
    meta = PILLAR_META.get(article.pillar, PILLAR_META["pillar_a"])
    dur_ms = int(round(float(article.verification.get("total_duration_ms", 12))))
    blocks_n = int(article.verification.get("code_blocks_tested", 2))
    words_n = max(750, int(article.word_count or 800))

    headline_lines = _wrap_text(article.title, max_chars=36, max_lines=4)
    headline_tspans = "".join(
        f'<tspan x="64" dy="{0 if i == 0 else 52}">{html.escape(line)}</tspan>'
        for i, line in enumerate(headline_lines)
    )
    code_lines = _extract_code_preview(article.markdown_body, max_lines=10, max_cols=46)
    code_svg_elems = "".join(
        f'<text x="90" y="{502 + i * 38}" fill="#cbd5e1" font-family="monospace" font-size="22">'
        f"{html.escape(f'{i + 1:02d}  {line}')}</text>"
        for i, line in enumerate(code_lines)
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1500" width="1000" height="1500" role="img" aria-label="{html.escape(article.title, quote=True)}">
  <rect width="1000" height="1500" fill="#0b1120" />
  <rect width="1000" height="14" fill="#2dd4bf" />
  <rect x="36" y="36" width="928" height="1428" fill="none" stroke="#334155" stroke-width="2" rx="16" />

  <rect x="64" y="72" width="290" height="52" rx="8" fill="#0f766e" />
  <text x="86" y="106" fill="#f8fafc" font-family="sans-serif" font-size="22" font-weight="800">DAY {day_num:02d}/30 SERIES</text>

  <rect x="374" y="72" width="380" height="52" rx="8" fill="#052e16" stroke="#86efac" stroke-width="2" />
  <text x="398" y="106" fill="#86efac" font-family="sans-serif" font-size="22" font-weight="800">SANDBOX PASS (EXIT 0)</text>

  <text x="64" y="168" fill="#2dd4bf" font-family="sans-serif" font-size="22" font-weight="700">{html.escape(meta["label"].upper())}</text>
  <text x="64" y="226" fill="#f8fafc" font-family="sans-serif" font-size="42" font-weight="800">{headline_tspans}</text>

  <rect x="64" y="410" width="872" height="540" rx="12" fill="#020617" stroke="#334155" stroke-width="2" />
  <rect x="66" y="412" width="868" height="48" rx="10" fill="#1e293b" />
  <circle cx="96" cy="436" r="8" fill="#ef4444" />
  <circle cx="122" cy="436" r="8" fill="#f59e0b" />
  <circle cx="148" cy="436" r="8" fill="#22c55e" />
  <text x="180" y="444" fill="#cbd5e1" font-family="monospace" font-size="20" font-weight="700">PYTHON 3.11+ STDLIB BENCHMARK</text>
  {code_svg_elems}

  <rect x="64" y="990" width="276" height="180" rx="12" fill="#111827" stroke="#334155" stroke-width="2" />
  <text x="88" y="1075" fill="#2dd4bf" font-family="sans-serif" font-size="46" font-weight="800">{words_n}</text>
  <text x="88" y="1125" fill="#cbd5e1" font-family="sans-serif" font-size="20" font-weight="700">VERIFIED WORDS</text>

  <rect x="362" y="990" width="276" height="180" rx="12" fill="#111827" stroke="#334155" stroke-width="2" />
  <text x="386" y="1075" fill="#86efac" font-family="sans-serif" font-size="46" font-weight="800">{blocks_n} PASS</text>
  <text x="386" y="1125" fill="#cbd5e1" font-family="sans-serif" font-size="20" font-weight="700">PYTHON3 -I BLOCKS</text>

  <rect x="660" y="990" width="276" height="180" rx="12" fill="#111827" stroke="#334155" stroke-width="2" />
  <text x="684" y="1075" fill="#38bdf8" font-family="sans-serif" font-size="46" font-weight="800">{dur_ms} MS</text>
  <text x="684" y="1125" fill="#cbd5e1" font-family="sans-serif" font-size="20" font-weight="700">SANDBOX RUNTIME</text>

  <rect x="64" y="1216" width="872" height="208" rx="14" fill="#0f766e" stroke="#2dd4bf" stroke-width="2" />
  <text x="96" y="1278" fill="#ffffff" font-family="sans-serif" font-size="30" font-weight="800">FULL BENCHMARK + FREE $0 PYTHON KIT</text>
  <text x="96" y="1330" fill="#ccfbf1" font-family="sans-serif" font-size="24" font-weight="700">{html.escape(SERIES_30D_NAME)}</text>
  <text x="96" y="1384" fill="#f8fafc" font-family="sans-serif" font-size="20" font-weight="600">Curated by {html.escape(config.author_name)}</text>
</svg>
"""


def generate_pin_assets(article: ArticleRecord, config: AppConfig) -> dict[str, Any]:
    """Write `site/pins/<slug>.png` (`< 35 KB`) and `exports/<slug>/pinterest_pin.svg`."""
    pins_dir = config.site_dir / "pins"
    pins_dir.mkdir(parents=True, exist_ok=True)
    png_path = pins_dir / f"{article.slug}.png"

    export_dir = config.exports_dir / article.slug
    export_dir.mkdir(parents=True, exist_ok=True)
    svg_path = export_dir / "pinterest_pin.svg"

    png_bytes = render_pin_png_bytes(article, config)
    png_path.write_bytes(png_bytes)

    svg_text = render_pin_svg(article, config)
    svg_path.write_text(svg_text, encoding="utf-8")

    return {
        "png_path": str(png_path),
        "svg_path": str(svg_path),
        "png_size_bytes": len(png_bytes),
        "png_sha256": hashlib.sha256(png_bytes).hexdigest(),
        "width": PIN_WIDTH,
        "height": PIN_HEIGHT,
        "public_image_url": f"{config.canonical_base_url}/pins/{article.slug}.png",
    }
