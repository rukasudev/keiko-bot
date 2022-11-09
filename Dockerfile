FROM python:3.10-slim

ENV PYTHONPATH "${PYTHONPATH}:/workspace"

WORKDIR /workspace

COPY requirements.txt .

RUN pip install -r requirements.txt

CMD ["python", "./__main__.py"]