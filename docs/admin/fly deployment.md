

You need to set `API_BASE` in the **test app’s** runtime env (or its `fly.toml`), not just in your local `fly.test.env.conf`.

Here are the two common ways:

## 1. Set it in `fly.toml` for the test app

If you have a separate `fly.test.toml` (recommended):

```toml
app = "vaultbubbles-test"

[env]
  API_BASE = "https://vaultbubbles-test.fly.dev"
```

Then deploy with:

```bash
fly deploy -c fly.test.toml
```

Fly will inject `API_BASE` into the Machines for `vaultbubbles-test`. [fly](https://fly.io/docs/machines/runtime-environment/)

If you’re sharing one `fly.toml`, you can still do:

```toml
[env]
  API_BASE = "https://vaultbubbles-test.fly.dev"
```

but then you must be careful to only use that config when deploying the test app (better to split configs).

## 2. Set it via `fly config` / secrets for the test app

From the CLI, targeting the test app:

```bash
fly config env -a vaultbubbles-test          # see current config env vars
fly secrets set API_BASE=https://vaultbubbles-test.fly.dev -a vaultbubbles-test
```

- `fly secrets set` is typically used for sensitive values, but it works fine for something like `API_BASE` if you don’t mind it being a secret. [docs.vapor](https://docs.vapor.codes/deploy/fly/)
- At runtime inside a `vaultbubbles-test` machine, `process.env.API_BASE` (Node) or `System.getenv("API_BASE")` (other stacks) will now be the test URL.

## Verify it’s applied

After setting it:

```bash
fly ssh console -a vaultbubbles-test
env | grep API_BASE
```

You should see:

```text
API_BASE=https://vaultbubbles-test.fly.dev
```

Once that’s true, your test app should stop calling prod and your logs should land where you expect.