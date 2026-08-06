"""Small shared helpers for byte/number parsing and formatting."""


class ParseError(ValueError):
    """Raised when user-entered text cannot be turned into bytes."""


def parse_int(text, default=None):
    """Parse a decimal / 0x-hex / 0b-binary integer from user text."""
    text = (text or "").strip()
    if not text:
        if default is None:
            raise ParseError("빈 값입니다")
        return default
    try:
        return int(text, 0) if text.lower().startswith(("0x", "0b", "0o")) else int(text, 10)
    except ValueError:
        raise ParseError(f"숫자로 해석할 수 없습니다: {text!r}")


def parse_bytes(text):
    """Parse a byte list from text.

    Accepts whitespace- or comma-separated tokens, each optionally 0x-prefixed
    ("0x12 0x34", "12,34"), or one unseparated hex blob of even length ("1234").
    """
    text = (text or "").strip()
    if not text:
        return []

    tokens = [t for t in text.replace(",", " ").split() if t]
    if len(tokens) == 1 and len(tokens[0]) > 2 and not tokens[0].lower().startswith("0x"):
        blob = tokens[0]
        if len(blob) % 2:
            raise ParseError(f"16진 문자열의 길이가 홀수입니다: {blob!r}")
        tokens = [blob[i:i + 2] for i in range(0, len(blob), 2)]

    out = []
    for tok in tokens:
        try:
            value = int(tok, 16) if not tok.lower().startswith("0x") else int(tok, 0)
        except ValueError:
            raise ParseError(f"바이트로 해석할 수 없습니다: {tok!r}")
        if not 0 <= value <= 0xFF:
            raise ParseError(f"바이트 범위(0~255)를 벗어났습니다: {tok!r}")
        out.append(value)
    return out


def fmt_bytes(data):
    """Format a byte sequence as space-separated uppercase hex."""
    return " ".join(f"{b:02X}" for b in data) if data else "(없음)"


def fmt_addr(addr):
    return f"0x{addr:02X}"
