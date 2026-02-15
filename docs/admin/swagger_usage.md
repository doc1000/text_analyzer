
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