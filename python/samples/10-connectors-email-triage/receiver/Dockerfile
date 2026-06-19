# Receiver container image for the sandboxes-connectors-email-triage
# scenario. Slim Python image; only deps are listed in requirements.txt.

FROM mcr.microsoft.com/azurelinux/base/python:3.12

WORKDIR /app

# Install dependencies first so re-builds with unchanged requirements
# can reuse the layer. Don't try to `pip install --upgrade pip` — the
# azurelinux base ships pip via RPM (no RECORD file), so an in-place
# pip upgrade fails. The shipped pip is recent enough for everything
# in requirements.txt.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Honour PORT if the platform sets one; ACA defaults targetPort=8080.
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
