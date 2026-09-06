"""Pure-logic helpers from ``internal.ts``.

Ports the two dependency-light, stdlib-only helpers: Python-compatible text
decoding with ``errors`` handling, and glob-pattern → regex translation. The
original ``BufferedReadable`` wrapper bound to node ``stream.Readable`` is not
portable and is skipped (see ``SKIPPED.md``).
"""

from __future__ import annotations

import re

# Map Node ``BufferEncoding`` names to Python codec labels where the two diverge.
# Only the UTF family participates in strict/replace/ignore; the others are
# lossless byte→char mappings where ``errors`` is meaningless.
_UTF_WEB_LABELS: dict[str, str] = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "utf16le": "utf-16-le",
    "ucs2": "utf-16-le",
    "ucs-2": "utf-16-le",
}


def decode_text_with_errors(
    data: bytes,
    encoding: str = "utf-8",
    errors: "strict | replace | ignore" = "strict",
    ignore_bom: bool = False,
) -> str:
    """Decode ``data`` into a string with Python-compatible ``errors`` handling.

    - ``'strict'`` (default): raise on invalid sequences
    - ``'replace'``: substitute each invalid sequence with U+FFFD
    - ``'ignore'``: drop invalid input sequences

    Non-UTF encodings (hex / base64 / latin1 / binary / ascii) are lossless
    byte↔char mappings, so ``errors`` is ignored for them — matching Node's
    ``Buffer.toString`` behavior. ``ignore_bom`` only affects UTF-8: when true
    any leading BOM is dropped, otherwise (``utf-8-sig``) it is preserved.
    """
    web_label = _UTF_WEB_LABELS.get(encoding)
    if web_label is None:
        return data.decode(encoding)

    if web_label == "utf-8" and not ignore_bom:
        # Preserve a leading BOM (mirrors TextDecoder's default).
        return data.decode("utf-8-sig", errors)

    return data.decode(web_label, errors)


def glob_pattern_to_regex(pattern: str, case_sensitive: bool = True) -> re.Pattern[str]:
    """Convert a glob pattern segment into a compiled regex.

    Mirrors Python ``pathlib`` behavior: includes dotfiles, case-sensitive by
    default. ``*`` matches any run of non-slash characters, ``?`` a single
    non-slash character, ``[...]`` a character class (``!`` negates), and a
    backslash escapes the next character.
    """
    regex = "^"
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            regex += "[^/]*"
        elif ch == "?":
            regex += "[^/]"
        elif ch == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                regex += r"\["
            else:
                char_class = pattern[i + 1 : end].replace("\\", "\\\\")
                if char_class.startswith("!"):
                    char_class = "^" + char_class[1:]
                elif char_class.startswith("^"):
                    char_class = "\\" + char_class
                regex += "[" + char_class + "]"
                i = end
        elif ch == "\\":
            if i + 1 < n:
                regex += re.escape(pattern[i + 1])
                i += 1
            else:
                regex += r"\\"
        else:
            regex += re.escape(ch)
        i += 1
    regex += "$"
    return re.compile(regex, 0 if case_sensitive else re.IGNORECASE)


__all__ = ["decode_text_with_errors", "glob_pattern_to_regex"]
