"""
MMR (Matchmaking Rating) calculations using ELO system.
Pure functions for calculating rating changes in 1v1 and team matches.
"""

import math


def expected(ra: float, rb: float) -> float:
    """
    Calculate the expected score for player A against player B.
    
    Args:
        ra: Rating of player A
        rb: Rating of player B
    
    Returns:
        Expected score (probability) for player A to win (0.0 to 1.0)
    """
    return 1 / (1 + math.pow(10, (rb - ra) / 400))


def elo_delta(ra: float, rb: float, score_a: float, k: int = 32) -> tuple[float, float]:
    """
    Calculate rating changes for both players after a match.
    
    Args:
        ra: Rating of player A
        rb: Rating of player B
        score_a: Actual score for player A (1.0 for win, 0.0 for loss, 0.5 for draw)
        k: K-factor determining maximum rating change per game
    
    Returns:
        Tuple of (new_rating_a, new_rating_b)
    """
    expected_a = expected(ra, rb)
    expected_b = 1 - expected_a
    
    delta_a = k * (score_a - expected_a)
    delta_b = k * ((1 - score_a) - expected_b)
    
    new_ra = ra + delta_a
    new_rb = rb + delta_b
    
    return (new_ra, new_rb)


def team_rating(ratings: list[float]) -> float:
    """
    Calculate the effective team rating from individual player ratings.
    Uses the average rating as the team's effective rating.
    
    Args:
        ratings: List of individual player ratings
    
    Returns:
        Team's effective rating
    """
    if not ratings:
        return 1200.0
    return sum(ratings) / len(ratings)


def apply_team_match(
    rA: list[float], 
    rB: list[float], 
    winner: str, 
    k: int = 32
) -> tuple[list[float], list[float]]:
    """
    Apply ELO rating changes to all players in a team match.
    
    Args:
        rA: List of ratings for team A players
        rB: List of ratings for team B players
        winner: "A" if team A won, "B" if team B won, "draw" for tie
        k: K-factor determining maximum rating change per game
    
    Returns:
        Tuple of (new_ratings_team_a, new_ratings_team_b)
    """
    # Calculate team ratings
    team_a_rating = team_rating(rA)
    team_b_rating = team_rating(rB)
    
    # Determine score
    if winner.upper() == "A":
        score_a = 1.0
    elif winner.upper() == "B":
        score_a = 0.0
    else:  # draw
        score_a = 0.5
    
    # Calculate expected scores
    expected_a = expected(team_a_rating, team_b_rating)
    
    # Calculate rating delta
    delta = k * (score_a - expected_a)
    
    # Apply the same delta to all players on each team
    new_rA = [r + delta for r in rA]
    new_rB = [r - delta for r in rB]
    
    return (new_rA, new_rB)

def expected_points_share(ra: float, rb: float) -> float:
    """Expected share of points for A vs B (Elo formula)."""
    return 1 / (1 + 10 ** (-(ra - rb) / 400))

def elo_points_update(ra: float, rb: float, share_a: float, k: int = 32) -> tuple[float, float]:
    """Update ratings based on points share for A (fraction of total points won)."""
    Ea = expected_points_share(ra, rb)
    delta = k * (share_a - Ea)
    return ra + delta, rb - delta

def team_points_update(ratingsA: list[float], ratingsB: list[float], share_a: float, k: int = 32) -> tuple[list[float], list[float]]:
    """Update team ratings based on points share for team A."""
    Ra = team_rating(ratingsA)
    Rb = team_rating(ratingsB)
    newA, newB = elo_points_update(Ra, Rb, share_a, k)
    dA = newA - Ra
    dB = newB - Rb
    return [r + dA for r in ratingsA], [r + dB for r in ratingsB]
