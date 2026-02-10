-- Generate verification code from within database

CREATE EXTENSION IF NOT EXISTS pgcrypto;


CREATE OR REPLACE FUNCTION generate_verification_code(
    p_email TEXT,
    p_purpose TEXT
)
RETURNS TEXT AS $$
DECLARE
    v_code TEXT;
    v_interval INTERVAL;
BEGIN
    -- Validate purpose input
    IF p_purpose NOT IN ('reviewer', 'user') THEN
        RAISE EXCEPTION 'Invalid purpose: %, must be "reviewer" or "user"', p_purpose;
    END IF;

    -- Set interval based on purpose
    IF p_purpose = 'reviewer' THEN
        v_interval := INTERVAL '14 days';
    ELSIF p_purpose = 'user' THEN
        v_interval := INTERVAL '15 minutes';
    END IF;

    -- Generate 6-digit code
    v_code := LPAD((TRUNC(random() * 1000000))::TEXT, 6, '0');

    -- Delete existing code for same email + purpose
    DELETE FROM extension_verification_codes
    WHERE email = p_email AND purpose = p_purpose;

    -- Insert new code
    INSERT INTO extension_verification_codes (
        id, email, code, purpose, created_at, expires_at
    )
    VALUES (
        gen_random_uuid(),
        p_email,
        v_code,
        p_purpose,
        NOW(),
        NOW() + v_interval
    );

    RETURN v_code;
END;
$$ LANGUAGE plpgsql;


select generate_verification_code('testuser@example.com','user');


