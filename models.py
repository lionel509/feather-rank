
@dataclass
class Signature:
    match_id: int
    user_id: int
    decision: str
    signed_name: str | None
    signed_at: str
"""
Data models for the badminton ranking system.
"""

from dataclasses import dataclass


@dataclass
class Player:
    user_id: int
    username: str
    rating: float
    wins: int
    losses: int


@dataclass
class Match:
    id: int | None
    guild_id: int
    mode: str
    team_a: list[int]
    team_b: list[int]
    set_winners: list[str]
    winner: str
    created_by: int
