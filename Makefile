# Perforación IA · objetivos principales
PY      ?= .venv/bin/python
PERFO   ?= .venv/bin/perfo
PYTEST  ?= .venv/bin/pytest

.PHONY: install template test test-slow e2e-smoke e2e-smoke-cpu e2e-full status

install:  ## Crea el entorno (Python 3.12) e instala dependencias
	@command -v uv >/dev/null 2>&1 && { uv venv --python 3.12 .venv && uv pip install --python $(PY) -e '.[dev,lora]'; } \
	  || { python3.12 -m venv .venv && $(PY) -m pip install -U pip && $(PY) -m pip install -e '.[dev,lora]'; }

template:  ## Regenera la plantilla v1, su imagen de referencia y la matriz de priorización
	$(PERFO) template build

test:  ## Pruebas rápidas (sin el modelo real) con cobertura
	$(PYTEST) -m "not slow" --cov=perfo --cov-report=term-missing

test-slow:  ## Pruebas que cargan TrOCR real
	$(PYTEST) -m slow

e2e-smoke:  ## Flujo de extremo a extremo, perfil smoke (usa GPU/MPS si está disponible)
	$(PERFO) e2e --profile smoke

e2e-smoke-cpu:  ## Flujo smoke forzando CPU (verifica el presupuesto de 15 minutos)
	$(PERFO) e2e --profile smoke --device cpu

e2e-full:  ## Flujo de extremo a extremo, perfil full
	$(PERFO) e2e --profile full

status:
	$(PERFO) status
