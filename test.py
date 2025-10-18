"""
Test suite for feather-rank Discord bot
Tests database functions, MMR calculations, and core functionality
"""

import asyncio
import os
import sys
from pathlib import Path

# Test database and MMR modules
async def test_database():
    """Test database initialization and operations"""
    print("🧪 Testing Database Operations...")
    
    # Import after setting test DB path
    import db
    
    # Use test database
    test_db_path = "test_feather_rank.db"
    await db.init_db(test_db_path)
    
    try:
        # Test 1: Create players
        print("  ✓ Testing player creation...")
        player1 = await db.get_or_create_player(12345, "TestPlayer1", base_rating=1200)
        assert player1['user_id'] == 12345
        assert player1['username'] == "TestPlayer1"
        assert player1['rating'] == 1200
        assert player1['wins'] == 0
        assert player1['losses'] == 0
        print("    ✅ Player creation works")
        
        # Test 2: Get existing player
        print("  ✓ Testing get existing player...")
        player1_again = await db.get_or_create_player(12345, "TestPlayer1")
        assert player1_again['user_id'] == 12345
        assert player1_again['rating'] == 1200  # Should not reset
        print("    ✅ Get existing player works")
        
        # Test 3: Create more players
        player2 = await db.get_or_create_player(67890, "TestPlayer2", base_rating=1200)
        player3 = await db.get_or_create_player(11111, "TestPlayer3", base_rating=1200)
        player4 = await db.get_or_create_player(22222, "TestPlayer4", base_rating=1200)
        
        # Test 4: Update player
        print("  ✓ Testing player update...")
        await db.update_player(12345, 1250.0, won=True)
        updated_player = await db.get_or_create_player(12345, "TestPlayer1")
        assert updated_player['rating'] == 1250.0
        assert updated_player['wins'] == 1
        assert updated_player['losses'] == 0
        print("    ✅ Player update works")
        
        # Test 5: Insert match
        print("  ✓ Testing match insertion...")
        match_id = await db.insert_match(
            guild_id=999,
            mode="2v2",
            team_a=[12345, 67890],
            team_b=[11111, 22222],
            set_winners=["A", "A"],
            winner="A",
            created_by=12345
        )
        assert match_id > 0
        print(f"    ✅ Match inserted with ID: {match_id}")
        
        # Test 6: Top players
        print("  ✓ Testing top players query...")
        await db.update_player(67890, 1300.0, won=True)
        await db.update_player(11111, 1150.0, won=False)
        top = await db.top_players(guild_id=999, limit=10)
        assert len(top) == 4
        assert top[0]['rating'] == 1300.0  # Highest rated
        print(f"    ✅ Top players query works (found {len(top)} players)")
        
        # Test 7: Recent matches
        print("  ✓ Testing recent matches query...")
        matches = await db.recent_matches(guild_id=999, user_id=12345, limit=5)
        assert len(matches) == 1
        assert matches[0]['id'] == match_id
        print(f"    ✅ Recent matches query works (found {len(matches)} matches)")
        
        print("✅ All database tests passed!\n")
        return True
        
    finally:
        # Cleanup test database
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
            print("  🧹 Cleaned up test database")


def test_mmr():
    """Test MMR/ELO calculations"""
    print("🧪 Testing MMR Calculations...")
    
    from mmr import expected, elo_delta, team_rating, apply_team_match
    
    # Test 1: Expected score
    print("  ✓ Testing expected score calculation...")
    exp = expected(1200, 1200)
    assert 0.49 < exp < 0.51  # Should be ~0.5 for equal ratings
    
    exp_higher = expected(1400, 1200)
    assert exp_higher > 0.7  # Higher rated player expected to win
    print("    ✅ Expected score calculation works")
    
    # Test 2: ELO delta
    print("  ✓ Testing ELO delta calculation...")
    new_a, new_b = elo_delta(1200, 1200, 1.0, k=32)  # Player A wins
    assert new_a > 1200  # Winner gains rating
    assert new_b < 1200  # Loser loses rating
    assert abs((new_a - 1200) + (new_b - 1200)) < 0.01  # Total rating conserved
    print(f"    ✅ ELO delta works (1200→{new_a:.1f}, 1200→{new_b:.1f})")
    
    # Test 3: Team rating
    print("  ✓ Testing team rating calculation...")
    team_r = team_rating([1200, 1400])
    assert team_r == 1300  # Average
    print(f"    ✅ Team rating calculation works (avg: {team_r})")
    
    # Test 4: Apply team match
    print("  ✓ Testing team match application...")
    team_a_ratings = [1200.0, 1200.0]
    team_b_ratings = [1200.0, 1200.0]
    new_a, new_b = apply_team_match(team_a_ratings, team_b_ratings, "A", k=32)
    
    assert all(r > 1200 for r in new_a)  # All winners gain
    assert all(r < 1200 for r in new_b)  # All losers lose
    assert new_a[0] == new_a[1]  # Same change for teammates
    assert new_b[0] == new_b[1]  # Same change for teammates
    print(f"    ✅ Team match works (Team A: {new_a[0]:.1f}, Team B: {new_b[0]:.1f})")
    
    print("✅ All MMR tests passed!\n")
    return True


