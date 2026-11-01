.PHONY: help lint format check test screenshot screenshots clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

lint: ## Check code with ruff
	ruff check .

format: ## Auto-format code with ruff
	ruff format .

check: ## Lint, format-check and test (what CI runs)
	ruff check .
	ruff format --check .
	pytest -q

test: ## Run the test suite
	pytest -q

screenshot: screenshots ## Take light + dark screenshots

screenshots: ## Take light + dark screenshots of each page into docs/screenshots/
	bash scripts/take-screenshots.sh

clean: ## Remove build artefacts
	rm -rf build dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
