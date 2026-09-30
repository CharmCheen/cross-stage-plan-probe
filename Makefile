.PHONY: test smoke doctor

test:
	python -m pytest -q

smoke:
	python -m cspp.cli smoke --config configs/m0_m2.local.yaml

doctor:
	python -m cspp.cli doctor --config configs/m0_m2.local.yaml
