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
