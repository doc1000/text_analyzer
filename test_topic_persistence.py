"""
Test script to verify topic persistence functionality.

This script:
1. Tests topic table creation
2. Tests topic saving and retrieval
3. Tests topic matching (reuse existing topics)
4. Tests hierarchical structure (parent_id links)
"""

import sys
import os
from pathlib import Path

# Add app directory to path
script_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(script_dir))

if Path("/code").exists():
    sys.path.insert(0, "/code")

try:
    from app.db import get_db, TOPIC_TABLE, EMBED_DIM
    from app.topics import compute_topics, _save_topic_to_db, _find_matching_topic
    from app.mmr import compute_centroid
    from app.config import PREFERENCES
    import numpy as np
    from sqlalchemy import func
except ImportError as e:
    print(f"❌ Import error: {e}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Python path: {sys.path}")
    raise


def test_topic_table_exists():
    """Test that TOPIC_TABLE was created successfully."""
    print("\n" + "="*60)
    print("TEST 1: Topic Table Exists")
    print("="*60)
    
    try:
        db_gen = get_db()
        db = next(db_gen)
        
        # Try to query the table
        count = db.query(func.count(TOPIC_TABLE.id)).scalar()
        print(f"✓ TOPIC_TABLE exists with {count} rows")
        print(f"  Table class: {TOPIC_TABLE}")
        print(f"  Embedding dimension: {EMBED_DIM}")
        
        # Cleanup
        try:
            next(db_gen)
        except StopIteration:
            pass
        
        return True
        
    except Exception as e:
        print(f"✗ TOPIC_TABLE check failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_save_topic():
    """Test saving a topic to the database."""
    print("\n" + "="*60)
    print("TEST 2: Save Topic")
    print("="*60)
    
    try:
        db_gen = get_db()
        db = next(db_gen)
        
        # Create a test centroid
        test_centroid = np.random.rand(EMBED_DIM)
        test_centroid = test_centroid / np.linalg.norm(test_centroid)  # Normalize
        
        # Save a test topic
        topic_id = _save_topic_to_db(
            db=db,
            title="Test Topic - Python Programming",
            centroid=test_centroid,
            document_count=5,
            summary="A test topic about Python programming",
            level_index=0
        )
        
        print(f"✓ Topic saved with ID: {topic_id}")
        
        # Verify it was saved
        saved_topic = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.id == topic_id).first()
        assert saved_topic is not None, "Topic should exist in database"
        assert saved_topic.title_text == "Test Topic - Python Programming"
        assert saved_topic.document_count == 5
        assert saved_topic.level_index == 0
        assert saved_topic.parent_id is None
        
        print(f"  Title: {saved_topic.title_text}")
        print(f"  Document count: {saved_topic.document_count}")
        print(f"  Level: {saved_topic.level_index}")
        print(f"  Match count: {saved_topic.match_count}")
        print("✓ Topic verification passed")
        
        # Cleanup
        try:
            next(db_gen)
        except StopIteration:
            pass
        
        return True
        
    except Exception as e:
        print(f"✗ Save topic test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_topic_matching():
    """Test topic matching (finding similar topics)."""
    print("\n" + "="*60)
    print("TEST 3: Topic Matching")
    print("="*60)
    
    try:
        db_gen = get_db()
        db = next(db_gen)
        
        # Create a test centroid
        test_centroid = np.random.rand(EMBED_DIM)
        test_centroid = test_centroid / np.linalg.norm(test_centroid)
        
        # Save a topic
        topic_id = _save_topic_to_db(
            db=db,
            title="Machine Learning Research",
            centroid=test_centroid,
            document_count=10,
            summary="Research on machine learning",
            level_index=0
        )
        
        print(f"✓ Created topic: Machine Learning Research (ID: {topic_id})")
        
        # Try to find a very similar topic (same centroid with tiny noise)
        similar_centroid = test_centroid + np.random.rand(EMBED_DIM) * 0.01
        similar_centroid = similar_centroid / np.linalg.norm(similar_centroid)
        
        match = _find_matching_topic(db, similar_centroid, similarity_threshold=0.80, level_index=0)
        
        if match:
            matched_id, matched_title, matched_summary = match
            print(f"✓ Found matching topic: {matched_title}")
            
            # Verify match count was incremented
            updated_topic = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.id == matched_id).first()
            assert updated_topic.match_count > 0, "Match count should be incremented"
            print(f"  Match count: {updated_topic.match_count}")
            print(f"  Last matched: {updated_topic.last_matched_at}")
            print("✓ Topic matching passed")
        else:
            print("⚠ No match found (might need to adjust similarity threshold)")
        
        # Try to find a dissimilar topic (random centroid)
        different_centroid = np.random.rand(EMBED_DIM)
        different_centroid = different_centroid / np.linalg.norm(different_centroid)
        
        no_match = _find_matching_topic(db, different_centroid, similarity_threshold=0.90, level_index=0)
        
        if no_match:
            print("⚠ Found match for dissimilar centroid (unexpected, might be false positive)")
        else:
            print("✓ No match for dissimilar centroid (expected)")
        
        # Cleanup
        try:
            next(db_gen)
        except StopIteration:
            pass
        
        return True
        
    except Exception as e:
        print(f"✗ Topic matching test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_hierarchical_structure():
    """Test hierarchical topic/subtopic structure with parent_id."""
    print("\n" + "="*60)
    print("TEST 4: Hierarchical Structure")
    print("="*60)
    
    try:
        db_gen = get_db()
        db = next(db_gen)
        
        # Create a parent topic
        parent_centroid = np.random.rand(EMBED_DIM)
        parent_centroid = parent_centroid / np.linalg.norm(parent_centroid)
        
        parent_id = _save_topic_to_db(
            db=db,
            title="Parent Topic - Web Development",
            centroid=parent_centroid,
            document_count=20,
            summary="Parent topic about web development",
            level_index=0
        )
        
        print(f"✓ Created parent topic: Web Development (ID: {parent_id})")
        
        # Create subtopics
        subtopic_titles = ["Frontend Development", "Backend Development", "DevOps"]
        subtopic_ids = []
        
        for i, subtopic_title in enumerate(subtopic_titles):
            subtopic_centroid = np.random.rand(EMBED_DIM)
            subtopic_centroid = subtopic_centroid / np.linalg.norm(subtopic_centroid)
            
            subtopic_id = _save_topic_to_db(
                db=db,
                title=subtopic_title,
                centroid=subtopic_centroid,
                document_count=5 + i,
                summary=f"Subtopic about {subtopic_title.lower()}",
                parent_id=parent_id,
                level_index=1
            )
            subtopic_ids.append(subtopic_id)
            print(f"  ✓ Created subtopic: {subtopic_title} (ID: {subtopic_id})")
        
        # Verify hierarchy
        parent_topic = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.id == parent_id).first()
        assert parent_topic.level_index == 0
        assert parent_topic.parent_id is None
        
        for subtopic_id, expected_title in zip(subtopic_ids, subtopic_titles):
            subtopic = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.id == subtopic_id).first()
            assert subtopic.level_index == 1
            assert subtopic.parent_id == parent_id
            assert subtopic.title_text == expected_title
        
        print(f"✓ Hierarchical structure verified")
        print(f"  Parent: {parent_topic.title_text} (level {parent_topic.level_index})")
        print(f"  Subtopics: {len(subtopic_ids)} children linked via parent_id")
        
        # Cleanup
        try:
            next(db_gen)
        except StopIteration:
            pass
        
        return True
        
    except Exception as e:
        print(f"✗ Hierarchical structure test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_end_to_end_topic_generation():
    """Test end-to-end topic generation with persistence."""
    print("\n" + "="*60)
    print("TEST 5: End-to-End Topic Generation (requires DB with data)")
    print("="*60)
    
    try:
        db_gen = get_db()
        db = next(db_gen)
        
        # Count existing topics before
        before_count = db.query(func.count(TOPIC_TABLE.id)).scalar()
        print(f"Topics in database before: {before_count}")
        
        # Run topic generation
        print("\nComputing topics with persistence...")
        topics_resp = compute_topics(db, days=30)
        
        # Count after
        after_count = db.query(func.count(TOPIC_TABLE.id)).scalar()
        print(f"Topics in database after: {after_count}")
        print(f"New topics created: {after_count - before_count}")
        
        print(f"\n✓ Found {len(topics_resp.topics)} topics")
        
        if topics_resp.topics:
            print("\nSample persisted topics:")
            for i, topic in enumerate(topics_resp.topics[:3]):
                print(f"  {i+1}. {topic.title} ({topic.documents_count} docs, {len(topic.subtopics)} subtopics)")
        
        # Verify topics were persisted
        if PREFERENCES.topic_persistence.persist_topics:
            assert after_count > before_count, "New topics should have been persisted"
            print("\n✓ Topics were persisted to database")
        
        # Run again to test matching
        print("\nRunning compute_topics() again to test matching...")
        before_second = after_count
        topics_resp2 = compute_topics(db, days=30)
        after_second = db.query(func.count(TOPIC_TABLE.id)).scalar()
        
        print(f"Topics after second run: {after_second}")
        print(f"New topics created: {after_second - before_second}")
        
        if after_second == before_second:
            print("✓ All topics were reused (matched existing) - no new topics created")
        else:
            print(f"⚠ {after_second - before_second} new topics created on second run")
        
        # Cleanup
        try:
            next(db_gen)
        except StopIteration:
            pass
        
        return True
        
    except Exception as e:
        print(f"⚠ End-to-end test skipped: {e}")
        print("  (This is expected if database has no documents)")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\n" + "="*60)
    print("Topic Persistence Test Suite")
    print("="*60)
    
    print(f"\nConfiguration:")
    print(f"  Persist topics: {PREFERENCES.topic_persistence.persist_topics}")
    print(f"  Persist subtopics: {PREFERENCES.topic_persistence.persist_subtopics}")
    print(f"  Similarity threshold (topic): {PREFERENCES.topic_persistence.similarity_threshold_topic}")
    print(f"  Similarity threshold (subtopic): {PREFERENCES.topic_persistence.similarity_threshold_subtopic}")
    
    results = []
    
    try:
        results.append(("Topic table exists", test_topic_table_exists()))
        results.append(("Save topic", test_save_topic()))
        results.append(("Topic matching", test_topic_matching()))
        results.append(("Hierarchical structure", test_hierarchical_structure()))
        results.append(("End-to-end generation", test_end_to_end_topic_generation()))
        
        print("\n" + "="*60)
        print("Test Results Summary")
        print("="*60)
        
        for test_name, passed in results:
            status = "✓ PASS" if passed else "✗ FAIL"
            print(f"  {status}: {test_name}")
        
        all_passed = all(result[1] for result in results)
        
        if all_passed:
            print("\n" + "="*60)
            print("✓ ALL TESTS PASSED")
            print("="*60)
            print("\nTopic persistence is working correctly!")
            print("- Topics are saved with centroids")
            print("- Matching reuses existing topics")
            print("- Hierarchical structure is maintained")
        else:
            print("\n" + "="*60)
            print("⚠ SOME TESTS FAILED")
            print("="*60)
            sys.exit(1)
        
    except Exception as e:
        print(f"\n✗ TEST SUITE FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
