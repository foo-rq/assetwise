# Assetwise

A hypothetical portfolio builder with a Python API. The current version has an interactive design prototype and an API health endpoint. Market data, financial analytics and SQL storage are not implemented yet.

## Run the API on Windows

Use Python 3.14. Run these commands in PowerShell from the repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

The virtual environment keeps this project's packages separate from other Python projects. Using its Python directly avoids PowerShell activation-policy changes. Create the environment once; repeat the install command when dependencies change.

The requirements files pin the tested packages, including indirect dependencies in `requirements-lock.txt`. For an API-only install without test tools, use `requirements.txt` instead of `requirements-dev.txt`.

Open [the health endpoint](http://127.0.0.1:8000/health). It should return:

```json
{"status":"ok"}
```

This confirms that the API responds. It does not check a database or market-data service. [Interactive API documentation](http://127.0.0.1:8000/docs) is available while the server runs. Press Ctrl+C in the terminal to stop it.

If port 8000 is already in use, change `--port 8000` to `--port 8001` and use that port in the browser links. If `python` is not found, install Python from [python.org](https://www.python.org/downloads/) and reopen PowerShell.

## Run the test

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

The health test checks the HTTP status, JSON content type and response body. It needs no API key or internet connection once the packages are installed.

## Open the prototype

Open `prototype/index.html` in a browser. It still runs independently of the API and uses sample allocations. See [the prototype review steps](prototype/README.md) for its supported interactions and limitations.

## Implementation references

- [FastAPI first steps](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [Starlette TestClient](https://starlette.dev/testclient/) for the current HTTPX2 dependency
