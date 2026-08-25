.PHONY: bench test lint clean

bench: ## build, gate equivalence, sweep duty cycle on both arms, write results/summary.md
	./scripts/run.sh

test:
	go test ./internal/...
	python3 scripts/gate_test.py
	python3 scripts/cost_test.py

lint:
	golangci-lint run
	ruff check scripts
	npx --yes prettier --check load pricing.json

clean:
	docker compose down -v
