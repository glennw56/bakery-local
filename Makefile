.PHONY: setup run board test backup

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
