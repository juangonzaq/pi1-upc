# Perforación IA · núcleo funcional

Este sistema digitaliza el *Reporte de equipo de perforación de taladros largos*, un formato manuscrito que se llena una vez por equipo y por guardia. Tiene los siguientes componentes:

- Reconocimiento con TrOCR.
- Confianza calibrada por tipo de campo.
- Enrutamiento a revisión humana.
- Homologación contra maestros versionados.
- Almacenamiento con trazabilidad hasta el recorte.
- Aprendizaje continuo a partir de las correcciones validadas.

En esta etapa no hay interfaz web. Todo se ejecuta desde la CLI `perfo`.

> La especificación de origen es el documento del proyecto (TB1, capítulos 1 y 3; historias HU01 a HU16). El prompt de construcción está en [`docs/prompt_claude_code.md`](docs/prompt_claude_code.md).

## Requisitos e instalación

Se necesita Python 3.12. `make install` crea `.venv` con `uv` si está disponible y, si no, con `venv` y `pip`:

```bash
make install
source .venv/bin/activate
perfo --help
```

La primera ejecución descarga de Hugging Face `microsoft/trocr-small-handwritten` (perfil smoke) o `microsoft/trocr-base-handwritten` (perfil full). También descarga las fuentes manuscritas OFL de Google Fonts a `data/fonts/`.

Si no hay red, el generador usa fuentes del sistema con variaciones de estilo. El modelo, en cambio, debe estar en la caché de Hugging Face.

El dispositivo se elige con `device` en `configs/default.yaml` o con `--device`:

- `auto` usa CUDA, luego MPS (Apple) y, si no hay ninguno, CPU.
- En MPS, la generación se ejecuta sin caché KV por un fallo de `transformers` 5.20.

## Flujo de extremo a extremo

```bash
make e2e-smoke          # perfil smoke (usa GPU/MPS si hay)
make e2e-smoke-cpu      # mismo flujo forzando CPU (presupuesto < 15 min)
make e2e-full           # perfil full (configurable en configs/profiles/full.yaml)
```

`perfo e2e` ejecuta y registra 16 pasos:

1. Genera el corpus sintético.
2. Importa los maestros, incluido uno inválido que se rechaza.
3. Ingesta los formatos con hash, duplicados e idempotencia.
4. Preprocesa.
5. Segmenta con verificación de alineación.
6. Reconoce con el modelo base.
7. Aplica las reglas.
8. Homologa.
9. Construye el dataset por supervisión débil.
10. Hace el ajuste fino.
11. Evalúa en el test congelado (IE3).
12. Calibra, fija umbrales y mide IE4.
13. Procesa el lote operativo y lo valida con el oráculo.
14. Almacena con trazabilidad y simula la aprobación.
15. Ejecuta el ciclo de aprendizaje con las compuertas G1–G6 (IE5).
16. Genera el reporte.

El reporte queda en `reports/e2e_<fecha>.md` y `.json`. Incluye tres gráficos PNG: la curva cobertura–riesgo, el diagrama de fiabilidad y la curva de aprendizaje. Cada corrida usa una carpeta nueva bajo `data/e2e/<perfil>/<fecha>/`.

> Los resultados sobre datos sintéticos validan el flujo, no los indicadores del proyecto.

## Uso paso a paso

Todos los comandos aceptan `-p <perfil>` y `-w <carpeta de trabajo>`.

```bash
perfo synth -p smoke -w data/demo                       # corpus sintético
perfo masters import-dir data/demo/synth/masters -w data/demo
perfo ingest --from-synth historical -w data/demo       # o: perfo ingest <carpeta> --metadata meta.csv
perfo process -w data/demo                              # preprocesa, segmenta, reconoce, tipa, homologa y enruta
perfo evaluate preliminary -w data/demo                 # CER inicial por tipo de campo, p50/p95
perfo dataset build -w data/demo                        # alinea con el CSV histórico y congela el test
perfo train -w data/demo [--lora]                       # ajuste fino con early stopping
perfo evaluate models -w data/demo                      # base vs ajustado en el test congelado (IE3)
perfo calibrate -w data/demo                            # temperatura por tipo de campo y umbral por cobertura–riesgo
perfo evaluate routing -w data/demo                     # ECE, curva cobertura–riesgo, IE4 y gráficos
perfo gates <versión> -w data/demo                      # compuertas G1–G5 del candidato
perfo promote <versión> --approver "Nombre" -w data/demo   # G6: promoción explícita con evidencia
perfo registry list -w data/demo
```

### Revisión manual y reentrenamiento posterior

Este es el circuito central: se procesa el formato, se revisa lo que el sistema envía a revisión y luego se reentrena con esas correcciones.

