import time
from typing import Optional, Iterable

try:
	import discord  # type: ignore
except Exception:  # Keep fmt import-light if discord isn't available in some contexts
	discord = None  # pragma: no cover


def bold(t: str) -> str:
	return f"**{t}**"


def code(t: str) -> str:
	return f"`{t}`"


def block(t: str, lang: str | None = None) -> str:
	return f"```{lang or ''}\n{t}\n```"


def mention(uid: int) -> str:
	return f"<@{uid}>"


def score_sets(sets: list[dict]) -> str:
	return " | ".join(f"{s.get('A', 0)}–{s.get('B', 0)}" for s in sets if s)


# --- Display name cache helper ---
_NAME_CACHE: dict[tuple[Optional[int], int], tuple[float, str]] = {}
_CACHE_TTL_SEC = 300.0  # 5 minutes
_MAX_CACHE_SIZE = 1000  # Prevent unbounded growth


def _clean_expired_cache():
	"""Remove expired entries from the name cache to prevent memory bloat."""
	now = time.time()
	expired_keys = [k for k, v in _NAME_CACHE.items() if now - v[0] >= _CACHE_TTL_SEC]
	for k in expired_keys:
		del _NAME_CACHE[k]
	
	# If still too large, remove oldest entries
	if len(_NAME_CACHE) > _MAX_CACHE_SIZE:
		sorted_entries = sorted(_NAME_CACHE.items(), key=lambda x: x[1][0])
		to_remove = len(_NAME_CACHE) - _MAX_CACHE_SIZE
		for k, _ in sorted_entries[:to_remove]:
			del _NAME_CACHE[k]


async def display_name_or_cached(
	bot,
	guild: Optional["discord.Guild"],
	user_id: int,
	fallback: Optional[str] = None,
) -> str:
	"""Return a user's display name, preferring guild nicknames, with a small TTL cache.

	Inputs:
	- bot: discord.Client or Bot (used to fetch users/members)
	- guild: current Guild or None
	- user_id: Discord user ID
	- fallback: text to use if lookup fails (defaults to "User<id>")

	Behavior:
	- Checks in-memory cache keyed by (guild_id, user_id) with TTL
	- Tries guild member (cache), then fetch_member, then global fetch_user
	- Returns fallback if everything fails
	"""
	if not user_id:
		return fallback or "Unknown"

	g_id = getattr(guild, "id", None) if guild is not None else None
	key = (g_id, user_id)
	now = time.time()
	
	# Periodically clean expired entries (every ~100 lookups)
	if len(_NAME_CACHE) % 100 == 0:
		_clean_expired_cache()
	
	cached = _NAME_CACHE.get(key)
	if cached and (now - cached[0] < _CACHE_TTL_SEC):
		return cached[1]

	name: Optional[str] = None

	# Prefer guild nickname/display_name
	try:
		member = None
		if guild is not None and hasattr(guild, "get_member"):
			member = guild.get_member(user_id)
		if member is not None:
			name = getattr(member, "display_name", None) or getattr(member, "name", None)
		elif guild is not None and hasattr(guild, "fetch_member"):
			try:
				member = await guild.fetch_member(user_id)  # type: ignore[attr-defined]
				name = getattr(member, "display_name", None) or getattr(member, "name", None)
			except Exception:
				name = None
	except Exception:
		name = None

	# Fallback to global user
	if name is None and hasattr(bot, "fetch_user"):
		try:
			user = await bot.fetch_user(user_id)
			name = getattr(user, "display_name", None) or getattr(user, "name", None)
		except Exception:
			name = None

	if name is None:
		name = fallback or f"User{user_id}"

	_NAME_CACHE[key] = (now, name)
	return name


def mono_table(rows: list[list[str]], headers: Optional[list[str]] = None) -> str:
	"""Render a simple monospaced table as a Markdown code block.

	- Pads columns to the widest cell
	- Includes a header divider if headers are provided
	"""
	# Normalize all to strings and compute column count
	norm_rows = [[str(c) for c in r] for r in rows]
	col_count = max((len(r) for r in norm_rows), default=0)
	if headers:
		headers = [str(h) for h in headers]
		col_count = max(col_count, len(headers))

	def pad_row(r: Iterable[str]) -> list[str]:
		lst = list(r)
		if len(lst) < col_count:
			lst += [""] * (col_count - len(lst))
		return lst

	if headers:
		headers = pad_row(headers)
	norm_rows = [pad_row(r) for r in norm_rows]

	widths = [0] * col_count
	if headers:
		for i, cell in enumerate(headers):
			widths[i] = max(widths[i], len(cell))
	for r in norm_rows:
		for i, cell in enumerate(r):
			widths[i] = max(widths[i], len(cell))

	def fmt_row(r: list[str]) -> str:
		return " | ".join((r[i].ljust(widths[i]) for i in range(col_count)))

	lines: list[str] = []
	if headers:
		lines.append(fmt_row(headers))
		# Divider like ---+--- style
		divider = "-+-".join("-" * w for w in widths)
		lines.append(divider)
	for r in norm_rows:
		lines.append(fmt_row(r))

	return block("\n".join(lines), "md")
