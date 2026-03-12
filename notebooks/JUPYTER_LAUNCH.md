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

-- installing modules once jupyter is open:

`ModuleNotFoundError` means pandas really isn’t installed in the Python environment your Jupyter container is using. The key detail: your Jupyter is running in **text_analyzer-jupyter-1**, which is a different container/image from your backend. You need to install pandas *inside the jupyter container image*, not in the backend one.

Here’s the minimal way to fix it.

## 1. Exec into the jupyter container and install pandas there

From your host (not inside any container):

```bash
docker exec -it text_analyzer-jupyter-1 /bin/bash
# or /bin/sh if bash isn’t available
```

Inside that shell, run:

```bash
python3 --version
which python3
which pip

python3 -m pip install --no-cache-dir pandas
```

Then test:

```bash
python3 -c "import pandas; print(pandas.__version__)"
```

If that prints a version, pandas is now available in that container’s Python env.

Go back to Jupyter Lab (that same container, port 8888), restart the kernel, and try:

```python
import pandas as pd
```

It should now work.

## 2. Make it permanent (Dockerfile change)

Right now you’re just modifying a running container; the change will disappear on rebuild. To keep pandas:

In the Dockerfile used to build `text_analyzer-jupyter` (not your backend’s Dockerfile), add a line like:

```dockerfile
RUN pip install --no-cache-dir pandas
```

Then rebuild and restart:

```bash
docker compose build jupyter
docker compose up -d jupyter
```

Now the Jupyter image will always include pandas, and the notebook can import it without extra steps.

The `sys.executable` you printed is inside the jupyter container, so once pandas is installed in that container’s `/usr/local/lib/python3.11/site-packages`, imports will succeed.
