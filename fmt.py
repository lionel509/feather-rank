from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence, Tuple


def bold(t: str) -> str:
    """Wrap text in Markdown bold markers.

    Args:
        t: Text to format.

    Returns:
        The text wrapped in ** **.
    """

    return f"**{t}**"


def code(t: str) -> str:
    """Wrap text in inline code markers.

    Args:
        t: Text to format.

    Returns:
        The text wrapped in backticks.
    """

    return f"`{t}`"


def block(t: str, lang: str | None = None) -> str:
    """Wrap text in a fenced code block.

    Args:
        t: Text content inside the block.
        lang: Optional language identifier (e.g., "md", "python").

    Returns:
        A fenced code block string using triple backticks.
    """

    fence = f"```{lang}" if lang else "```"
    return f"{fence}\n{t}\n```"


def _extract_pair(d: dict) -> Tuple[Any, Any]:
    """Extract a score pair (left, right) from a set dict.

    Tries common key pairs first; falls back to the first two values
    in insertion order if specific keys aren't found.
    """

    preferred_pairs = [
        ("a", "b"),
        ("p1", "p2"),
        ("p1_score", "p2_score"),
        ("left", "right"),
        ("home", "away"),
        ("score1", "score2"),
    ]

    for k1, k2 in preferred_pairs:
        if k1 in d and k2 in d:
            return d[k1], d[k2]

    vals = list(d.values())
    if len(vals) >= 2:
        return vals[0], vals[1]

    raise ValueError("Score set dictionary must contain at least two values")


def score_sets(sets: Sequence[dict]) -> str:
    """Format a sequence of set score dicts into a compact string.

    Example output: "21–18 | 19–21 | 22–20"

    Args:
        sets: Iterable of dictionaries each containing at least two values
              representing the left and right scores.

    Returns:
        A single string joining each set with " | " using an en dash between scores.
    """

    parts: List[str] = []
    for s in sets:
        l, r = _extract_pair(s)
        parts.append(f"{l}{r}".replace("\u0013\u0013", "–"))  # ensure en dash
    return " | ".join(parts)


def mono_table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    """Create a fixed-width monospace table wrapped in a Markdown code block.

    - Columns are left-padded to the widest cell (header or row) per column.
    - Columns are joined with " | ".
    - The entire table is wrapped with block(..., lang="md").

    Args:
        rows: Row values; items will be stringified.
        headers: Column headers.

    Returns:
        A Markdown code block string containing the table.
    """

    # Normalize and compute widths with truncation for overly long cell strings
    col_count = len(headers)
    norm_rows: List[List[str]] = []
    for row in rows:
        r_raw = [str(c) for c in list(row)[:col_count]]
        # Truncate long values (e.g., very long usernames) to 20 chars with ellipsis
        r = [(_truncate(c, 20)) for c in r_raw]
        if len(r) < col_count:
            r.extend([""] * (col_count - len(r)))
        norm_rows.append(r)

    headers_str = [str(h) for h in headers]

    widths = [
        max(len(headers_str[i]), max((len(r[i]) for r in norm_rows), default=0))
        for i in range(col_count)
    ]

    def fmt_line(cols: Sequence[str]) -> str:
        return " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))

    lines: List[str] = [fmt_line(headers_str)]
    # Build lines with a 2000 char safety cap (Discord limit)
    # Reserve ~50 chars for code fences and potential footer
    MAX_MSG = 2000
    RESERVED = 80
    current_len = len(lines[0]) + 1  # header + newline
    truncated = False
    for r in norm_rows:
        line = fmt_line(r)
        # Consider fenced wrapping overhead and newlines
        projected = current_len + len(line) + 1 + len("```md\n") + len("\n```")
        if projected > MAX_MSG - RESERVED:
            truncated = True
            break
        lines.append(line)
        current_len += len(line) + 1

    if truncated:
        lines.append("… (truncated)")

    table = "\n".join(lines)
    return block(table, lang="md")


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    if max_len <= 1:
        return s[:max_len]
    return s[: max_len - 1] + "…"


__all__ = [
    "bold",
    "code",
    "block",
    "score_sets",
    "mono_table",
]


def mention(user_id: int) -> str:
    """Return a Discord mention string for a user ID."""
    return f"<@{user_id}>"


async def display_name_or_cached(bot, guild, user_id: int, fallback: str | None = None) -> str:
    """Return display name for a user, using guild cache or API fallback.

    - Tries guild.get_member first if a guild is provided.
    - Falls back to bot.fetch_user; returns display_name or name.
    - On error, returns fallback or a synthetic name.
    """
    m = guild.get_member(user_id) if guild else None
    if m:
        return m.display_name
    try:
        u = await bot.fetch_user(user_id)
        return getattr(u, "display_name", None) or getattr(u, "name", f"User{user_id}")
    except Exception:
        return fallback or f"User{user_id}"
