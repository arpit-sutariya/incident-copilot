# Shortcuts for macOS/Linux. The commands are also in README.md.
.PHONY: setup console shop test eval eval-ai eval-oracle

setup:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

console:
	.venv/bin/python -m console.app

shop:
	.venv/bin/python -m shoplite.run

test:
	.venv/bin/python -m pytest -q

eval:
	.venv/bin/python -m eval.run_eval

eval-ai:
	.venv/bin/python -m eval.run_eval --diagnoser ai

eval-oracle:
	.venv/bin/python -m eval.run_eval --diagnoser oracle
