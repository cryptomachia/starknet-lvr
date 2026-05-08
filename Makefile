.PHONY: help install test lint pull pull-ekubo pull-ohlc pull-funding pull-rpc pull-univ3 pull-pragma \
        backtest backtest-v2 vault hypotheses cointegration sigma-term defispring figures \
        api indexer indexer-loop dev clean repro all cairo-build cairo-test

help:
	@echo "LVR Lab — research + product pipeline"
	@echo
	@echo "  install        Install Python deps + dev tools"
	@echo "  test           Run pytest suite (125+ tests)"
	@echo
	@echo "  Data pulls:"
	@echo "    pull-ekubo     Ekubo public API daily aggregates"
	@echo "    pull-ohlc      Coinbase 30d OHLC for ETH/BTC/STRK"
	@echo "    pull-pragma    Pragma oracle reads via on-chain RPC"
	@echo "    pull-rpc       Starknet RPC event sample (Ekubo Core)"
	@echo "    pull-univ3     DefiLlama Uniswap v3 cross-AMM data"
	@echo "    pull-funding   Extended Exchange perp funding (geo-restricted)"
	@echo "    pull           All of the above"
	@echo
	@echo "  Analysis runs:"
	@echo "    backtest       Daily-aggregate σ_fee + wedge backtest"
	@echo "    backtest-v2    4-strategy vault backtest"
	@echo "    hypotheses     H1-H4 with Newey-West + cluster bootstrap"
	@echo "    cointegration  Engle-Granger on quasi-stable LST pairs"
	@echo "    sigma-term     σ_fee term structure across windows"
	@echo "    figures        Regenerate the 9 paper figures"
	@echo
	@echo "  Live services:"
	@echo "    indexer        Run the indexer once against live Starknet"
	@echo "    indexer-loop   Long-running indexer (Ctrl-C to stop)"
	@echo "    api            Run the FastAPI dashboard backend on :8000"
	@echo
	@echo "  Cairo:"
	@echo "    cairo-build    scarb build (requires Scarb 2.8+)"
	@echo "    cairo-test     scarb cairo-test"
	@echo
	@echo "  repro            Full reproduction: pulls + backtest + figures + tests"

install:
	pip3 install --user -e ".[test,analysis,api]"

# ---------- Data pulls ----------
pull-ekubo:
	python3 scripts/pull_ekubo_data.py

pull-ohlc:
	python3 scripts/pull_coinbase_ohlc.py 30

pull-funding:
	python3 scripts/pull_extended_funding.py 30

pull-rpc:
	python3 scripts/pull_starknet_rpc.py 30

pull-pragma:
	python3 scripts/pull_pragma_oracle.py

pull-univ3:
	python3 scripts/pull_uniswap_v3.py

pull: pull-ekubo pull-ohlc pull-funding pull-rpc pull-univ3 pull-pragma

# ---------- Analysis runs ----------
backtest:
	python3 scripts/run_backtest.py

vault:
	python3 scripts/run_vault_backtest.py

backtest-v2:
	python3 scripts/run_vault_backtest_v2.py

hypotheses:
	python3 scripts/run_hypothesis_tests.py

cointegration:
	python3 scripts/run_cointegration_test.py

sigma-term:
	python3 scripts/run_sigma_fee_term_structure.py

defispring:
	python3 scripts/pull_defispring_gauges.py

figures:
	python3 figures/make_figures.py

# ---------- Live services ----------
indexer:
	PYTHONPATH=src python3 -m lvr_lab.indexer.service --pipelines ekubo --once --max-blocks-per-iter 50

indexer-loop:
	PYTHONPATH=src python3 -m lvr_lab.indexer.service --pipelines ekubo --poll-seconds 10

api:
	PYTHONPATH=src LVR_LAB_DATA_DIR=data uvicorn lvr_lab.api.dashboard:app --host 0.0.0.0 --port 8000

# ---------- Tests + lint ----------
test:
	python3 -m pytest tests/ -v

test-quiet:
	python3 -m pytest tests/ -q

lint:
	ruff check src tests scripts || true

# ---------- Cairo ----------
cairo-build:
	cd cairo/ekubo_greeks && scarb build

cairo-test:
	cd cairo/ekubo_greeks && scarb cairo-test

# ---------- Reproduction ----------
repro: pull backtest backtest-v2 hypotheses cointegration sigma-term figures
	@echo "Reproduction complete. Outputs in data/ and figures/"

clean:
	rm -f data/wedge_timeseries.csv data/wedge_summary.csv
	rm -f data/coinbase_ohlc_*.csv data/extended_funding_*.csv
	rm -f data/cointegration_results.csv data/sigma_fee_term_structure.csv
	rm -f data/h1_h4_results.csv data/vault_nav.csv data/vault_nav_v2.csv
	rm -f data/starknet_events_sample.json data/uniswap_v3_scoping.csv
	rm -rf src/lvr_lab/__pycache__ src/lvr_lab/*/__pycache__
	rm -rf .pytest_cache

all: install repro test
