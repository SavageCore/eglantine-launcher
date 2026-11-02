.PHONY: help lint format check test screenshot screenshots clean appimage release major minor patch

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

lint: ## Check code with ruff
	ruff check .

format: ## Auto-format code with ruff
	ruff format .

check: ## Lint, format-check and test (what CI runs)
	ruff check .
	ruff format --check .
	xvfb-run -a pytest -q

test: ## Run the test suite
	xvfb-run -a pytest -q

screenshot: screenshots ## Take light + dark screenshots

screenshots: ## Take light + dark screenshots of each page into docs/screenshots/
	bash scripts/take-screenshots.sh

clean: ## Remove build artefacts
	rm -rf build dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

appimage: ## Rebuild ~/eglantine-launcher.AppImage (quit the launcher first)
	env -u APPDIR bash packaging/build-appimage.sh

release: ## Cut a release: check, bump (default patch), commit, push + tag (V=0.2.0 or major|minor|patch)
	bash scripts/release.sh $(V) $(filter major minor patch,$(MAKECMDGOALS))
major minor patch:
	@:
