#!/usr/bin/env python3
"""
Supabase Migration Runner for Persistent Semantic Trees

Applies migrations 007-018 to the Supabase PostgreSQL database.
Each migration is executed sequentially using the original .sql files.

Usage:
    python migrate_to_supabase.py              # Apply migrations (with confirmation)
    python migrate_to_supabase.py --dry-run    # Validate without persisting
"""

import os
import sys
import re
import time
import argparse
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

MIGRATIONS = [
    "007_tree_infrastructure.sql",
    "008_semantic_tree_v2.sql",
    "009_merge_tree_into_semantic_tree_v2.sql",
    "010_node_document_document_fk.sql",
    "011_semantic_tree_anchors_staging.sql",
    "012_fetch_subtree_for_documents.sql",
    "013_label_sem_tree.sql",
    "014_hybrid_node_labeling.sql",
    "015_label_refinement_async.sql",
    "016_fetch_tree_flat_compression_metadata.sql",
    "017_cluster_unique_attach_document.sql",
    "018_pca_model.sql",
]


def get_connection_string():
    raw_url = os.getenv("DATABASE_URL")
    if not raw_url:
        print("ERROR: DATABASE_URL not set. Check your .env file.")
        sys.exit(1)
    url = raw_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    url = url.replace("postgres://", "postgresql://", 1)
    return url


def strip_transaction_control(sql: str) -> str:
    """Remove BEGIN/COMMIT statements for dry-run (single-transaction) mode."""
    sql = re.sub(r"^\s*BEGIN\s*;\s*$", "", sql, flags=re.MULTILINE | re.IGNORECASE)
    sql = re.sub(r"^\s*COMMIT\s*;\s*$", "", sql, flags=re.MULTILINE | re.IGNORECASE)
    return sql


