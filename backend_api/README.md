# E-O-A Backend API (starter)

This is a separate, minimal Cloud Run service scaffold. It intentionally does
not send email, read inboxes, write Google Sheets, or modify the existing
Streamlit application. The only route in this first version is `GET /health`.

## Local start

```bash
python -m pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080
```

## Cloud Run / Google Buildpacks

- Build type: Python via Google Cloud buildpacks
- Build context directory: `backend_api`
- Entrypoint: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Function target: leave blank

Keep Cloud Run authentication required. Do not add email passwords, service
account JSON, or other secrets to source control. Operational endpoints will
be added only after authentication, permissions, and safe integration with the
existing outreach engine have been designed and tested.
