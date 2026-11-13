# Perforación IA · núcleo funcional

Este sistema digitaliza el *Reporte de equipo de perforación de taladros largos*, un formato manuscrito que se llena una vez por equipo y por guardia. Tiene los siguientes componentes:

- Reconocimiento con TrOCR.
- Confianza calibrada por tipo de campo.
- Enrutamiento a revisión humana.
- Homologación contra maestros versionados.
- Almacenamiento con trazabilidad hasta el recorte.
- Aprendizaje continuo a partir de las correcciones validadas.

Se opera desde la CLI `perfo` o desde una interfaz web local en Next.js (ver [Interfaz web local](#interfaz-web-local)), que usa los mismos servicios a través de una API HTTP.

> La especificación de origen es el documento del proyecto (TB1, capítulos 1 y 3; historias HU01 a HU16). El prompt de construcción está en [`docs/prompt_claude_code.md`](docs/prompt_claude_code.md).

## Requisitos e instalación

Se necesita Python 3.12. `make install` crea `.venv` con `uv` si está disponible y, si no, con `venv` y `pip`. Instala también el extra `web` (FastAPI, Uvicorn y python-multipart) que usa la interfaz web; si ya tenía el entorno creado, basta con `make install-web`:

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

## Interfaz web local

La interfaz web permite probar el circuito sin la CLI:

1. Reconocer un formato con el modelo activo o con otra versión.
2. Ver el resultado campo por campo sobre la imagen alineada.
3. Revisar y corregir a mano lo que el sistema envió a revisión.
4. Aprobar el registro.
5. Reentrenar con esas correcciones y promover el candidato.

Funciona con dos procesos locales:

| Proceso | Qué es | Dirección |
|---|---|---|
| API | `perfo serve`: adaptador HTTP (FastAPI) sobre los mismos servicios de aplicación que la CLI | http://127.0.0.1:8000 (documentación interactiva en `/docs`) |
| Web | Next.js 16 (App Router, TypeScript, Tailwind CSS 4) en `web/` | http://localhost:3100 |

### Requisitos

- **Python.** Python 3.12 con el entorno del proyecto (`make install`, o `make install-web` si el entorno ya existía).
- **Node.js.** Node.js 20.9 o superior y npm. Se probó con Node 22.22 y npm 10.9.
- **Espacio de trabajo.** Uno con modelo ajustado y calibrado, maestros importados y corpus sintético. La forma más rápida de tenerlo es `make e2e-smoke`, que tarda unos 6 min con MPS y unos 12 min en CPU.

### Puesta en marcha

```bash
make install          # entorno Python (una vez)
make e2e-smoke        # modelo ajustado, calibración, maestros y corpus sintético (una vez)
make web-install      # dependencias de Next.js (una vez)
make dev              # API y web en paralelo; Ctrl+C detiene ambas
```

Si prefiere ver los registros por separado, use dos terminales:

```bash
make api              # terminal 1: perfo serve sobre la última corrida e2e del perfil smoke
make web              # terminal 2: next dev en http://localhost:3100
```

Luego abra http://localhost:3100. La web usa el puerto 3100 y no el 3000 habitual de Next.js porque el 3000 suele estar ocupado por otras herramientas locales (por ejemplo, Metabase en Docker). La barra lateral indica si la API está conectada, el dispositivo (CPU, MPS o CUDA) y el modelo activo.

Opciones:

- **Otra carpeta de trabajo.** `make api` sirve la corrida más reciente de `data/e2e/smoke/`. Para otra, use `make api WS=data/e2e/smoke/<fecha>` o `perfo serve -w <carpeta>`. Con `PROFILE=full` se usa el perfil full.
- **Otro puerto de la API.** Use `make api PORT=8100` y cree `web/.env.local` con `NEXT_PUBLIC_PERFO_API=http://127.0.0.1:8100` (hay un ejemplo en `web/.env.example`). Después reinicie `make web`.
- **Versión compilada.** `make web-build` compila la interfaz y la sirve en el puerto 3100. Es más rápida que el modo desarrollo.

> La web modifica la carpeta de trabajo que sirve la API: agrega documentos, correcciones, alias y versiones de modelo. Para volver a empezar, ejecute otra vez `make e2e-smoke`, que crea una carpeta nueva.

### Recorrido de prueba

1. **Reconocer formato.**
   - *Generar formatos nuevos* crea formatos sintéticos «demo» (`D0001`, `D0002`, …). Usan los mismos maestros y la misma letra de cada operador, tienen verdad conocida y ningún modelo los vio al entrenar.
   - *Procesar* ejecuta la ingesta, el control de calidad, la alineación, la segmentación, la lectura con TrOCR, las reglas, la homologación y el enrutamiento. Luego abre el resultado.
   - También puede subir un JPG o PNG propio. Si indica fecha, guardia y equipo, se activa la detección de duplicados.
2. **Documento.**
   - Muestra la imagen alineada con una caja por campo: verde si es automático, ámbar si va a revisión y azul si ya tiene decisión. También muestra los tiempos por etapa.
   - Para cada campo muestra el recorte, el texto leído, el valor propuesto, la confianza calibrada frente al umbral de su tipo, los motivos de revisión y las candidatas de homologación.
   - En formatos sintéticos agrega la verdad y si el valor coincide, y una pestaña con los errores frente a la verdad.
3. **Revisión manual.** En cada campo a revisión se puede elegir:
   - **Confirmar** la propuesta.
   - **Corregir** el valor. Opcionalmente se agrega el texto tal como está escrito, lo que mejora la etiqueta para el reentrenamiento.
   - **Elegir una candidata**, con lo que el sistema aprende un alias.
   - **Marcarlo como vacío.**

   La revisión muestra un campo a la vez, junto al formato alineado, y avanza sola al siguiente campo sin decidir. Atajos de teclado: <kbd>Enter</kbd> confirma, <kbd>E</kbd> corrige, <kbd>V</kbd> marca vacío, <kbd>1</kbd> a <kbd>3</kbd> eligen una candidata del maestro, y <kbd>J</kbd> y <kbd>K</kbd> pasan al campo siguiente o al anterior.

   *Validar registro* guarda una versión inmutable con hash y ofrece abrir el siguiente formato por revisar. Después el Jefe de Guardia aprueba u observa el registro; si lo observa, vuelve a revisión. *Rellenar con la verdad* simula al validador y existe solo para la demo.

   Las decisiones se guardan como borrador en el navegador, así que recargar la página no las borra. Solo cuentan como correcciones para el reentrenamiento después de *Validar registro*. *Volver a leer* descarta el borrador, porque cambia las lecturas sobre las que se decidió.

   Las horas sin AM o PM necesitan el turno para validarse. En los formatos físicos la referencia no trae el turno, así que *Rellenar con la verdad* lo deja por decidir.
4. **Banco de recortes.**
   - Prueba el reconocedor por sí solo. Compara versiones (por defecto, la línea base frente al modelo activo) sobre los mismos recortes no vistos.
   - Reporta exactitud literal, CER, cuántos recortes se aceptarían automáticamente con la calibración de cada versión y cuántos de esos serían errores.
   - Acepta también un recorte propio.
5. **Modelos y reentrenamiento.**
   - *Ejecutar ciclo* corre en segundo plano y muestra su registro en vivo. Los pasos son: snapshot de correcciones, reentrenamiento desde el modelo activo con *replay*, recalibración y compuertas G1–G5. En smoke con MPS tarda de 2 a 4 min.
   - Si queda un candidato, *Promover* registra la aprobación (G6). La promoción se rechaza si falla una compuerta exigida.
   - Una vez promovido el candidato, los formatos siguientes se reconocen con él.
6. **Evolución.**
   - Muestra cuántos caracteres de cada 100 lee bien cada versión sobre el mismo conjunto de formatos, qué tipos de dato mejoraron y ejemplos de un mismo recorte leído por la primera y la última versión.
   - Para sumar un punto, ejecute un ciclo con *Ejecutar ciclo* o `perfo cycle run`, y luego `perfo evaluate reference`. Los ejemplos se calculan una vez y quedan en `reports/`.
   - Un aviso indica si aún no hay reentrenamientos con correcciones, si los formatos son generados o si una versión ya no tiene sus pesos. Con escritura real de operadores, ponga `real_handwriting: true` en la configuración.

### Diseño

La interfaz usa la línea de marca de Hochschild Mining:
- **Logo e ícono:** tomados de su sitio y guardados en `web/public/brand/`.
- **Colores:** dorado `#C29A39`, tinta `#24211F`, plata `#BBBCBE` y grises `#F4F4F4`/`#F9F9F9`.
- **Esquinas:** rectas, como en el sitio.

La tipografía es Outfit, como sustituta libre de su Galano Grotesque, más Helvetica Neue. Los estados (automático, a revisión, decidido y error) tienen colores validados para daltonismo y siempre van con ícono y texto. Los tokens están en `web/app/globals.css`.

### API

La interfaz solo usa estos endpoints. También sirven para integrar otra aplicación.

| Método y ruta | Uso |
|---|---|
| `GET /api/health`, `GET /api/status` | Conexión, dispositivo, modelo activo, documentos por estado y correcciones disponibles |
| `GET /api/samples`, `POST /api/samples/generate`, `POST /api/samples/{id}/process` | Formatos sintéticos de prueba |
| `POST /api/documents` (multipart) | Registra y reconoce un escaneo (`file`, `user`, `model`, `fecha`, `guardia`, `equipo`) |
| `GET /api/documents`, `GET /api/documents/{id}` | Lista y detalle de los campos (recorte, lectura, confianza, ruta, motivos, homologación y verdad) |
| `POST /api/documents/{id}/process`, `POST /api/documents/{id}/confirm-duplicate` | Reprocesa con otra versión de modelo o confirma un duplicado |
| `POST /api/documents/{id}/validate`, `POST /api/documents/{id}/approve` | Revisión manual (decisiones `confirm`, `correct`, `choose` y `clear`) y aprobación |
| `GET /api/documents/{id}/trace/{campo}` | Trazabilidad desde el valor hasta el recorte y el documento |
| `GET /api/bench/crops`, `POST /api/bench/run`, `POST /api/bench/upload` | Banco de recortes |
| `GET /api/models`, `POST /api/cycle`, `GET /api/jobs/{id}`, `POST /api/models/{v}/promote` | Registro, ciclo de aprendizaje y promoción |
| `GET /api/evolution`, `GET /api/evolution/examples` | Mediciones por versión y comparación de lecturas recorte por recorte |

La API escucha solo en `127.0.0.1` y no tiene autenticación: está pensada para uso local. Atiende un modelo a la vez. Mientras corre un ciclo, procesar o promover responde 409 con el motivo, pero las consultas siguen funcionando.

### Problemas frecuentes

- **«API sin conexión» o «No se pudo conectar con la API».** Compruebe que `make api` esté corriendo y que `NEXT_PUBLIC_PERFO_API` coincida con el puerto. Después de cambiar `.env.local`, reinicie `make web`.
- **La primera lectura tarda varios segundos.** Es la carga del modelo en memoria (de 5 a 20 s). Las siguientes tardan alrededor de 1 s por formato con MPS.
- **Error de CORS.** La API acepta los orígenes `localhost` y `127.0.0.1` en los puertos 3100 y 3000. Si la web corre en otro origen, agréguelo con `perfo serve --origin http://localhost:3200`.
- **El puerto 3100 está ocupado.** Use `cd web && npx next dev --port 3200` y agregue ese origen a la API como en el punto anterior.
- **`make web` se cierra con `make: *** [web] Error 1`.** Si la salida dice «Another next dev server is already running», ya hay otro `next dev` en `web/`, aunque sea en otro puerto: Next permite uno por carpeta. Recuerde que `make dev` y `make dev-fisico` ya levantan la web, así que no hace falta `make web` aparte. Para detener el otro servidor, use el PID que muestra el mensaje.
- **Se reinició la API durante un ciclo.** Los trabajos viven en memoria y ese ciclo se pierde. El registro de modelos no cambia, porque el candidato se registra solo al terminar el entrenamiento. Vuelva a ejecutar el ciclo.
- **Un PDF queda en ERROR.** El preprocesamiento todavía no rasteriza PDF (ver Limitaciones). Use JPG o PNG.
- **Datos reales.** Sirva la carpeta de trabajo real con `perfo serve -w <carpeta> --reference ref.csv`. Sin corpus sintético, la página de reconocimiento solo ofrece subir escaneos. La verdad de cada campo aparece cuando el nombre del archivo figura en el CSV de doble digitación.

## Formato físico del proyecto (HU06)

El repositorio del proyecto (`github.com/ronaldroca2017/TESIS`) trae el diseño real del *Reporte de equipo de perforación de taladros largos*. Lo midió HU06 sobre una página plana de referencia (`referencia_plana.png`, 2912×1776 px) e incluye 50 fotos generadas sobre ese diseño, con su verdad. La plantilla v1 de este núcleo se reconstruyó desde el TB1 y no coincide con ese diseño, por lo que se agregó una **plantilla v2** y un **perfil `fisico`**.

### Puesta en marcha

```bash
make fisico-data      # descarga solo Formatos_fisicos, HU06_plantilla (sin node_modules) y el generador → data/formatos_fisicos/repo
make fisico           # plantillas reales, códigos, verdad, ingesta, reconocimiento y evaluación contra la verdad (≈ 4 min con MPS)
make dev-fisico       # API del perfil fisico (con su verdad) y web en http://localhost:3100
```

Cada paso de `make fisico` hace lo siguiente:

1. Importa el maestro de códigos tomado de la leyenda impresa en el formato (`configs/masters_fisico/codigos.csv`).
2. Convierte la verdad de HU06 al CSV de referencia por campo (`perfo truth import-boxes`). Cada texto se asigna al campo que cubre la mayor parte de su caja y los textos tachados no cuentan como valor.
3. Ingesta las 52 imágenes.
4. Importa el modelo ajustado de la última corrida e2e (`perfo registry import`), sin su calibración.
5. Procesa con ese modelo (`perfo process --model latest`).
6. Compara la línea base y el modelo ajustado sobre todos los formatos con verdad (`perfo evaluate reference`).

### Plantilla v2

`perfo template import-hu06` genera `configs/template_v2.json` y su referencia a partir de la plantilla de HU06:

- **Coordenadas y campos.** Conserva las coordenadas de HU06 y los 37 campos priorizados en HU01 (289 instancias con las tablas expandidas).
- **Identificadores.** Cada campo usa los identificadores de este núcleo (`header.date`, `activities.r03.length_m`, …), así que las reglas, el tipado de horas y la validación funcionan sin cambios. También guarda el ID de la matriz (`matrix_id`, F01–F44) y el de HU06 (`hu06_id`) para la trazabilidad.
- **Anclas de alineación.** Son las 32 esquinas exteriores de los marcos impresos, que el importador detecta solo. En una búsqueda amplia, los cruces internos de la grilla, que se repiten cada fila, confunden la alineación.
- **Campos sin maestro.** Operador, labor, estado y ubicación quedan como texto libre hasta contar con los maestros reales (personal, labores). El código de equipo y la guardia quedan como códigos sin lista cerrada.

Ajustes del perfil `fisico` para fotos:

- **Alineación.** Admite una escala de foto a página mayor que la de un escaneo, ORB a mayor resolución, refinamiento por anclas en tres pasadas (amplia, media y fina) y una corrección local de la curvatura de la hoja con un campo de desplazamientos entre las anclas. Hasta un 10 % de las anclas puede quedar tapado o fuera del encuadre.
- **Segmentación.** En la página plana las filas miden 25 px, la mitad que en v1, así que el margen interior baja a 3 px y la tinta mínima por componente a 4 px; con 12 px se borraba el punto decimal. Además, la marca de repetición debe ser un trazo delgado que atraviese la celda; antes, 373 dígitos de una cifra se tomaban por marcas.

### Resultados sobre las 50 fotos generadas

| Modelo | CER | Exactitud literal | Exactitud por valor |
|---|---|---|---|
| TrOCR base sin ajuste | 0,501 | 24,1 % | 27,5 % |
| Ajustado con los datos sintéticos v1 (`ft-20261017021128`) | 0,194 | 61,1 % | 69,1 % |

- **Alcance.** La evaluación cubre 5 906 campos. Las 50 fotos se alinearon y ninguna celda con texto quedó como vacía.
- **Mejora del ajuste.** Reduce la CER un 61 % aunque entrenó con otras fuentes y otro diseño.
- **Por tipo de campo.** La CER del modelo ajustado es 0,09 en códigos, 0,13 en horas, 0,18 en números, 0,14 en fechas y 0,31 en texto libre.
- **Sesgo.** Los parámetros de segmentación se eligieron con estas mismas 50 fotos, entre 7 variantes, así que la cifra es algo optimista. Hay que confirmarla con formatos nuevos.

### Limitaciones del formato físico

- **Fotos reales.** La plantilla v2 redibuja el formato y no coincide con el papel, así que hay dos plantillas más, medidas sobre las fotos: taladros largos (`ejemplo_03`) y Jackleg (`ejemplo_02`, = `Formato_fisico_01`). Del Jackleg no se lee la malla de perforación, porque es un dibujo. `make fisico-templates` las genera: las definiciones van a `configs/templates_real/` y las referencias planas a `data/plantillas_reales/`, fuera de git porque salen de fotos reales. El sistema prueba primero v2 y, si no reconoce la hoja, usa la alternativa con más correspondencias.
- **Una sola foto por plantilla.** Cada plantilla se midió sobre la misma foto con la que se probó. No hay otra foto ni verdad, así que falta medir qué tan bien funcionan con hojas nuevas y qué tan bien leen. A 110 ppp todos los campos van a revisión. Para mejorarlas hace falta un escaneo a 300 ppp de cada formato en blanco.
- **Turno.** Es una casilla (Día/Noche/C. Guardia) y la verdad de HU06 no la incluye. TrOCR lee las etiquetas impresas, así que el turno siempre va a revisión. Mientras tanto, las horas de 12 h sin sufijo quedan ambiguas. Falta un detector de marca.
- **Sin calibración.** No hay calibración para este formato, así que todos los campos van a revisión. Para aceptar automáticamente hay que calibrar con una partición de estas fotos (o de formatos reales).
- **Datos generados.** En el 13 % de las filas (78 de 617), el % de desviación no cumple la fórmula impresa en el formato; las reglas lo marcan, como pide el TB1.

## Control de resolución

La resolución se mide en ppp equivalentes: el lado mayor de la imagen frente al de la página de referencia, a la resolución de la plantilla. Hay dos niveles:

- **Mínimo aceptado** (`preprocessing.min_dpi`, 80 ppp por defecto). Por debajo, la imagen se rechaza en el control de calidad. El mensaje indica el tamaño de la imagen y cuántos píxeles harían falta.
- **Resolución recomendada** (la de la plantilla: 200 ppp, según el TB1). Entre ambos valores el documento se procesa con un aviso de calidad, visible en la web y en la causa del estado. Además, todos sus campos van a revisión con el motivo «Imagen bajo la resolución recomendada», porque la calibración de la confianza se midió a la resolución recomendada y no garantiza el riesgo por debajo de ella. Esto se desactiva con `routing.low_resolution_forces_review: false`.

Medido sobre `gen_001`: al reducir la foto de 2560 a 1280 px (110 ppp), la CER del modelo ajustado sube de 0,229 a 0,351 y las celdas exactas bajan del 51 % al 30 %.

El escaneo defectuoso de baja resolución del corpus sintético se genera a 75 ppp, para que el e2e siga probando el rechazo.

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
  adapters/http/ API FastAPI para la interfaz web (adaptador de entrada, sin reglas de negocio)
  adapters/hu06/ importación de la plantilla y de la verdad por cajas del formato físico (HU06)
  cli.py · e2e.py · bootstrap.py (raíz de composición)
web/             interfaz Next.js: app/ (páginas), components/, lib/ (cliente de la API y tipos)
configs/         default.yaml · thresholds.yaml · abbreviations.yaml · profiles/{smoke,full,fisico}.yaml
                 template_v1.json · template_v1_reference.png · field_priority.csv (matriz, IE1)
                 template_v2.json · template_v2_reference.png · masters_fisico/codigos.csv (formato físico, HU06)
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
- **Interfaz web.** Es para uso local: no tiene autenticación ni roles, los trabajos de reentrenamiento viven en la memoria del proceso de la API y se atiende un modelo a la vez.
- **PDF.** La ingesta acepta PDF y lo registra, pero el preprocesamiento solo decodifica imágenes (JPG/PNG). Un PDF queda en `ERROR` con su causa hasta que se agregue un rasterizador, por ejemplo `pypdfium2`.
