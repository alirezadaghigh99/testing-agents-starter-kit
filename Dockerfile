FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MSWEA_SILENT_STARTUP=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home agent
WORKDIR /home/agent/harness
COPY pyproject.toml README.md ./
COPY testagent ./testagent
RUN pip install -e .
COPY . .
RUN chown -R agent:agent /home/agent && rm -f .env
USER agent

ENTRYPOINT ["python", "-m"]
CMD ["testagent.run", "--help"]
