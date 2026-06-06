FROM python:3.11-slim

WORKDIR /workspace/live-hermes
ENV PYTHONPATH=/workspace/live-hermes

RUN pip install --no-cache-dir 'pydantic>=2,<3'

CMD ["python", "/workspace/live-hermes/deploy/docker/job-intel-exporter.py"]