```bash
perfo ingest --from-synth operational -w data/demo      # formatos nuevos
perfo process -w data/demo                              # con el modelo activo y su calibración
perfo review list -w data/demo                          # documentos en revisión y campos que requieren acción
perfo review show O0002 -w data/demo                    # lectura, propuesta, confianza, motivos y candidatas;
                                                        # además genera data/demo/review/O0002.png con los recortes
perfo review confirm O0002 -w data/demo --user jperez \
    --set activities.r01.length_m=10.0 --text activities.r01.length_m="10,0" \
    --choose header.operator=P0014 --clear activities.r05.notes --accept-rest
perfo review tray -w data/demo                          # bandeja del Jefe de Guardia (HU10)
perfo approve O0002 -d aprobar --user "Jefe de Guardia" -w data/demo     # o: -d observar --comment "..."
perfo trace O0002 activities.r01.length_m -w data/demo  # valor → propuesta → reconocimiento → recorte → documento
perfo cycle run -w data/demo                            # snapshot de correcciones + replay + recalibración + G1–G5
perfo promote <candidato> --approver "Jefe de Planeamiento" -w data/demo  # el activo solo cambia si las compuertas pasan
```

Reglas de la validación:

- Solo los campos enrutados a revisión exigen una acción. Si falta una decisión, la confirmación se bloquea y lista los campos.
- `--accept-rest` confirma de forma explícita los campos que quedan.
- Cada confirmación crea una versión inmutable del registro, con hash.
- Cada corrección se guarda con el recorte, el valor reconocido, el valor validado, la confianza, el modelo, el usuario y la fecha.
- Elegir una candidata de homologación aprende un alias.
- Si el Jefe de Guardia observa el registro, este vuelve a revisión y la nueva versión parte de la última validada.
- El ciclo nunca usa correcciones de formatos de prueba, selección, calibración o de la muestra del Sprint 1.

`perfo review oracle` simula al validador usando el *ground truth* sintético.

## Arquitectura

La solución sigue una arquitectura hexagonal (puertos y adaptadores):

```
src/perfo/
  domain/        entidades, plantilla, estados, tipado (horas 12/24 h), reglas, enrutamiento,
                 política de homologación y compuertas (sin torch, transformers, SQLAlchemy ni archivos)
  ports/         ingestion, recognition, masters, storage, repository, registry, telemetry, queue
  application/   ingest, preprocess, segment, structuring, homologation, processing, validation
  adapters/      trocr · sqlite (SQLAlchemy, esquema compatible con PostgreSQL 16) · storage_fs ·
                 masters_file (CSV/XLSX) · registry_disk (+ MLflow opcional) · queue_inproc · telemetry_log
  ml/            dataset, train, evaluate, metrics, pipeline (calibración y enrutamiento), learning_cycle
  synth/         plantilla v1, render del formato, escritura simulada, ruido, maestros, oráculo
  reporting/     reporte e2e, gráficos, hoja de revisión
  cli.py · e2e.py · bootstrap.py (raíz de composición)
configs/         default.yaml · thresholds.yaml · abbreviations.yaml · profiles/{smoke,full}.yaml
                 template_v1.json · template_v1_reference.png · field_priority.csv (matriz, IE1)
```

Estos componentes se pueden reemplazar sin tocar el núcleo, porque cada uno está detrás de su puerto:

| Componente actual | Reemplazo previsto |
|---|---|
| Cola en proceso | Celery + Redis |
| SQLite | PostgreSQL |
| Disco | MinIO/S3 |
| Registro en disco | MLflow (`registry.backend: mlflow`) |

Estados del documento: `CARGADO → EN_COLA → PROCESADO → EN_REVISION → VALIDADO → APROBADO`, más `ERROR` y `DUPLICADO_PENDIENTE`.

## Decisiones principales

**Plantilla v1 y matriz de priorización (IE1).** Se priorizan 36 de los 46 campos candidatos con la fórmula 0,5·R + 0,3·F + 0,2·L ≥ 3,5 (TB1, tabla 4.11); el detalle está en `configs/field_priority.csv`. La valoración es una propuesta pendiente de validación del PO.

**Confianza.** Se calcula como la media geométrica de las probabilidades por token (`mean_logprob`) o como la probabilidad mínima por token (`min_prob`). La elección se hace en `model.aggregation`.

**Calibración y enrutamiento.**
- Se usa escalado de temperatura por tipo de campo, ajustado en la mitad de calibración.
- La temperatura se ajusta contra la correctitud del valor tipado u homologado (`calibration.target: value`), que es lo que decide el enrutamiento. El ECE también se mide sobre el valor; el ECE sobre texto literal se informa aparte.
- El umbral es el punto de mayor cobertura con error residual ≤ 1 %, con un soporte mínimo configurable.
- Un tipo de campo sin temperatura o sin umbral nunca se acepta automáticamente.
- Una regla violada o una homologación bajo su umbral fuerzan la revisión.
- Para certificar un riesgo ≤ 1 %, las etiquetas de la mitad de calibración deben tener menos error que ese 1 %. Las del histórico tienen errores de transcripción (el TB1 estima 4 %), así que se recomienda verificar esa mitad por doble digitación, igual que el subconjunto de prueba.

**Entrenamiento.**
- Hay *early stopping* con la mitad de selección, pero no puede activarse antes del 75 % de los pasos (`training.min_steps_ratio`). Al inicio del ajuste la CER de selección empeora antes de mejorar.

