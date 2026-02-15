
Syntax
Header
Send it in the X-Bootstrap-Token header.
Value
It must exactly match the VB_BOOTSTRAP_TOKEN environment variable set on the Fly.io app.
Format
It’s a plain string; there’s no special format or encoding. Any value works as long as it matches the env var.
Example
curl -X POST -H "X-Bootstrap-Token: YOUR_VB_BOOTSTRAP_TOKEN" https://your-app.fly.dev/admin/backfill
Replace:
YOUR_VB_BOOTSTRAP_TOKEN with the actual value of VB_BOOTSTRAP_TOKEN from Fly.io secrets
your-app.fly.dev with your Fly.io app domain
Behavior (from app/auth.py)
auth.py
Lines 311-316
def require_bootstrap_token(x_bootstrap_token: Optional[str] = Header(None)) -> None:    expected = os.getenv("VB_BOOTSTRAP_TOKEN", "")    if not expected:        raise HTTPException(status_code=500, detail="Server misconfigured: VB_BOOTSTRAP_TOKEN not set")    if not x_bootstrap_token or x_bootstrap_token != expected:        raise HTTPException(status_code=401, detail="Unauthorized")
Missing/incorrect token → 401 Unauthorized
VB_BOOTSTRAP_TOKEN not set on the server → 500 Server misconfigured
Fly.io setup
Define the secret:
fly secrets set VB_BOOTSTRAP_TOKEN=your-secret-token-here
Then use that same value in the X-Bootstrap-Token header when calling /admin/backfill.



Accessing Swagger locally
Swagger UI is at:
Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc
/docs and /openapi.json are public, so you can open Swagger without any auth.
Auth for protected endpoints
Most endpoints use Bearer token auth (API key).
1. Create an API key
Set VB_BOOTSTRAP_TOKEN in .env (e.g. dev-bootstrap-token-change-me from env.example).
In Swagger, call POST /auth/bootstrap/create-key:
Header: X-Bootstrap-Token: dev-bootstrap-token-change-me (or your value)
Body: {"email": "your@email.com"}
The response includes api_key (e.g. vb_live_abc123xyz...). Save it; it’s only shown once.
2. Authorize in Swagger
Click Authorize in Swagger UI.
In the Bearer auth field, enter the full API key (e.g. vb_live_abc123xyz...).
Swagger adds the Bearer prefix for you.
Click Authorize, then Close.
Auth types by endpoint
Endpoint type	Auth
Most API endpoints (documents, query, topics, etc.)	Bearer token (API key)
/auth/bootstrap/create-key	X-Bootstrap-Token header (must match VB_BOOTSTRAP_TOKEN)
/admin/* (e.g. backfill)	X-Bootstrap-Token header
Local auth behavior
VB_REQUIRE_AUTH defaults to false, so some endpoints may work without a token.
For a consistent experience, create an API key and use it in Swagger.
API keys use the vb_live_ prefix; extension tokens use vb_ext_.