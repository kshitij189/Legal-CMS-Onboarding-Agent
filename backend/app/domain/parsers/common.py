import re

_SEPARATORS = re.compile(r"[_.\-]+")


def humanize(key: str) -> str:
    """Fallback label when a source gives none: 'client_mobile' -> 'Client mobile'."""
    text = _SEPARATORS.sub(" ", key).strip()
    return text[:1].upper() + text[1:] if text else key
