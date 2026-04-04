.PHONY: setup sync run run-baseline run-context run-shadow test
.PHONY: report

ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

setup:
	cd $(ROOT) && uv sync --dev

sync:
	cd $(ROOT) && uv sync --dev \
		--reinstall-package starship-engine \
		--reinstall-package starship-web \
		--reinstall-package starship-shared \
		--reinstall-package starship-simlab \
		--reinstall-package starship-reports

run: run-context

run-baseline:
	cd $(ROOT) && uv run cli --signal-mode spx

run-context:
	cd $(ROOT) && uv run cli --signal-mode es_context --context-symbol ES --context-exchange CME --context-code ES

run-shadow:
	cd $(ROOT) && uv run cli --signal-mode es_context --context-symbol ES --context-exchange CME --context-code ES --early-alerts-enabled

test:
	cd $(ROOT) && uv run pytest

report:
	cd $(ROOT) && uv run starship-report --date $(DATE)



# uv run uvicorn starship_web.app:app --host 127.0.0.1 --port 8000
# uv run python -m starship_engine.runner

SYSTEMD_UNITS=\
	starship-web.service \
	starship-engine.service \
	starship-alpha-start.service \
	starship-alpha-start.timer \
	starship-alpha-stop.service \
	starship-alpha-stop.timer

.PHONY: systemd-install
systemd-install:
	@echo "Installing systemd units..."
	@for unit in $(SYSTEMD_UNITS); do \
		sudo cp deploy/systemd/$$unit /etc/systemd/system/; \
	done
	@sudo systemctl daemon-reload
	@sudo systemctl enable --now starship-web.service starship-engine.service
	@sudo systemctl enable --now starship-alpha-start.timer starship-alpha-stop.timer
	@echo "Done. Check status with: systemctl status starship-web starship-engine"

.PHONY: env-install
env-install:
	@echo "Installing /opt/starship-alpha/.env from template (will not overwrite existing)..."
	@if [ -f /opt/starship-alpha/.env ]; then \
		echo "Already exists: /opt/starship-alpha/.env"; \
	else \
		sudo mkdir -p /opt/starship-alpha; \
		sudo cp deploy/env/.env.example /opt/starship-alpha/.env; \
		sudo chown $(shell id -un):$(shell id -gn) /opt/starship-alpha/.env; \
		sudo chmod 600 /opt/starship-alpha/.env; \
		echo "Wrote /opt/starship-alpha/.env"; \
	fi

.PHONY: deploy
deploy: env-install systemd-install
	@echo "Deploy complete."

.PHONY: systemd-uninstall
systemd-uninstall:
	@echo "Stopping and removing systemd units..."
	@sudo systemctl disable --now starship-alpha-start.timer starship-alpha-stop.timer || true
	@sudo systemctl disable --now starship-web.service starship-engine.service || true
	@for unit in $(SYSTEMD_UNITS); do \
		sudo rm -f /etc/systemd/system/$$unit; \
	done
	@sudo systemctl daemon-reload
	@echo "Done."
