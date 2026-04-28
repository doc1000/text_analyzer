# Launch Jupyter from Local Backend

1. **Start the dev stack** (includes the Jupyter service):
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up
   ```
   Or `up -d` to run in the background.
 --remove-orphans
 Found orphan containers ([text_analyzer-backend-run-d7915c3f0cf4]) for this project. If you removed or renamed this service in your compose file, you can run this command with the --remove-orphans flag to clean it up."
2. **Open in Firefox**: http://localhost:8888

3. **Token**: None required (runs with `--NotebookApp.token=''`).

---

**To bring up Jupyter only** (if backend/db are already running):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up jupyter
```


to install new modules.  you have to execute this:
%pip install pandas
# then have to re-start the kernel potentially.  