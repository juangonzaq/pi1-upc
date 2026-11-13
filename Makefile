# Perforación IA · objetivos principales
PY      ?= .venv/bin/python
PERFO   ?= .venv/bin/perfo
PYTEST  ?= .venv/bin/pytest
PROFILE ?= smoke
WS      ?=
PORT    ?= 8000

.PHONY: install install-web template fisico-data fisico-template fisico-templates fisico api-fisico dev-fisico test test-slow e2e-smoke e2e-smoke-cpu e2e-full status api web-install web web-build dev

install:  ## Crea el entorno (Python 3.12) e instala dependencias
	@command -v uv >/dev/null 2>&1 && { uv venv --python 3.12 .venv && uv pip install --python $(PY) -e '.[dev,lora,web]'; } \
	  || { python3.12 -m venv .venv && $(PY) -m pip install -U pip && $(PY) -m pip install -e '.[dev,lora,web]'; }

install-web:  ## Agrega al entorno existente las dependencias de la API web (FastAPI, Uvicorn)
	@command -v uv >/dev/null 2>&1 && uv pip install --python $(PY) -e '.[web]' || $(PY) -m pip install -e '.[web]'

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

api:  ## API HTTP para la interfaz web (por defecto, la última corrida e2e del perfil; WS=<carpeta> para otra)
	$(PERFO) serve --profile $(PROFILE) --port $(PORT) $(if $(WS),--workspace $(WS))

web-install:  ## Dependencias de la interfaz Next.js (requiere Node.js >= 20.9)
	cd web && npm install

web:  ## Interfaz web en modo desarrollo: http://localhost:3100
	cd web && npm run dev

web-build:  ## Interfaz web compilada (producción local): http://localhost:3100
	cd web && npm run build && npm run start

dev:  ## API y web en paralelo (Ctrl+C detiene ambas)
	$(MAKE) -j2 api web

# ---------------------------------------------------------------- formato físico (diseño de HU06)
FISICO_SRC  ?= data/formatos_fisicos/repo
FISICO_REPO ?= https://github.com/ronaldroca2017/TESIS.git
FISICO_REF  ?= data/fisico/referencia_gen.csv

fisico-data:  ## Descarga del repositorio del proyecto solo los formatos, la plantilla HU06 y el generador (sin node_modules)
	git clone --depth 1 --filter=blob:none --no-checkout $(FISICO_REPO) $(FISICO_SRC)
	cd $(FISICO_SRC) && git sparse-checkout init --no-cone && \
	  printf '%s\n' '/Formato_fisico_01.jpeg' '/Formatos_fisicos/' '/HU06_plantilla/' '!/HU06_plantilla/segmentador/node_modules/' '/generador_formatos/' > .git/info/sparse-checkout && \
	  git checkout main

fisico-template:  ## Regenera configs/template_v2.json desde la plantilla de HU06 (solo si HU06 cambia)
	$(PERFO) template import-hu06 $(FISICO_SRC)/HU06_plantilla/plantilla_v1_reporte-perforacion-tl.json \
	  --reference-image $(FISICO_SRC)/HU06_plantilla/referencia_plana.png --out configs/template_v2.json

fisico-templates:  ## Plantillas de los formatos reales (taladros largos y Jackleg) a partir de sus fotos; las referencias quedan en data/
	$(PERFO) template from-photos $(FISICO_SRC)/Formatos_fisicos

fisico: fisico-templates  ## Formato físico: plantillas reales, códigos, verdad, ingesta, reconocimiento y evaluación contra la verdad (perfil fisico)
	$(PERFO) masters import --type codigos --file configs/masters_fisico/codigos.csv -p fisico
	$(PERFO) truth import-boxes $(FISICO_SRC)/Formatos_fisicos/verdad --images $(FISICO_SRC)/Formatos_fisicos --out $(FISICO_REF) -p fisico
	$(PERFO) ingest $(FISICO_SRC)/Formatos_fisicos --batch operational --user carga.fisico -p fisico
	-$(PERFO) registry import -p fisico
	$(PERFO) process -p fisico --model latest
	$(PERFO) evaluate reference --reference $(FISICO_REF) -p fisico

api-fisico:  ## API para la web sobre el formato físico, con su verdad
	$(PERFO) serve --profile fisico --port $(PORT) --reference $(FISICO_REF)

dev-fisico:  ## API del formato físico y web en paralelo (Ctrl+C detiene ambas)
	$(MAKE) -j2 api-fisico web