**Dataset por supervisión débil.**
- El histórico guarda valores normalizados (códigos, horas en 24 h), no lo que está escrito.
- La etiqueta literal se deriva generando variantes del valor (formatos, abreviaturas, formas de nombres) y eligiendo la más cercana a la lectura del modelo base. Si no hay una candidata cercana, el recorte se excluye y se reporta la causa.
- La CER se mide sobre el texto literal en mayúsculas.

**Partición.**
- Se parte por formato en 70/15/15, estratificado por operador.
- Hay operadores no vistos que solo aparecen en prueba, y una muestra del Sprint 1 que se excluye.
- El test queda congelado (`split-v1`).
- La prueba de fuga revisa el id, el hash y la clave de negocio.

**Promoción.**
- La activación del primer modelo ajustado exige G1, G2 y G6; G3–G5 se informan.
- Cada ciclo exige G1–G6.
- Si falla una compuerta o el entrenamiento, el modelo activo no cambia.

## Cómo cambiar a datos reales

1. **Plantilla.** Hay que recalibrar la plantilla v1 sobre el formato físico real:
   - Escanee un formato vacío a 300 ppp y reemplace `configs/template_v1_reference.png`.
   - Ajuste las cajas normalizadas de `configs/template_v1.json`, sea editando `src/perfo/synth/layout.py` y ejecutando `perfo template build`, o editando el JSON directamente.
   - Publique la versión nueva (por ejemplo `1.1`). Las versiones son inmutables y cada documento queda asociado a la suya.
   - Revise `alignment.tolerance_px`, `min_dpi`, `min_sharpness` y, en `configs/default.yaml`, `segmentation.binarize_threshold` y `min_ink_px`. Estos valores se calibraron con datos sintéticos.

2. **Maestros.** Ponga las exportaciones del área en `data/real/maestros/` con estos nombres y columnas:

   | Archivo | Columnas obligatorias | Columna opcional |
   |---|---|---|
   | `labores.csv` | `codigo`, `descripcion` | `alias` separados por `\|` |
   | `equipos.csv` | `codigo`, `descripcion` | — |
   | `personal.csv` | `codigo`, `nombre` (en formato `APELLIDOS, NOMBRES`) | — |
   | `codigos.csv` | `tipo` (actividad, demora o falla), `codigo`, `descripcion` | — |

   Se aceptan CSV o XLSX. Luego ejecute `perfo masters import-dir data/real/maestros -w data/real/ws`.

3. **Escaneos y CSV histórico.**
   - Ponga los escaneos en `data/real/escaneos/` y un `metadatos.csv` con las columnas `archivo,fecha,guardia,equipo[,lote]`.
   - Exporte el histórico del sistema web en formato largo, con las columnas `registro_id,fecha,guardia,equipo,campo,valor`.
   - `campo` es el id de la plantilla (por ejemplo `activities.r03.code`) y los valores van normalizados: códigos de maestro, `HH:MM` y fechas ISO.
   - Si la exportación tiene otras columnas, conviértala a este formato antes de construir el dataset.

4. **Alinear y construir el dataset real:**
   ```bash
   perfo ingest data/real/escaneos --metadata data/real/metadatos.csv --user secretaria -w data/real/ws
   perfo process -w data/real/ws
   perfo dataset build --history data/real/registro_historico.csv -w data/real/ws
   ```
   El reporte de calidad de la alineación queda en `data/real/ws/reports/dataset_<snapshot>.json`, con las exclusiones por causa e IE2.

5. **Evaluación con referencia real.** Use un CSV de doble digitación del subconjunto de prueba con las columnas `archivo,campo,valor,texto[,operador]`. Ejecute `perfo evaluate models --reference ref.csv`, `perfo evaluate routing --reference ref.csv` y `perfo cycle run --reference ref.csv`.

6. **Volumen requerido para IE2.** Ajuste `dataset.required_formats` según el supuesto del TB1 (2 000 formatos).

## Pruebas

La suite de pruebas automatizadas (pytest) todavía no se ha creado. Por pedido del usuario, primero se valida que el flujo funcione. Los objetivos `make test` y `make test-slow` ya están definidos, y la carpeta `tests/` está reservada.

## Limitaciones conocidas

- **Escritura simulada.** La escritura se genera con fuentes manuscritas. El ajuste fino aprende esas fuentes y su mejora no se traslada tal cual a la escritura real.
- **Homologación optimista.** Las variantes de labores se generan con el mismo diccionario de abreviaturas que usa la homologación, por lo que la Exactitud@1 sobre texto literal resulta casi perfecta.
- **Calibración en smoke.** En el perfil smoke la mitad de calibración es pequeña y varios tipos de campo no alcanzan el soporte mínimo. Por diseño, esos campos van a revisión.
- **Reproducibilidad.** El entrenamiento en MPS no es reproducible bit a bit. La compuerta G5 verifica la semilla, el hash del snapshot y el hash del artefacto, no un reentrenamiento idéntico.
- **PDF.** La ingesta acepta PDF y lo registra, pero el preprocesamiento solo decodifica imágenes (JPG/PNG). Un PDF queda en `ERROR` con su causa hasta que se agregue un rasterizador, por ejemplo `pypdfium2`.
