from typing import List, Dict, Tuple, Optional

def valid_set(a: int, b: int, target: int = 21, win_by: int = 2, cap: Optional[int] = 30) -> bool:
    """
    Returns True if the set score (a, b) is valid according to badminton rules.
    - max(a, b) >= target
    - abs(a - b) >= win_by unless cap is reached
    - If cap is reached, next point wins (e.g., 30-29)
    """
    if a < 0 or b < 0:
        return False
    if cap is not None and (a > cap or b > cap):
        return False
    if a == b:
        return False
    if cap is not None and (a == cap or b == cap):
        # Cap reached, next point wins
        return abs(a - b) == 1
    if max(a, b) >= target and abs(a - b) >= win_by:
        return True
    return False

def match_winner(
    set_scores: List[Dict],
    target: int = 21,
    win_by: int = 2,
    cap: int = 30
) -> Tuple[str, int, int, int, int]:
    """
    Determines the match winner and set/point totals.
    Returns (winner, sets_a, sets_b, points_a, points_b)
    winner: 'A', 'B', or '' if not decided
    """
    sets_a = sets_b = points_a = points_b = 0
    for s in set_scores:
        a = s.get('A', 0)
        b = s.get('B', 0)
        points_a += a
        points_b += b
        if valid_set(a, b, target, win_by, cap):
            if a > b:
                sets_a += 1
            else:
                sets_b += 1
    # Best of 3
    if sets_a >= 2:
        return ('A', sets_a, sets_b, points_a, points_b)
    if sets_b >= 2:
        return ('B', sets_a, sets_b, points_a, points_b)
    return ('', sets_a, sets_b, points_a, points_b)
