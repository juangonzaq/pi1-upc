# Prompt para Claude Code — Núcleo de Perforación IA (modelo + flujo de prueba completo, sin interfaces)

Copia todo lo que está debajo de la línea y pégalo en Claude Code, dentro de una carpeta vacía (o del repositorio del proyecto). Si tienes el documento del proyecto (TB1) en .docx o .pdf, ponlo en `docs/` antes de empezar.

---

## Rol y objetivo

Actúa como ingeniero senior de ML y backend en Python. Vas a construir el **núcleo funcional** de "Perforación IA", un sistema que digitaliza los formatos manuscritos de perforación de taladros largos de una unidad minera subterránea de Hochschild Mining en el Perú.

En esta etapa **no hay interfaces**: ni frontend ni pantallas. Todo se ejecuta desde una **CLI** y una **suite de pruebas**. El entregable es:

1. El modelo de reconocimiento (TrOCR) con inferencia, ajuste fino, calibración de confianza y reentrenamiento.
2. Un **flujo de prueba de extremo a extremo** que procese formatos desde la imagen hasta el registro validado y almacenado, y que cierre el ciclo de aprendizaje continuo.
3. Un reporte automático con los indicadores del proyecto (IE1 a IE5).

Si existe `docs/`, léelo primero: es la especificación de origen (capítulos 1 y 3, historias HU01 a HU16). Si algo de este prompt contradice el documento, avísame antes de decidir.

## Contexto del dominio (lo mínimo que necesitas)

- **El formato** es el "Reporte de equipo de perforación de taladros largos": una página horizontal de diseño fijo, que se llena a mano una vez por equipo y por guardia.
  - Bloques:
    - Cabecera: fecha, turno día/noche, guardia, código de equipo, tajo, operador, horómetro de motor inicial/final y horómetro de percusión inicial/final.
    - Tabla de actividades: 22 filas × 15 columnas. Columnas: N.° de actividad, código de actividad, hora/horómetro inicial y final, labor, taladro repasado, N.° de fila, N.° de taladros, N.° de barras, longitud del taladro (m), ángulo programado y ejecutado, desviación (m) y desviación (%), observaciones.
    - Demoras operativas: labor, código, hora inicial y final, observación.
    - Paradas por fallas: labor, código, hora inicial y final, horas totales, observación.
    - Cierre de guardia y control de aceros.
    - Firmas (solo se verifica que estén presentes).
  - En total hay 46 campos candidatos y más de 400 celdas.
- **Códigos controlados:** actividades 101–107, demoras 201–217, fallas 301–306.
- **Regla impresa en el formato:** % desviación = desviación (m) / longitud del taladro (m) × 100. Una desviación mayor de 2 % debe corregirse.
- **Retos reales de la escritura:**
  - Labores escritas en diagonal a lo largo de varias filas.
  - Trazos verticales que reemplazan valores repetidos.
  - Marcas de revisión en tinta roja.
  - Horas en formato de 12 y de 24 horas.
  - Celdas vacías.
  - Abreviaturas como NV = NIVEL, TJ = TAJO, GAL = GALERÍA.
- **Homologación:** las descripciones de labor, equipo y operador deben convertirse al código del maestro correspondiente. Los maestros se importan desde CSV o Excel y se versionan.
- **Datos reales:** todavía no están disponibles en el repositorio. Por eso el flujo debe funcionar **primero con datos sintéticos** y quedar listo para cambiar a datos reales sin tocar el núcleo.

## Decisiones de diseño que debes respetar

1. **Arquitectura hexagonal (puertos y adaptadores).**
   - El dominio no importa nada de torch, transformers, SQLAlchemy ni del sistema de archivos.
   - Puertos mínimos:
     - `DocumentIngestionPort.submit(document, metadata) -> document_id`
     - `RecognitionPort.recognize(field_image, field_type) -> (text, score, model_version)`
     - `MasterDataPort.import_master(type, file) -> (version, errors)` y `get_master(type, version=None)`
     - `ObjectStoragePort` (put/get inmutable, con metadatos)
     - `RepositoryPort`
     - `ModelRegistryPort` (publish / get_active / metadatos de evaluación)
     - `TelemetryPort`
   - Adaptadores de esta etapa:
     - Sistema de archivos local para objetos.
     - SQLite mediante SQLAlchemy, con el esquema compatible con PostgreSQL 16.
     - Cola en proceso (síncrona) detrás del puerto, para poder cambiarla luego por Celery + Redis.
     - Registro de modelos en disco, con un adaptador opcional para MLflow.
2. **Reconocedor:** TrOCR de Hugging Face. El modelo base es configurable: por defecto `microsoft/trocr-small-handwritten` para CPU, y `microsoft/trocr-base-handwritten` como alternativa. El modelo preentrenado es la **línea base** y el ajustado se compara contra él.
3. **Segmentación por plantilla:** no hay detección de texto general. Una plantilla versionada (JSON) define las coordenadas normalizadas de cada campo o celda, su tipo (`numeric`, `code`, `time`, `date`, `free_text`, `master_ref`) y su obligatoriedad. La segmentación debe ser **determinista**: el mismo documento con la misma plantilla produce exactamente los mismos recortes.
4. **Confianza por campo:** se deriva de las probabilidades por token de la generación (`output_scores`). Implementa al menos dos agregaciones, la media de log-probabilidades y la probabilidad mínima por token, y deja la elegida como configuración.
5. **Calibración:**
   - Escalado de temperatura **por tipo de campo**, ajustado en la mitad de calibración del conjunto de validación.
   - Mide el ECE (15 bins) y gráficos de fiabilidad. La meta es ECE ≤ 0,05 en el conjunto de prueba.
6. **Enrutamiento por confianza:**
   - El umbral por tipo de campo se elige en la **curva cobertura–riesgo**: el punto de mayor cobertura cuyo error residual en los aceptados es ≤ 1 %.
   - Dos compuertas fuerzan la revisión aunque la confianza sea alta: una **regla de negocio violada** o una **homologación por debajo de su umbral**.
   - Si un campo no tiene confianza fiable, nunca se acepta automáticamente.
7. **Homologación (4 etapas):**
   - Normalización: mayúsculas, sin tildes ni signos, y expansión de abreviaturas desde un diccionario configurable.
   - Coincidencia exacta contra el maestro y el diccionario de alias.
   - Coincidencia aproximada con RapidFuzz (Levenshtein y Jaro-Winkler combinados en una puntuación).
   - Decisión: si la mejor candidata supera el umbral, se propone; si no, se devuelven las 3 mejores. La elección del validador se guarda como alias.
   - Metas: Exactitud@1 ≥ 90 % y Recall@3 ≥ 97 %.
8. **Trazabilidad:** el modelo de datos separa documento, página, recorte, resultado de reconocimiento, valor final y corrección, para que nada se sobrescriba.
   - Entidades: Documento (con hash SHA-256), Plantilla (versión y coordenadas), Campo, Recorte, Resultado de reconocimiento (texto, score, versión de modelo), Homologación (código, puntuación, método), Valor validado, Corrección, Maestro versionado, Alias, Versión de modelo, Perfil de calibración, Registro y Estados del documento.
   - Estados: `CARGADO → EN_COLA → PROCESADO → EN_REVISION → VALIDADO → APROBADO`, además de `ERROR` y `DUPLICADO_PENDIENTE`.
9. **Aprendizaje continuo (no se actualiza en línea):**
   - Cada corrección validada se guarda con recorte, valor reconocido, valor validado, confianza, usuario y fecha.
   - Al cierre de un ciclo:
     - Se genera un snapshot versionado del dataset.
     - Se reentrena un candidato mezclando las correcciones con una muestra del corpus histórico (*replay*), para evitar el olvido catastrófico.
     - Se recalibra la confianza y se compara el candidato contra el modelo activo en el **conjunto de prueba congelado**.
   - El candidato debe pasar las compuertas:
     - **G1 Integridad:** procedencia completa del dataset y sin fuga del conjunto de prueba.
     - **G2 Calidad:** CER y exactitud por campo iguales o mejores que el activo.
     - **G3 Calibración:** ECE dentro de la meta y riesgo ≤ 1 % en la cobertura elegida.
     - **G4 Homologación:** Exactitud@1 y Recall@3 no empeoran.
     - **G5 Operación:** latencia p95 dentro del presupuesto y artefacto reproducible.
     - **G6 Aprobación:** promoción explícita con un comando CLI que registra quién aprobó, la fecha, la versión y la evidencia.
   - Si una compuerta falla, el modelo activo no cambia.
10. **Partición de datos:** por **formato**, nunca por campo. Proporción 70/15/15, estratificada por operador de perforación. La validación se divide en una mitad para selección de modelo y otra para calibración. El conjunto de prueba queda **congelado y versionado**, y se agrega una prueba automática de fuga que falla si algún formato de prueba aparece en el entrenamiento.

## Datos sintéticos (imprescindibles para el flujo de prueba)

Construye un generador que produzca formatos completos y realistas a partir de la misma plantilla v1:

