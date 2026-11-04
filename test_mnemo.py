from mnemo.core import Mnemo
import time
import os

def cleanup():
    """Remove test databases before running."""
    for f in ["test_mnemo.db"]:
        if os.path.exists(f):
            os.remove(f)

def main():
    cleanup()
    print("Initializing Mnemo...")
    mnemo = Mnemo(db_path="test_mnemo.db", chroma_path="./test_chroma")
    
    print("\n1. Adding episodes (Working Memory)...")
    # Simulating a conversation about different topics
    mnemo.add("Alice is a software engineer based in London.")
    mnemo.add("Alice loves drinking black coffee while coding.")
    mnemo.add("Bob is working on a secret machine learning project.")
    mnemo.add("The project uses PyTorch and runs on cloud servers.")
    mnemo.add("Bob and Alice met at a tech conference in Berlin.")
    
    print("Waiting 1 second for embeddings to index...")
    time.sleep(1)
    
    print("\n2. Running Consolidation (Cognitive Sieve)...")
    processed = mnemo.consolidate()
    print(f"Processed episodes: {processed}")
    
    print("\n3. Testing Recall (Hybrid Search)...")
    
    # Test 1: Direct match
    print("\n--- Query: 'Where is Alice?' ---")
    res1 = mnemo.recall("Where is Alice?")
    print("Episodes:")
    for ep in res1["episodes"]:
        print(f"  - {ep['text']}")
    print("Graph facts:")
    for fact in res1["facts"][:5]:
        print(f"  - {fact['source']} -> {fact['relation']} -> {fact['target']} (Weight: {fact['weight']})")

    # Test 2: Indirect match (finding connections)
    print("\n--- Query: 'What technologies are used in Bob's project?' ---")
    res2 = mnemo.recall("What technologies are used in Bob's project?")
    print("Episodes:")
    for ep in res2["episodes"]:
        print(f"  - {ep['text']}")
    print("Graph facts:")
    for fact in res2["facts"][:5]:
        print(f"  - {fact['source']} -> {fact['relation']} -> {fact['target']} (Weight: {fact['weight']})")

    # Test 3: Relationship discovery
    print("\n--- Query: 'How do Alice and Bob know each other?' ---")
    res3 = mnemo.recall("How do Alice and Bob know each other?")
    print("Episodes:")
    for ep in res3["episodes"]:
        print(f"  - {ep['text']}")
    print("Graph facts:")
    for fact in res3["facts"][:5]:
        print(f"  - {fact['source']} -> {fact['relation']} -> {fact['target']} (Weight: {fact['weight']})")

def test_decay():
    """Test that old memories fade away."""
    print("\n=== Testing Decay (Forgetting) ===")
    
    # Use a fresh database
    import os
    for f in ["test_decay.db"]:
        if os.path.exists(f):
            os.remove(f)
    
    from mnemo.core import Mnemo
    import time
    
    mnemo = Mnemo(db_path="test_decay.db", chroma_path="./test_chroma_decay")
    
    print("Adding episodes...")
    mnemo.add("The user loves chocolate ice cream.")
    mnemo.add("The user hates rainy weather.")
    mnemo.add("The user is learning to play the guitar.")
    
    time.sleep(1)
    mnemo.consolidate()
    
    # Check current weight
    print("\nBefore decay:")
    facts = mnemo.recall("What does the user love?")["facts"]
    for f in facts[:5]:
        print(f"  {f['source']} -> {f['relation']} -> {f['target']} (weight={f['weight']})")
    
    # Manually set last_reinforced to 30 days ago to simulate aging
    import sqlite3
    from datetime import datetime, timedelta
    old_date = (datetime.now() - timedelta(days=30)).isoformat()
    
    conn = sqlite3.connect("test_decay.db")
    conn.execute("UPDATE edges SET last_reinforced = ?", (old_date,))
    conn.commit()
    conn.close()
    
    # Run decay
    print("\nRunning decay (simulating 30 days)...")
    affected = mnemo.decay(decay_rate=0.1, threshold=0.05)
    print(f"Affected edges: {affected}")
    
    # Check after decay
    print("\nAfter decay:")
    facts = mnemo.recall("What does the user love?")["facts"]
    if not facts:
        print("  (all facts faded away — perfect for testing!)")
    else:
        for f in facts[:5]:
            print(f"  {f['source']} -> {f['relation']} -> {f['target']} (weight={f['weight']:.2f})")

if __name__ == "__main__":
    main()
    test_decay()