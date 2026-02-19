

to set up a new testing/dev app:
fly apps create vaultbubbles-test
fly will give you an endpoint: https://vaultbubbles-test.fly.dev

### Deploy to test branch:
fly deploy --app vaultbubbles-test


### you can add separate config file:
fly.production.toml
fly.test.toml

fly deploy --config fly.test.toml --app vaultbubbles-test

Step 3: Copy Secrets to Test App

Fly secrets are per app.

So run:

fly secrets list --app vaultbubbles


Then set them on test app:

fly secrets set KEY=value --app vaultbubbles-test

or in batch:
Get-Content .env | fly secrets import -a vaultbubbles-test

i should create a local fly.secrets - should reset some of the tokens to specific to that deploy, but otherwise good. 
need DATABASE_URL postgresql://postgres:[YOUR-PASSWORD]@db.vfdrpyflaclqhryszquy.supabase.co:5432/postgres
check bitwarden for pw and instructions

Add this to package.json:

"scripts": {
  "deploy:prod": "fly deploy --app vaultbubbles",
  "deploy:test": "fly deploy --app vaultbubbles-test"
}

deploy with:
npm run deploy:test