- Renderiza el formato vacío (cuadrícula, encabezados, lista de códigos y la nota de la regla del 2 %) y lo llena con valores plausibles y consistentes:
  - Fechas, guardias y horas en formatos mixtos de 12 y 24 horas.
  - Horómetros donde el final sea mayor o igual que el inicial.
  - Longitudes de taladro entre 3 y 15 m.
  - Desviaciones coherentes con la regla, más un porcentaje de casos que la violen.
  - Códigos válidos más algunos inválidos.
  - Labores tomadas de un maestro sintético, escritas con variantes (por ejemplo "TJ 4120 N", "Tajo 4120N" o "NV 4300").
- Para simular la escritura manuscrita:
  - Usa varias fuentes de estilo manuscrito con licencia libre; descárgalas si hay red, y si no la hay, usa transformaciones de fuentes disponibles.
  - Aplica una variación por "operador": inclinación, grosor, *jitter* y tamaño.
  - Agrega ruido de escaneo: rotación leve, perspectiva, desenfoque, manchas, celdas vacías, algunos trazos de repetición y marcas en rojo.
- Por cada formato genera:
  - La imagen a 300 ppp.
  - El *ground truth* por campo en JSON.
  - Un "registro histórico transcrito" en CSV que simule el sistema web actual, con un pequeño porcentaje de errores de transcripción. Así se puede probar la **construcción del dataset por supervisión débil**, que alinea por fecha, guardia y equipo y excluye los casos sin correspondencia única, reportando la causa.
- Genera también maestros sintéticos en CSV (labores, equipos, personal y códigos) y un diccionario de abreviaturas.
- Todo debe ser reproducible con una semilla.

## El flujo de prueba de extremo a extremo

Implementa un comando `perfo e2e --profile smoke|full` que ejecute y registre estos pasos:

1. **Generar** el corpus sintético. El perfil `smoke` debe correr en CPU en menos de 15 minutos (por ejemplo, ~60 formatos y pocos campos por formato). El perfil `full` es configurable.
2. **Importar** los maestros, con validación y versionado. Un maestro inválido se rechaza y se conserva el vigente.
3. **Ingestar** los formatos: hash SHA-256, detección de duplicados e idempotencia (una carga repetida no crea un registro nuevo).
4. **Control de calidad y preprocesamiento:** resolución equivalente a 300 ppp, orientación y corrección de perspectiva/inclinación. Se conservan el original y la versión preprocesada, junto con la transformación aplicada. Si la imagen no es corregible, el procesamiento se bloquea y se registra la causa.
5. **Segmentar** con la plantilla v1, verificando la alineación de la plantilla con una tolerancia configurable.
6. **Reconocer con el modelo base**, que es la línea base. Registra la CER inicial por tipo de campo y el tiempo por formato (p50 y p95).
7. **Estructurar y aplicar reglas:** tipado, rangos, consistencia de horómetros, regla del 2 %, códigos válidos y campos obligatorios.
8. **Homologar** con los maestros.
9. **Construir el dataset** por alineación con el CSV histórico y generar el reporte de calidad de la alineación.
10. **Ajustar TrOCR** (fine-tuning completo; LoRA es opcional y queda detrás de una bandera). Usa *early stopping* con la mitad de selección de modelo.
11. **Evaluar** el modelo base contra el ajustado en el conjunto de prueba congelado: CER, WER, exactitud por campo y por tipo de campo, y CER en operadores no vistos en el entrenamiento. Calcula **IE3** = (CER inicial − CER ajustado) / CER inicial.
12. **Calibrar y fijar umbrales** por tipo de campo. Calcula el ECE, la curva cobertura–riesgo e **IE4** = campos aceptados automáticamente / campos procesados, con su error residual.
13. **Validación humana simulada:** un "validador oráculo" que usa el *ground truth* corrige **solo** los campos enrutados a revisión, elige candidatas de homologación (lo que genera alias) y confirma. Mide cuántos campos tocó.
14. **Almacenar** el registro confirmado como versión inmutable, con trazabilidad completa hasta el recorte. Agrega la **aprobación simulada** del Jefe de Guardia, que puede aprobar u observar.
15. **Ciclo de aprendizaje continuo:** snapshot de las correcciones, reentrenamiento con *replay*, recalibración, compuertas G1–G6 y promoción (o bloqueo). Calcula **IE5** = mejora relativa de la CER frente al modelo previo.
16. **Reporte:** genera `reports/e2e_<fecha>.md` y su `.json` con:
    - Los indicadores IE1–IE5 frente a sus metas (≥30 campos priorizados; 100 % del dataset; ≥20 % de reducción de CER; ≥80 % de reducción de la intervención con error ≤1 %; ≥5 % de mejora en el ciclo).
    - Métricas de calidad: ECE, Exactitud@1 y Recall@3, tiempos p50/p95.
    - Gráficos en PNG: curva cobertura–riesgo, diagrama de fiabilidad y curva de aprendizaje.
    - Una nota visible: **"Resultados sobre datos sintéticos: validan el flujo, no los indicadores del proyecto"**.

