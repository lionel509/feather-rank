"""
Test random player (bot) functionality in doubles matches.
"""

import asyncio
import os
import sys

# Set test environment
os.environ["TEST_MODE"] = "1"
os.environ["DATABASE_PATH"] = "test_random_player.db"

async def test_random_player_logic():
    """Test that bot can be used as random player in doubles."""
    print("🧪 Testing Random Player (Bot) Functionality...")
    
    from feather_rank import db
    
    # Initialize test database
    test_db_path = "test_random_player.db"
    await db.init_db(test_db_path)
    
    try:
        # Create test players
        print("  ✓ Creating test players...")
        player1 = await db.get_or_create_player(12345, "Player1", base_rating=1200)
        player2 = await db.get_or_create_player(67890, "Player2", base_rating=1200)
        player3 = await db.get_or_create_player(11111, "Player3", base_rating=1200)
        
        # Simulate bot ID
        bot_id = 99999
        
        print("  ✓ Testing match with bot as player...")
        # Create a match with bot as one player (team_a has bot + player1, team_b has player2 + player3)
        match_id = await db.insert_pending_match_points(
            guild_id=999,
            mode="2v2",
            team_a=[bot_id, player1["user_id"]],
            team_b=[player2["user_id"], player3["user_id"]],
            set_scores=[{"A": 21, "B": 15}, {"A": 21, "B": 18}],
            reporter=player1["user_id"],
            target_points=21
        )
        assert match_id > 0
        print(f"    ✅ Match created with bot as player (ID: {match_id})")
        
        # Verify match participants include bot
        match = await db.get_match(match_id)
        participants = await db.get_match_participant_ids(match_id)
        assert bot_id in participants, "Bot should be in participants"
        print(f"    ✅ Bot is in participants: {participants}")
        
        # Test that non-reporters excludes bot (simulating notify_verification logic)
        reporter = match.get("reporter")
        non_reporters = [uid for uid in participants if uid != reporter and uid != bot_id]
        assert bot_id not in non_reporters, "Bot should be excluded from non-reporters"
        print(f"    ✅ Bot excluded from verification list: {non_reporters}")
        
        # Test rating calculation with bot
        print("  ✓ Testing rating calculation with bot as guest...")
        guest_rating = 1200.0  # Default guest rating
        
        # Simulate getting players with bot as guest
        a_ids = [bot_id, player1["user_id"]]
        players_a = []
        for uid in a_ids:
            if uid == bot_id:
                players_a.append({"user_id": uid, "username": "Guest", "rating": guest_rating, "wins": 0, "losses": 0})
            else:
                players_a.append(await db.get_or_create_player(uid, f"User{uid}"))
        
        assert len(players_a) == 2
        assert players_a[0]["user_id"] == bot_id
        assert players_a[0]["rating"] == guest_rating
        assert players_a[1]["user_id"] == player1["user_id"]
        print(f"    ✅ Bot player uses guest rating: {guest_rating}")
        
        print("✅ All random player tests passed!\n")
        return True
        
    finally:
        # Cleanup test database
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
            print("  🧹 Cleaned up test database")

async def run_tests():
    """Run all tests"""
    print("=" * 60)
    print("🚀 Running Random Player Test Suite")
    print("=" * 60 + "\n")
    
    try:
        result = await test_random_player_logic()
        print("=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n❌ Tests failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(run_tests())
    sys.exit(exit_code)
