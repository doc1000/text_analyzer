# Launch Jupyter from Local Backend

1. **Start the dev stack** (includes the Jupyter service):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up
   ```
   Or `up -d` to run in the background.

2. **Open in Firefox**: http://localhost:8888

3. **Token**: None required (runs with `--NotebookApp.token=''`).

---

**To bring up Jupyter only** (if backend/db are already running):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up jupyter
```