Si una meta no se cumple, el reporte lo dice tal cual, con la causa probable. **Nunca ajustes los datos ni las métricas para que una meta "pase".**

## Pruebas automatizadas (pytest)

- **Unitarias de dominio** (sin infraestructura): normalización, reglas (incluida la regla del 2 %), tipado de horas 12/24 h, máquina de estados, decisión de enrutamiento y las dos compuertas que fuerzan revisión.
- **De contrato:** el `RecognitionPort` y el `MasterDataPort` con dobles de prueba, y con el modelo real marcadas como `@pytest.mark.slow`.
- **Determinismo de la segmentación**, e **idempotencia** de la ingesta y del reproceso.
- **Fuga de datos:** ningún formato de prueba puede estar en el entrenamiento ni en la calibración.
- **Resiliencia:**
  - Si el reconocedor no está disponible, se hace un reintento acotado y luego se pasa a `ERROR`.
  - Si falla el reentrenamiento, el modelo activo se mantiene.
  - Si el candidato es peor, la compuerta bloquea la promoción.
- **E2E smoke:** el comando `perfo e2e --profile smoke` corre de principio a fin y produce el reporte.
- Cobertura ≥ 70 % en el código nuevo.

## Stack y estructura

- Python 3.12, con dependencias gestionadas en `pyproject.toml` (uv o pip):
  - Modelo: `transformers`, `torch` (CPU por defecto; usa GPU si está disponible), `datasets` (opcional).
  - Imagen: `pillow`, `opencv-python-headless`.
  - Homologación y métricas: `rapidfuzz`, `jiwer`, `scikit-learn`, `numpy`.
  - Infraestructura y CLI: `sqlalchemy`, `pydantic`, `typer`, `pyyaml`, `matplotlib`.
  - Pruebas: `pytest`, `pytest-cov`.
  - Registro de modelos: `mlflow` (opcional).
- El código y los identificadores van en inglés; los reportes, los mensajes de la CLI y la documentación, en español.
- Estructura sugerida:
  - `src/perfo/domain/` (entidades, reglas, estados, políticas)
  - `src/perfo/application/` (casos de uso)
  - `src/perfo/ports/`
  - `src/perfo/adapters/` (trocr, storage, sqlite, masters, registry)
  - `src/perfo/ml/` (dataset, train, evaluate, calibrate, learning_cycle)
  - `src/perfo/synth/`
  - `src/perfo/cli.py`
  - `configs/` (plantilla v1, umbrales, perfiles smoke/full, abreviaturas)
  - `tests/`, `reports/`, `data/` (en `.gitignore`)
- Agrega un `Makefile` con los objetivos `install`, `test`, `test-slow`, `e2e-smoke` y `e2e-full`.
- Agrega un `README.md` en español que explique cómo correr todo y **cómo cambiar a datos reales**:
  - Dónde poner los escaneos reales, el CSV histórico exportado y los maestros reales.
  - Qué comando usar para alinear y construir el dataset real.
  - Que la plantilla v1 debe recalibrarse sobre el formato físico real.

## Forma de trabajo

1. Antes de escribir código, muéstrame un **plan breve**: estructura del repositorio, campos de la plantilla v1 (propón al menos 30 campos priorizados en una tabla de matriz de priorización, que es el IE1), riesgos técnicos y lo que vas a simular. Espera mi visto bueno.
2. Construye por fases, cada una con sus pruebas pasando antes de continuar:
   - (a) dominio + puertos + persistencia
   - (b) generador sintético + plantilla + segmentación
   - (c) reconocimiento base + reglas + homologación
   - (d) dataset + fine-tuning + evaluación
   - (e) calibración + enrutamiento
   - (f) validación simulada + almacenamiento + aprobación
   - (g) aprendizaje continuo + compuertas
   - (h) comando e2e + reporte
3. Haz un commit por fase con un mensaje claro.
4. No inventes resultados. Si algo no se puede (sin red para descargar el modelo, sin fuentes, sin GPU), dilo, deja la alternativa funcionando y documéntala.
5. Al final, ejecuta `make test` y `make e2e-smoke`, y muéstrame el resumen del reporte, qué metas se alcanzaron con datos sintéticos y los 3 riesgos principales antes de usar datos reales.