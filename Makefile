.PHONY: lint test build deploy-node1 deploy-node2 logs-backend logs-worker db-migrate db-shell

lint:
	cd backend && ruff check src/ tests/
	cd backend && mypy src/

test:
	cd backend && python -m pytest tests/ -v --tb=short
	cd worker && python -m pytest tests/ -v --tb=short

build:
	docker build -t ghcr.io/tasin/tars-backend:latest ./backend
	docker build -t ghcr.io/tasin/tars-worker:latest ./worker

deploy-node1:
	ssh node1 "cd /opt/tars/deploy/node1 && docker compose pull && docker compose up -d --remove-orphans"

deploy-node2:
	ssh node2 "cd /opt/tars/deploy/node2 && docker compose pull && docker compose up -d --remove-orphans"

logs-backend:
	ssh node1 "cd /opt/tars/deploy/node1 && docker compose logs -f tars-backend"

logs-worker:
	ssh node2 "cd /opt/tars/deploy/node2 && docker compose logs -f tars-worker"

db-migrate:
	cd backend && alembic upgrade head

db-shell:
	docker exec -it tars-db psql -U tars -d tars
