FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /console

COPY requirements.txt /console/requirements.txt
RUN pip install --no-cache-dir -r /console/requirements.txt

COPY console/app /console/app

EXPOSE 8088

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8088"]