def preflight_checks(cur):
    """Run pre-flight checks and report current database state. Returns True if safe to proceed."""
    print("=" * 60)
    print("PRE-FLIGHT CHECKS")
    print("=" * 60)

    cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    has_pgvector = cur.fetchone() is not None
    print(f"  pgvector extension: {'YES' if has_pgvector else 'NO'}")
    if not has_pgvector:
        print("  FATAL: pgvector extension required. Enable it in the Supabase dashboard.")
        return False

    cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'")
    print(f"  pgcrypto extension: {'YES' if cur.fetchone() is not None else 'NO'}")

    cur.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'semantic_tree_v2'"
    )
    has_schema = cur.fetchone() is not None
    print(
        f"  semantic_tree_v2 schema: {'EXISTS (partial migration?)' if has_schema else 'does not exist'}"
    )

    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'documents'
          AND column_name = 'reduced_embedding'
    """)
    print(
        f"  documents.reduced_embedding: {'EXISTS' if cur.fetchone() is not None else 'does not exist'}"
    )

    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'documents'
          AND column_name = 'user_tags'
    """)
    print(
        f"  documents.user_tags: {'EXISTS' if cur.fetchone() is not None else 'does not exist'}"
    )

    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'ingest_queue'"
    )
    print(
        f"  ingest_queue (from main 007): {'EXISTS' if cur.fetchone() is not None else 'does not exist'}"
    )

    cur.execute("SELECT id FROM users WHERE email = 'test@example.com'")
    test_user = cur.fetchone()
    print(
        f"  test@example.com user: {'EXISTS (id=' + str(test_user[0])[:8] + '...)' if test_user else 'MISSING (needed for tests)'}"
    )

    cur.execute("SELECT COUNT(*) FROM users")
    user_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM vaults")
    vault_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM vault_memberships")
    membership_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM documents")
    doc_count = cur.fetchone()[0]

    print(f"\n  Existing data:")
    print(f"    users:             {user_count}")
    print(f"    vaults:            {vault_count}")
    print(f"    vault_memberships: {membership_count}")
    print(f"    documents:         {doc_count}")

    cur.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'embedding' AND table_name = 'pca_model'
    """)
    print(
        f"  embedding.pca_model: {'EXISTS' if cur.fetchone() is not None else 'does not exist'}"
    )

    print("=" * 60)
    return True


def run_migration_file(conn, filename, dry_run=False):
    """Execute a single migration SQL file. Returns True on success."""
    filepath = MIGRATIONS_DIR / filename
    if not filepath.exists():
        print(f"  FILE NOT FOUND: {filepath}")
        return False

    sql = filepath.read_text(encoding="utf-8")
    if dry_run:
        sql = strip_transaction_control(sql)

    cur = conn.cursor()
    try:
        cur.execute(sql)
        return True
    except Exception as e:
        print(f"\n  ERROR: {e}")
        return False
    finally:
        cur.close()


def post_migration_checks(cur):
    """Verify migration results. Returns True if all critical checks pass."""
    print("\n" + "=" * 60)
    print("POST-MIGRATION VERIFICATION")
    print("=" * 60)

    checks = []

    cur.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'semantic_tree_v2'"
    )
    checks.append(("semantic_tree_v2 schema", cur.fetchone() is not None))

    for table in [
        "vault_user_role",
        "tree_node",
        "tree_node_stats",
        "node_document",
        "document_anchor",
    ]:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'semantic_tree_v2' AND table_name = %s",
            (table,),
        )
        checks.append((f"semantic_tree_v2.{table}", cur.fetchone() is not None))

    for col in ["reduced_embedding", "user_tags"]:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'documents' AND column_name = %s",
            (col,),
        )
        checks.append((f"documents.{col}", cur.fetchone() is not None))

    for col in [
        "node_type", "centroid", "node_role",
        "title_source", "label_signals", "label_status",
    ]:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'semantic_tree_v2' AND table_name = 'tree_node' "
            "AND column_name = %s",
            (col,),
        )
        checks.append((f"tree_node.{col}", cur.fetchone() is not None))

    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'embedding' AND table_name = 'pca_model'"
    )
    checks.append(("embedding.pca_model", cur.fetchone() is not None))

    cur.execute("SELECT COUNT(*) FROM semantic_tree_v2.vault_user_role")
    vur_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM vault_memberships")
    vm_count = cur.fetchone()[0]
    checks.append(
        (f"vault_user_role sync ({vur_count} roles >= {vm_count} memberships)", vur_count >= vm_count)
    )

    for func in [
        "user_has_vault_role",
        "create_node",
        "create_cluster_node",
        "attach_document",
        "fetch_tree_flat",
        "ensure_staging_node",
        "fetch_subtree_for_documents",
        "update_node_label",
        "recompute_node_centroids",
    ]:
        cur.execute(
            "SELECT 1 FROM information_schema.routines "
            "WHERE routine_schema = 'semantic_tree_v2' AND routine_name = %s",
            (func,),
        )
        checks.append((f"function {func}()", cur.fetchone() is not None))

    all_ok = True
    for label, ok in checks:
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {label}")
        if not ok:
            all_ok = False

    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'tree_nodes'"
    )
    tree_nodes_gone = cur.fetchone() is None
    print(f"  [{'OK' if tree_nodes_gone else 'WARN'}] public.tree_nodes dropped (expected after 009)")

    print("=" * 60)
    return all_ok


def main():
    parser = argparse.ArgumentParser(
        description="Apply semantic tree migrations (007-018) to Supabase"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all migrations in a single transaction then rollback",
    )
    args = parser.parse_args()

    conn_str = get_connection_string()
    display_url = re.sub(r"://[^:]+:[^@]+@", "://***:***@", conn_str)
    mode = "DRY RUN (will rollback)" if args.dry_run else "LIVE (will commit)"

    print(f"Target: {display_url}")
    print(f"Mode:   {mode}")
    print(f"Migrations: {MIGRATIONS[0]} .. {MIGRATIONS[-1]} ({len(MIGRATIONS)} files)")
    print()

    # ── dry-run: single transaction, rollback at end ─────────────────────
    if args.dry_run:
        conn = psycopg2.connect(conn_str)
        conn.autocommit = False
        cur = conn.cursor()

        if not preflight_checks(cur):
            conn.close()
            sys.exit(1)

        print(f"\nApplying {len(MIGRATIONS)} migrations (dry-run)...\n")
        for filename in MIGRATIONS:
            t0 = time.time()
            print(f"  {filename} ... ", end="", flush=True)
            ok = run_migration_file(conn, filename, dry_run=True)
            elapsed = time.time() - t0
            if ok:
                print(f"OK ({elapsed:.1f}s)")
            else:
                print(f"FAILED ({elapsed:.1f}s)")
                conn.rollback()
                conn.close()
                print("\nDry run FAILED. No changes applied.")
                sys.exit(1)

        post_migration_checks(cur)
        cur.close()
        conn.rollback()
        conn.close()
        print("\nDry run complete. All migrations valid. NO changes persisted.")
        return

    # ── live: autocommit, each file manages its own transaction ──────────
    conn = psycopg2.connect(conn_str)
    conn.autocommit = True
    cur = conn.cursor()

    if not preflight_checks(cur):
        cur.close()
        conn.close()
        sys.exit(1)

    answer = input("\nProceed with LIVE migration? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        cur.close()
        conn.close()
        sys.exit(0)

    print(f"\nApplying {len(MIGRATIONS)} migrations...\n")
    for filename in MIGRATIONS:
        t0 = time.time()
        print(f"  {filename} ... ", end="", flush=True)
        ok = run_migration_file(conn, filename, dry_run=False)
        elapsed = time.time() - t0
        if ok:
            print(f"OK ({elapsed:.1f}s)")
        else:
            print(f"FAILED ({elapsed:.1f}s)")
            cur.close()
            conn.close()
            print("\nMigration FAILED. Previously applied files in this run were committed.")
            sys.exit(1)

    post_migration_checks(cur)
    cur.close()
    conn.close()
    print("\nAll migrations applied successfully.")


if __name__ == "__main__":
    main()