def test_models():
    """Test data models"""
    print("🧪 Testing Data Models...")
    
    from models import Player, Match
    
    # Test Player dataclass
    print("  ✓ Testing Player model...")
    player = Player(
        user_id=12345,
        username="TestUser",
        rating=1200.0,
        wins=5,
        losses=3
    )
    assert player.user_id == 12345
    assert player.rating == 1200.0
    print("    ✅ Player model works")
    
    # Test Match dataclass
    print("  ✓ Testing Match model...")
    match = Match(
        id=1,
        guild_id=999,
        mode="2v2",
        team_a=[12345, 67890],
        team_b=[11111, 22222],
        set_winners=["A", "B", "A"],
        winner="A",
        created_by=12345
    )
    assert match.mode == "2v2"
    assert len(match.team_a) == 2
    assert match.winner == "A"
    print("    ✅ Match model works")
    
    print("✅ All model tests passed!\n")
    return True


def test_config():
    """Test configuration loading"""
    print("🧪 Testing Configuration...")
    
    # Check if .env.example exists
    print("  ✓ Checking configuration files...")
    if os.path.exists(".env.example"):
        print("    ✅ .env.example exists")
    
    # Test default values
    test_k = int(os.getenv("K_FACTOR", "32"))
    assert test_k == 32
    print(f"    ✅ K_FACTOR default: {test_k}")
    
    test_db = os.getenv("DATABASE_PATH", "./smashcord.sqlite")
    assert test_db == "./smashcord.sqlite"
    print(f"    ✅ DATABASE_PATH default: {test_db}")
    
    print("✅ Configuration tests passed!\n")
    return True


async def test_tos():
    print("🧪 Testing ToS acceptance...")
    import db
    test_db_path = "test_feather_rank.db"
    await db.init_db(test_db_path)
    user_id = 55555
    # Should not have accepted yet
    accepted = await db.has_accepted_tos(user_id)
    assert not accepted, "User should not have accepted ToS yet"
    print("  ✓ ToS not accepted by default")
    # Accept ToS
    await db.set_tos_accepted(user_id, version="testv1")
    accepted = await db.has_accepted_tos(user_id)
    assert accepted, "User should have accepted ToS after set_tos_accepted"
    print("  ✓ ToS accepted and stored")
    # Accept again (should not error)
    await db.set_tos_accepted(user_id, version="testv2")
    accepted = await db.has_accepted_tos(user_id)
    assert accepted, "User should still have accepted ToS after re-accepting"
    print("  ✓ ToS re-acceptance does not break")
    print("✅ ToS tests passed!\n")
    # Cleanup
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        print("  🧹 Cleaned up test database")


async def run_all_tests():
    """Run all tests"""
    print("=" * 60)
    print("🚀 Running Feather-Rank Test Suite")
    print("=" * 60 + "\n")
    
    results = []
    
    # Test 1: Models
    try:
        results.append(("Models", test_models()))
    except Exception as e:
        print(f"❌ Models test failed: {e}\n")
        results.append(("Models", False))
    
    # Test 2: MMR
    try:
        results.append(("MMR", test_mmr()))
    except Exception as e:
        print(f"❌ MMR test failed: {e}\n")
        results.append(("MMR", False))
    
    # Test 3: Database
    try:
        results.append(("Database", await test_database()))
    except Exception as e:
        print(f"❌ Database test failed: {e}\n")
        results.append(("Database", False))
    
    # Test 4: Config
    try:
        results.append(("Config", test_config()))
    except Exception as e:
        print(f"❌ Config test failed: {e}\n")
        results.append(("Config", False))
    
    # Test 5: ToS
    try:
        await test_tos()
        results.append(("ToS", True))
    except Exception as e:
        print(f"❌ ToS test failed: {e}\n")
        results.append(("ToS", False))
    
    # Summary
    print("=" * 60)
    print("📊 Test Summary")
    print("=" * 60)
    
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {test_name:20} {status}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    
    print("\n" + "=" * 60)
    print(f"Total: {passed}/{total} tests passed")
    print("=" * 60)
    
    if passed == total:
        print("\n🎉 All tests passed! Everything works correctly!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please review the errors above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_all_tests())
    sys.exit(exit_code)
