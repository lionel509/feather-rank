from typing import List, Dict, Tuple, Optional

def valid_set(a: int, b: int, target: int, win_by: int = 2, cap: Optional[int] = None) -> bool:
    """
    Returns True if the set score (a, b) is valid according to badminton rules.
    - max(a, b) >= target
    - abs(a - b) >= win_by unless cap is reached
    - If cap is reached, next point wins (e.g., 30-29 or 15-14)
    """
    if a < 0 or b < 0:
        return False
    m = max(a, b)
    d = abs(a - b)
    if cap is not None and m > cap:
        return False
    # if nobody reached target yet, set not finished
    if m < target:
        return False
    # if cap hit, next point wins (e.g., 30-29 or 15-14)
    if cap is not None and m == cap:
        return True
    return d >= win_by

def match_winner(
    set_scores: List[Dict],
    target: int,
    win_by: int = 2,
    cap: Optional[int] = None
) -> Tuple[str, int, int, int, int]:
    """
    Determines the match winner and set/point totals.
    Returns (winner, sets_a, sets_b, points_a, points_b)
    Raises ValueError if any set is invalid.
    """
    sets_a = sets_b = pts_a = pts_b = 0
    for s in set_scores:
        a, b = int(s["A"]), int(s["B"])
        if not valid_set(a, b, target, win_by, cap):
            raise ValueError("Invalid set")
        pts_a += a
        pts_b += b
        if a > b:
            sets_a += 1
        else:
            sets_b += 1
        if sets_a == 2 or sets_b == 2:
            break
    winner = "A" if sets_a > sets_b else "B"
    return winner, sets_a, sets_b, pts_a, pts_b


def set_finished(a: int, b: int, target: int, win_by: int = 2, cap: int | None = None) -> tuple[bool, str | None]:
    """
    Check if a set is finished and return the winner.
    
    Args:
        a: Team A points
        b: Team B points
        target: Target points to reach (e.g., 21 or 11)
        win_by: Minimum point difference to win (default: 2)
        cap: Cap points (e.g., 30 or 15), auto-calculated if None
    
    Returns:
        Tuple of (is_finished, winner)
        - is_finished: True if the set is complete
        - winner: 'A' or 'B' if finished, None if not finished
    
    Examples:
        set_finished(21, 19, 21) -> (True, 'A')   # Won by 2
        set_finished(21, 20, 21) -> (False, None)  # Need win by 2
        set_finished(30, 29, 21) -> (True, 'A')   # Hit cap
        set_finished(15, 10, 21) -> (False, None)  # Neither reached target
    """
    cap = cap or (30 if target >= 21 else 15)
    m = max(a, b)
    d = abs(a - b)
    
    # If neither team reached target, set is not finished
    if m < target:
        return (False, None)
    
    # If cap is reached, higher score wins immediately
    if cap and m >= cap:
        return (True, 'A' if a > b else 'B')
    
    # Otherwise, need to win by required margin
    return (d >= win_by, 'A' if a > b else 'B' if d >= win_by else None)
