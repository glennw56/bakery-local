.PHONY: setup run board test backup ingest ingest-watch

setup:
	./scripts/setup.sh

run:
	./scripts/run.sh

board:
	HOST=0.0.0.0 ./scripts/run.sh

test:
	./scripts/test.sh

backup:
	./scripts/backup.sh

ingest:
	PYTHONPATH=. .venv/bin/python -m scripts.ingest_drinks

ingest-watch:
	PYTHONPATH=. .venv/bin/python -m scripts.ingest_drinks --watch
