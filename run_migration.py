#!/usr/bin/env python3
"""Run the vault migration on the database."""

from sqlalchemy import create_engine, text
import os

def run_migration():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        return False
    
    engine = create_engine(database_url)
    
    statements = [
        # Create vaults table
        """
        CREATE TABLE IF NOT EXISTS vaults (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
            is_personal BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            archived_at TIMESTAMPTZ
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_vaults_owner ON vaults(owner_id)",
        
        # Create vault_memberships table  
        """
        CREATE TABLE IF NOT EXISTS vault_memberships (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            vault_id UUID NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'owner',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (vault_id, user_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_vault_memberships_user ON vault_memberships(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_vault_memberships_vault ON vault_memberships(vault_id)",
        
        # Add columns to documents (if they don't exist)
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'documents' AND column_name = 'vault_id'
            ) THEN
                ALTER TABLE documents ADD COLUMN vault_id UUID;
                CREATE INDEX idx_documents_vault ON documents(vault_id);
            END IF;
        END $$
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'documents' AND column_name = 'created_by'
            ) THEN
                ALTER TABLE documents ADD COLUMN created_by UUID REFERENCES users(id) ON DELETE SET NULL;
                CREATE INDEX idx_documents_created_by ON documents(created_by);
            END IF;
        END $$
        """,
        
        # Create personal vault for each user (if they don't have one)
        """
        INSERT INTO vaults (id, name, owner_id, is_personal, created_at)
        SELECT 
            gen_random_uuid(), 
            'Personal Vault', 
            u.id, 
            true, 
            now()
        FROM users u
        WHERE NOT EXISTS (
            SELECT 1 FROM vaults v WHERE v.owner_id = u.id AND v.is_personal = true
        )
        """,
        
        # Create owner memberships for personal vaults
        """
        INSERT INTO vault_memberships (id, vault_id, user_id, role, created_at)
        SELECT 
            gen_random_uuid(),
            v.id,
            v.owner_id,
            'owner',
            now()
        FROM vaults v
        WHERE v.owner_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM vault_memberships vm 
            WHERE vm.vault_id = v.id AND vm.user_id = v.owner_id
        )
        """,
        
        # Assign orphan documents to first user's personal vault
        """
        UPDATE documents d
        SET vault_id = (
            SELECT v.id 
            FROM vaults v 
            WHERE v.is_personal = true 
            ORDER BY v.created_at ASC 
            LIMIT 1
        )
        WHERE d.vault_id IS NULL
        """,
    ]
    
    with engine.connect() as conn:
        for i, stmt in enumerate(statements):
            try:
                conn.execute(text(stmt))
                conn.commit()
                print(f"Statement {i+1}/{len(statements)}: OK")
            except Exception as e:
                print(f"Statement {i+1}/{len(statements)}: ERROR - {e}")
    
    # Check results
    with engine.connect() as conn:
        vaults = conn.execute(text("SELECT COUNT(*) FROM vaults")).scalar()
        memberships = conn.execute(text("SELECT COUNT(*) FROM vault_memberships")).scalar()
        docs_with_vault = conn.execute(text("SELECT COUNT(*) FROM documents WHERE vault_id IS NOT NULL")).scalar()
        docs_without_vault = conn.execute(text("SELECT COUNT(*) FROM documents WHERE vault_id IS NULL")).scalar()
        
        print(f"\n=== Migration Results ===")
        print(f"Vaults created: {vaults}")
        print(f"Memberships created: {memberships}")
        print(f"Documents with vault_id: {docs_with_vault}")
        print(f"Documents without vault_id: {docs_without_vault}")
    
    return True

if __name__ == "__main__":
    run_migration()
