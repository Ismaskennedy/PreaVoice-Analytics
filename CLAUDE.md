# Contexto del proyecto para Claude

> Lee este archivo completo al inicio de cada sesión, antes de tocar nada.

---

## Qué es esto

**PREA Voice Analytics**: plataforma de análisis de llamadas para contact
centers de **cobranza en México**. Transcribe grabaciones, las analiza con IA,
las califica contra un checklist configurable y muestra los resultados.

El documento maestro de diseño (`PREA-Voice-Analytics-Diseno-Tecnico.md`)
**todavía no existe**. Se está construyendo de forma incremental a medida que
avanzamos, empezando por el esquema de base de datos del Bloque A. No asumas
que hay decisiones de diseño fuera de lo que está escrito aquí o en el código.

---

## Quién trabaja aquí y cómo

**Un solo desarrollador**, con formación de negocio y poco de ingeniería, que
construye este sistema con tu ayuda directa. Esto cambia cómo debes trabajar:

- **Explica antes de hacer.** Di en una o dos frases qué vas a hacer y por qué,
  en lenguaje llano. Luego hazlo.
- **Nada de jerga sin traducir.** Si escribes "índice parcial" o "constraint de
  exclusión", explica en la misma línea qué significa y para qué sirve.
- **Un cambio a la vez.** No refactorices tres módulos en una respuesta. Es
  imposible de revisar para alguien que está aprendiendo.
- **Corre las pruebas siempre** al terminar un cambio, sin que te lo pidan, y
  reporta el resultado.
- **Cuando algo falle, di qué falló y por qué**, no solo "lo arreglé". El
  objetivo es que la persona aprenda el sistema que está construyendo.
- **Si una petición lleva por mal camino, dilo.** Es preferible discutir una
  decisión ahora que desarmarla en tres semanas.

---

## Alcance actual (v1) — lee esto antes de proponer nada

Se construye una **rebanada vertical**: un camino completo que atraviesa todo
el sistema, feo pero funcional, antes de ensanchar nada.

```
subir un WAV a mano → transcribir → analizar con IA → calificar → verlo en pantalla
```

### Dentro del v1
- Carga de grabaciones por **dos caminos**, ambos manuales (sin Vicidial):
  1. Arrastrar un archivo a la vez.
  2. Subir un CSV por lotes (URL de descarga del .mp3, código del gestor,
     fecha/hora de la llamada) y que el sistema descargue cada grabación.
- Transcripción por **API** (Deepgram o Whisper), no autohospedada
- Análisis con LLM y salida JSON validada
- Un checklist, un cliente
- Una pantalla de detalle de llamada: audio + transcripción sincronizada +
  análisis + calificación
- Un listado de llamadas con filtros básicos

### Fuera del v1 (se construye después, si acaso)
| Componente | Por qué se pospone |
|---|---|
| Ingesta automática de Vicidial | Depende de accesos que aún no existen |
| GPUs propias / Kubernetes | Solo se justifica arriba de ~5,000 llamadas/día |
| ClickHouse, OpenSearch | PostgreSQL aguanta el primer año |
| Buscador en lenguaje natural | Espectacular en demo, prescindible en operación |
| Coach IA, alertas, reportes programados | Cada uno es un módulo completo |
| Multi-tenant activo (más de un cliente real) | La tabla existe por la regla #1, pero solo se usa un tenant en v1 |
| Particionado de tablas, pgvector/embeddings | Prematuro al volumen del v1 |

**No propongas nada de la columna derecha** salvo que la persona lo pida
explícitamente. El mayor riesgo de este proyecto es la ambición.

---

## Estado actual del código

**Bloque A — Cimientos: completo (Fases 1-6) y validado contra PostgreSQL
real.**

Lo que existe hoy:

- Esqueleto del proyecto: `pyproject.toml`, Alembic configurado
  (`alembic.ini`, `alembic/env.py`), `Makefile`, `scripts/check_db.py`
- Migración `0001_schemas_and_rbac`: esquemas `app`, `ops`, `audit`; función
  `app.tenant_session(tenant_id uuid)`; RBAC: `app.tenants`, `app.users`,
  `app.roles`, `app.permissions`, `app.role_permissions`, `app.user_roles`,
  con `FORCE ROW LEVEL SECURITY` en `users` y `user_roles`
- Migración `0002_calls_and_transcripts`: `app.clients` (la cartera),
  `app.agent_assignments` (a qué cartera está asignado cada gestor *ahora*),
  `app.resolve_assignment()` + trigger que congela `calls.client_id` al
  insertar (regla #2), `app.calls`, `app.recordings` (con `source_url` para
  la carga por CSV y `storage_path` para donde queda el archivo ya
  descargado/guardado), `app.transcripts` + `app.transcript_segments`.
  `app.users` ganó `external_code` para cruzar el CSV de carga por lotes
  (el "usuario" del CSV es un código interno, no un correo). Todas las
  tablas con `tenant_id` llevan `FORCE ROW LEVEL SECURITY`.
- Migración `0003_taxonomies_and_checklist`: `app.taxonomy_categories` +
  `app.taxonomy_values` (con `is_legal_risk`, para las banderas de riesgo
  legal del dominio), y `app.checklists` + `app.checklist_items`, que arman
  un checklist seleccionando valores de taxonomía. Un índice parcial (solo
  cubre las filas activas) exige un único checklist activo por cliente sin
  bloquear que existan versiones viejas desactivadas.
- Migración `0004_call_analysis`: `app.call_analyses` (resultado crudo del
  LLM, con `state` `CURRENT`/`SUPERSEDED` igual que `transcripts`) y
  `app.call_analysis_findings` (un renglón por ítem de checklist evaluado).
  La regla #4 quedó escrita como restricción de base de datos, no solo como
  convención: un `CHECK` impide que `is_met` tenga un valor a menos que
  también existan `evidence_quote`, `evidence_start_ms` y `evidence_end_ms`
  — Postgres rechaza la fila si la IA "afirma sin citar".
- Migración `0005_call_evaluations`: `app.call_evaluations` (la
  calificación: `draft` hasta que Calidad la confirma, mismo patrón
  `CURRENT`/`SUPERSEDED` que transcripts/analyses) y
  `app.call_evaluation_corrections` (lo que un humano corrige de un
  hallazgo de la IA). Dos reglas más quedaron como restricciones reales:
  un `CHECK` exige que una evaluación `confirmed` tenga quién y cuándo la
  confirmó, y una en `draft` no tenga ninguno de los dos (regla #7); y un
  *trigger* en `call_evaluation_corrections` rechaza cualquier `UPDATE` o
  `DELETE` — append-only de verdad, no solo la promesa de no tocarlo
  (regla #3).
- Migración `0006_audit_and_jobs`: `audit.activity_log` (bitácora, aislada
  por tenant y append-only con el mismo *trigger* que las correcciones) y
  `ops.processing_jobs` (cola simple para transcribir/analizar, consumida
  por *polling* — sin Redis ni RabbitMQ). **`ops.processing_jobs` es la
  primera tabla del proyecto sin RLS**, a propósito: un proceso que reparte
  trabajos pendientes necesita ver los de todos los clientes a la vez para
  decidir cuál sigue; cuando ese proceso toque los datos reales de una
  llamada, ahí sí tiene que llamar a `tenant_session()` como todo lo demás.
- `scripts/seed.py` completo: 3 roles (`agente`, `calidad`, `admin`), 6
  permisos y su relación con los roles, un gestor y un revisor de Calidad de
  prueba, una taxonomía real de cobranza (4 prácticas prohibidas con
  `is_legal_risk = true`, 3 elementos esperados de la llamada), y un
  checklist que usa esos 7 valores. Idempotente — correrlo varias veces no
  duplica nada.
- Pruebas, las 24 pasando contra Postgres real (`tests/test_migrations.py`,
  `tests/test_calls.py`, `tests/test_taxonomy.py`, `tests/test_analysis.py`,
  `tests/test_evaluations.py`, `tests/test_audit_and_jobs.py`,
  `tests/test_webapp.py`): ciclo upgrade/downgrade, aislamiento por tenant,
  `resolve_assignment()` congela el cliente correcto, una llamada vieja no
  se mueve de cartera cuando reasignan al gestor, la bandera
  `is_legal_risk`, un solo checklist activo por cliente, un hallazgo sin
  evidencia no puede afirmar nada, un solo análisis/evaluación vigente por
  llamada, una evaluación confirmada necesita revisor y fecha, que una
  corrección o una entrada de auditoría no se pueden editar ni borrar, y que
  `ops.processing_jobs` sí se ve entre tenants (la excepción a propósito).
- Lección aprendida (Fase 1) y ya reflejada en las pruebas:
  `FORCE ROW LEVEL SECURITY` bloquea inserciones **hasta del dueño de la
  tabla** si no se llamó antes a `tenant_session()`. Cualquier script (seed,
  migraciones de datos, etc.) que escriba en tablas con `tenant_id` debe
  llamar `tenant_session()` primero, tenant por tenant — no hay atajo de
  "modo administrador" implícito.
- Entorno local de desarrollo funcionando: PostgreSQL 18 vía Chocolatey,
  base `aim8_dev` y rol `aim8_dev` (dueño de la base), credenciales en
  `.env` (no versionado). `make venv/check-db/migrate/test-all/db-reset`
  verificados de punta a punta.

**El Bloque A ya está completo.** Lo único que queda pendiente de la lista
original es `make lint` (formato, tipos, contratos de arquitectura), que no
es parte del esquema — se hace cuando haya más código Python que justifique
la herramienta.

**Contexto de negocio (2026-08-08): hay demo el lunes 2026-08-10.** Eso
disparó construir de golpe el resto de la rebanada vertical (transcripción +
análisis con IA + calificación + pantalla de detalle), saltándonos el orden
"un cambio a la vez" habitual porque no había margen. Ver más abajo.

**Sesión del 2026-08-09: login, dashboard y diseño — completos.** El hueco
del usuario admin (anotado ayer) ya está resuelto: existe login real y una
pantalla de alta de usuarios.

- **Login** (`webapp/auth.py`): contraseñas con `hashlib.pbkdf2_hmac`
  (260,000 iteraciones, librería estándar, sin dependencia nueva para el
  hash en sí) + sesión por cookie firmada (`SessionMiddleware` de Starlette,
  requiere `itsdangerous` — sí es una dependencia nueva, y `SECRET_KEY` en
  `.env`). `GET/POST /login`, `POST /logout`. Todas las rutas antes
  abiertas ahora exigen sesión (`require_login`); `/users` exige además rol
  `admin` (`require_admin`).
- **Sigue habiendo un solo tenant fijo** (`TENANT_SLUG`): el login resuelve
  "quién eres", no "de qué organización eres" — eso seguirá pendiente
  mientras el v1 tenga un solo tenant real.
- `POST /calls/upload` ahora usa a quien inició sesión: si es `agente`, la
  llamada es suya; si es `calidad`/`admin` subiendo en nombre de alguien, se
  sigue usando el primer agente (todavía no hay selector de agente en
  pantalla). `POST /calls/{id}/confirm` ahora exige rol `calidad` o `admin`
  y usa a quien confirmó de verdad (antes era un `_reviewer_id` fijo).
- **Gestión de usuarios** (`/users`, `/users/new`, solo `admin`): listar y
  crear usuarios con rol, desde el navegador — ya no hace falta editar
  `scripts/seed.py` para agregar a alguien.
- **`scripts/seed.py`** ahora crea `admin-demo@example.com` además de
  `agente-demo`/`calidad-demo`, y los tres quedan con contraseña
  (`demo1234` por default, configurable con `SEED_PASSWORD`). El script
  importa `webapp.auth.hash_password` — como se corre con
  `python scripts/seed.py` (no `-m`), tuvo que agregarse la raíz del repo a
  `sys.path` a mano al principio del archivo, si no `import webapp` no se
  encuentra.
- **Dashboard** (`GET /`, antes era la lista de llamadas — la lista se
  movió a `GET /calls`): métricas (llamadas cargadas, calificación
  promedio, llamadas con riesgo legal) + dos gráficas con **Chart.js vía
  CDN** (`<script src="cdn.jsdelivr.net/npm/chart.js">`, sin build ni
  npm) — cumplimiento por ítem del checklist, y calificación por llamada.
  Excepción puntual a "sin JS": los datos se pasan al script con el filtro
  `tojson` de Jinja2 y Chart.js dibuja con `<canvas>`, nada de frameworks de
  frontend.
- **Pasada de diseño** en `base.html`: nav bar con marca/enlaces/usuario/
  salir, paleta de colores con variables CSS, tarjetas y tablas con más
  aire. Sigue siendo HTML servido desde el backend, cero JS de UI (aparte
  de Chart.js).
- **Renombrado a "PREA Voice Analytics"** (2026-08-09, más tarde el mismo
  día): "AIM8 Speech Analytics" era el nombre de trabajo, el real es este.
  Cambiado en todas las plantillas, el título de la app en FastAPI,
  `pyproject.toml` (nombre del paquete: `prea-voice-analytics`), `README.md`
  y aquí arriba. Logo ya colocado por el usuario en `webapp/static/logo.png`
  (montado como `/static`), con un chip blanco redondeado alrededor en el
  login para que se vea bien sobre fondo oscuro sin importar si el PNG
  tiene transparencia o no (no pude confirmarlo — es PNG de paleta, no
  RGBA simple).
- **Tema oscuro** (2026-08-09, pedido explícito porque el diseño claro "se
  veía feo"): paleta completa en `base.html` vía variables CSS
  (`--fondo`, `--tarjeta`, `--azul`, etc.). Gráficas de Chart.js en
  `dashboard.html` ajustadas a mano (`Chart.defaults.color`, colores de
  ejes/grid) porque los defaults de Chart.js asumen fondo claro y quedaban
  ilegibles sobre oscuro.
- **`/calls` rediseñada estilo WhatsApp**: dos paneles — a la izquierda la
  lista de gestores (con iniciales tipo avatar y conteo de llamadas), a la
  derecha las llamadas del gestor seleccionado. Filtros por estado y "solo
  riesgo legal" (formulario `GET`, se auto-envía con `onchange`, cero
  JavaScript propio). El primer gestor de la lista se selecciona solo al
  entrar (como abrir la primera conversación). La carga de archivo pasó a
  vivir dentro de un `<details>` colapsable arriba del panel derecho.
  **Esto es explícitamente temporal**: el usuario aclaró que esta pantalla
  todavía va a cambiar más (la lista de la izquierda podría no ser
  siempre gestores) — no asumir que esta estructura es definitiva.
- **Resumen de la llamada en vez de transcripción literal** (pedido
  explícito, mismo día): la pantalla de detalle ya no muestra la
  transcripción completa como lo primero que se ve — ahora hay un recuadro
  "Resumen de la llamada" con quién contestó (`who_answered`: titular/
  familiar/tercero/no_contesto, misma regla #4 que las emociones — necesita
  cita+tiempos o se anula) y una síntesis en texto libre de 2-4 líneas
  (`summary`, sin evidencia obligatoria: es una síntesis de toda la llamada,
  no una afirmación puntual). La transcripción completa se sigue guardando
  igual que siempre (es la evidencia detrás de cada cita del checklist/
  emociones) pero quedó detrás de un `<details>` colapsado ("Ver
  transcripción completa"). Migración `0011_call_summary`, columnas nuevas
  en `app.call_analyses`. Mismo prompt/llamada a GPT-4o-mini que checklist +
  emociones + coaching, para no sumar latencia.
- **Áreas de oportunidad para el asesor** (pedido explícito, mismo día):
  cada análisis ahora también genera sugerencias de coaching, en el mismo
  prompt/llamada a GPT-4o-mini que ya hacía checklist + emociones (no una
  llamada aparte, para no sumar latencia). Dos categorías:
  `mejora_llamada` (calidad general de la llamada) y `cierre_negociacion`
  (qué decir para cerrar el compromiso de pago). Tabla nueva
  `app.call_coaching_suggestions` (migración `0010`). A diferencia de los
  hallazgos del checklist, **estas sugerencias NO llevan la regla #4**
  (evidencia obligatoria) — son recomendaciones, no afirmaciones sobre lo
  que pasó en la llamada. Si el modelo sí da una cita de contexto, tiene
  que traer sus marcas de tiempo completas (`CHECK` de consistencia, más
  suave que el de los findings) o se descarta la cita — la sugerencia en sí
  se conserva de todos modos.
- Migración `0009_password_hash`: agrega `app.users.password_hash`
  (nullable — un usuario puede existir sin poder loguearse).
- Pruebas: 56 pasando (46 tras login/dashboard/diseño, +5 de las sugerencias
  de coaching, +5 del resumen/who_answered: se conservan sin cita, se
  descarta la cita si falta un timestamp, categoría/etiqueta fuera de lista
  permitida se rechaza, el resumen en texto libre no exige evidencia).
  `tests/test_auth.py` es nuevo (login correcto/
  incorrecto, rutas protegidas redirigen, `/users` bloqueado para no-admin,
  admin puede crear usuario y ese usuario puede loguearse). Tuve que
  arreglar `test_upgrade_downgrade_cycle`: comprobaba una columna
  específica de la migración 0008, que dejó de ser la última en cuanto
  llegó la 0009 — ahora solo compara el puntero de `alembic_version` antes/
  después, sin acoplarse a cuál migración es la más reciente.
- **Sigue pendiente** (no se tocó hoy): desplegar en un servidor (decisión
  de infraestructura, se aborda después de que la demo esté lista aquí en
  local), selector de agente en la pantalla de carga cuando sube alguien
  que no es agente, "olvidé mi contraseña"/recuperación (no se construyó,
  no se pidió).

**Pantalla web (`webapp/`, fuera del Bloque A): el camino completo ya
funciona.** Subir un archivo → transcribir (Whisper) → analizar con IA
(GPT-4o-mini) → calificar → ver, con evidencia y confirmación humana.

- `GET /` — lista de llamadas, con calificación si ya se analizaron.
- `POST /calls/upload` — guarda el archivo en `RECORDINGS_DIR` (disco
  local), inserta `app.calls`/`app.recordings`, y **de inmediato, en la
  misma petición HTTP** (síncrono, el usuario espera unos segundos)
  transcribe y analiza. Ver decisión de diseño abajo.
- `GET /calls/{id}` — pantalla de detalle: reproductor de audio,
  transcripción con marcas de tiempo, checklist con cada hallazgo (cumplido/
  no cumplido/sin evidencia, con la cita textual si la hay), calificación, y
  un botón "Confirmar evaluación" (regla #7: la IA propone en borrador,
  Calidad confirma).
- `POST /calls/{id}/confirm` — marca la evaluación vigente como `confirmed`,
  con el revisor de Calidad sembrado (`calidad-demo`) y la fecha.
- `webapp/services/transcription.py` — llama a Whisper (`response_format=
  "verbose_json"`, `timestamp_granularities=["segment"]`), devuelve
  segmentos con `start_ms`/`end_ms`. Whisper no distingue quién habla
  (`speaker` queda `None`).
- `webapp/services/analysis.py` — arma el prompt con el checklist real
  (traído de `app.checklist_items`/`app.taxonomy_values` de la base) y la
  transcripción, llama a GPT-4o-mini con `temperature=0` y
  `response_format={"type":"json_object"}`. **La regla #4 se aplica dos
  veces**: la base de datos la exige con un `CHECK`, y este código también
  la fuerza (`is_met` se anula en Python si el modelo no trajo cita completa)
  — no confiamos en que el modelo obedezca el prompt. `compute_score()`
  calcula el porcentaje solo sobre ítems que NO son de riesgo legal; los de
  riesgo legal son una bandera aparte, no restan puntos del checklist.
- Requiere `OPENAI_API_KEY` en `.env` (no committeado). Sin ella, la carga
  de todos modos guarda el archivo pero el procesamiento falla con manejo
  de errores (`app.calls.status = 'failed'`), no tumba la petición.
- **Decisión de diseño, solo para la demo**: se saltó `ops.processing_jobs`
  a propósito — transcribir+analizar corre síncrono dentro de
  `POST /calls/upload`, no como cola en segundo plano. Es la simplificación
  correcta para dos días de plazo; con volumen real esto es exactamente lo
  que el worker de `ops.processing_jobs` debería repartir de forma
  asíncrona. Revisar esta decisión antes de ir a producción.
- Bug real que encontré y corregí en el camino: `agent_id` se buscaba con
  `SELECT id FROM app.users LIMIT 1`, que dejó de ser válido en cuanto
  `make seed` empezó a crear dos usuarios (`agente-demo` y `calidad-demo`) —
  sin `ORDER BY`, Postgres podía devolver cualquiera de los dos. Ahora se
  busca por rol (`JOIN app.user_roles`/`app.roles WHERE r.code = 'agente'`).
- Migración `0007_call_status_analyzed`: amplía `app.calls.status` con
  `analyzing`/`analyzed` (antes solo llegaba hasta `transcribed`).
- Nota de Starlette (la librería debajo de FastAPI): en esta versión
  `TemplateResponse` cambió de firma — ahora es
  `TemplateResponse(request, "nombre.html", contexto)`, con `request` como
  primer argumento posicional, no dentro del diccionario de contexto.
- Todavía **no** existe: carga por CSV con descarga de URLs, login real,
  gestión de usuarios. La subida de un archivo no es transaccional con el
  guardado en disco (si una escritura fallara después de guardar el
  archivo, quedaría un archivo huérfano) — aceptable para esta pantalla,
  revisar si se vuelve un problema real.
- **Riesgo pendiente, decisión consciente del 2026-08-08**: todavía no existe
  el enmascaramiento de datos personales (nombre, RFC, CURP, cuentas
  bancarias) antes de mandar audio/transcripción a OpenAI, aunque el
  contexto del dominio ya lo exige ("no lo omitas por conveniencia"). Para
  la demo interna del 2026-08-10 se subió una grabación real de una llamada
  de cobranza sin enmascarar, a propósito, sabiendo que los datos del
  cliente salen sin filtrar hacia un tercero. **No usar el sistema con
  llamadas reales de clientes fuera de esta demo puntual hasta construir el
  enmascaramiento.**
- **Probado en caliente contra la API real de OpenAI el 2026-08-08**: se
  generó un audio de prueba (voz sintética, texto de cobranza) con
  `client.audio.speech.create`, se subió por `POST /calls/upload` de
  verdad, y salió `status = 'analyzed'`, `score = 100`, con evidencia
  correcta para "informar el monto" y "ofrecer convenio", y `None` (sin
  evidencia inventada) en todos los ítems donde no correspondía —
  incluidos los 4 de riesgo legal. El botón de confirmar también se probó
  de punta a punta. Ojo: al imprimir texto con acentos en la terminal de
  Windows a veces se ve mal (`gustar�a`) — es un problema de la terminal,
  no de los datos guardados (UTF-8 en Postgres vía psycopg).
- **Migración `0008_emotions_and_audio_quality`** (agregada el 2026-08-08
  a petición tuya después de probar con una llamada real): `app.call_analyses`
  ganó `agent_emotion`/`debtor_emotion` (con cita+tiempos, mismo `CHECK` de
  evidencia que las findings del checklist — regla #4) y `audio_quality`
  (`buena`/`regular`/`mala`, calculado a partir de `avg_logprob` que Whisper
  ya calcula por segmento — **es una aproximación desde la confianza del
  transcriptor, no un análisis acústico real** de ruido/interferencia).
  Etiquetas de emoción permitidas (lista cerrada, ver `EMOTIONS` en
  `webapp/services/analysis.py` y la migración): tranquilo, empático,
  neutral, impaciente, frustrado, molesto, agresivo, ansioso, cooperativo.
- **La prueba `test_upgrade_downgrade_cycle` ya no hace `downgrade base`**:
  esta base de datos tiene datos reales de la demo (llamadas subidas de
  verdad) y un downgrade completo choca contra restricciones viejas que ya
  no coinciden con esos datos. Ahora solo prueba que la última migración es
  reversible (un paso atrás y adelante). Si se necesita probar el ciclo
  completo de migraciones otra vez, hace falta una base de datos aparte,
  no esta.
- Pruebas: 38 pasando. `tests/test_ai_services.py` prueba
  `transcription.py`/`analysis.py` con un cliente de OpenAI simulado (sin
  gastar crédito real ni necesitar la key) — incluye pruebas específicas de
  que una afirmación (checklist o emoción) sin evidencia se anula en código,
  y de que una etiqueta de emoción fuera de la lista permitida se rechaza.

---

## Entorno de esta persona

Trabaja en **Windows** y **no usa Docker** (ya batalló con WSL2). PostgreSQL
se instaló localmente vía Chocolatey (`choco install make postgresql`) porque
no había ninguna instancia disponible al empezar el proyecto — revisa si
sigue siendo local o si ya se movió a la nube antes de asumir la conexión.

- **No propongas soluciones que requieran Docker** salvo que te lo pida.
- **`pgvector` no está instalado** y no hace falta en el v1. La tabla
  `app.call_embeddings` (fuera de alcance por ahora) solo existiría si la
  extensión estuviera instalada.
- Redis y RabbitMQ **no** están instalados y no hacen falta en el v1. Para
  trabajo en segundo plano se usa una tabla de cola simple
  (`ops.processing_jobs`, todavía no creada) consumida por *polling* — es la
  solución más simple que resuelve el problema sin infraestructura extra.
- `make` se instaló vía Chocolatey. Si algún día no está disponible, los
  targets del Makefile son wrappers delgados sobre scripts de Python
  (`scripts/*.py`) que se pueden correr directo con `.venv/Scripts/python.exe`.

## Comandos

```bash
make venv          # crea .venv e instala dependencias
make check-db       # verifica conexión y qué esquemas ya existen
make migrate        # aplica las migraciones
make seed           # crea el tenant/cliente/gestor de prueba (idempotente)
make run            # levanta la pantalla web en http://127.0.0.1:8000/
make test-all        # todas las pruebas (necesitan Postgres real)
make test            # hoy es igual a test-all; se separará cuando haya pruebas sin DB
make lint             # placeholder: llega más adelante
make db-reset          # destruye y recrea todo (¡borra datos!)
make enable-vector      # placeholder: solo aplica si instalas pgvector
```

---

## Reglas del código que no se negocian

Estas decisiones ya se tomaron y tienen razones. Si crees que alguna está mal,
**dilo y discútelo**, pero no la cambies por tu cuenta.

1. **Toda consulta pasa por `tenant_session()`.** Nunca abras una sesión de base
   de datos por otro camino. Es lo que activa el aislamiento entre clientes.
   Implementada como `app.tenant_session(tenant_id uuid)`: sin llamarla, las
   políticas de RLS niegan todo por defecto (no hay tenant, no hay filas).

2. **`calls.client_id` es una instantánea inmutable.** Se congela en la carga
   con `app.resolve_assignment()`, vía un trigger `BEFORE INSERT` en
   `app.calls` que lo llena si llega vacío. Los reportes jamás hacen JOIN
   contra la asignación actual del agente (`app.agent_assignments`, que solo
   guarda la asignación *vigente*). Si un gestor cambia de cartera, sus
   llamadas viejas siguen perteneciendo a la cartera vieja — probado en
   `tests/test_calls.py`.

3. **Nada se borra.** Un reproceso crea un registro nuevo y marca el anterior
   como `SUPERSEDED` (así en `transcripts`, `call_analyses` y
   `call_evaluations`). Las correcciones humanas son append-only — en
   `app.call_evaluation_corrections` un *trigger* rechaza cualquier `UPDATE`
   o `DELETE`, no es solo una convención. El resultado original de la IA se
   conserva siempre.

4. **Toda afirmación de la IA lleva evidencia** con marcas de tiempo
   (`evidence_start_ms`, `evidence_end_ms`, `evidence_quote`). Si un campo no
   tiene evidencia, se anula. Es la principal defensa contra alucinaciones, y
   en cobranza una fecha o un monto inventado destruye la confianza en todo
   el sistema. Implementado como un `CHECK` en
   `app.call_analysis_findings`: `is_met` no puede tener valor sin evidencia
   completa — probado en `tests/test_analysis.py`.

5. **Las migraciones se escriben en SQL crudo**, no con el DSL de Alembic. RLS,
   particionado y restricciones de exclusión se autogeneran mal. Escapa los dos
   puntos en literales JSON (`'{"a"\:true}'`) o SQLAlchemy los tomará como
   parámetros.

6. **Las taxonomías son datos, no código.** Viven en `app.taxonomy_values`
   y se inyectan literalmente en el prompt. Para agregar un valor nuevo se
   inserta una fila, no se modifica una migración.

7. **La IA propone, el humano dispone.** Una evaluación solo es oficial cuando
   Calidad la confirma. Ninguna consecuencia laboral debe salir de un score
   automático sin revisión humana. Implementado como un `CHECK` en
   `app.call_evaluations`: `status = 'confirmed'` exige `confirmed_by` y
   `confirmed_at`; `status = 'draft'` exige que ambos estén vacíos — probado
   en `tests/test_evaluations.py`.

---

## Decisiones técnicas y su razón

| Decisión | Razón |
|---|---|
| PostgreSQL para todo en v1 | Un motor menos que operar; aguanta el primer año |
| Transcripción por API | A bajo volumen cuesta ~$130/mes y evita administrar GPUs |
| Modelo LLM económico con salida JSON estricta | El 88% de las llamadas de cobranza son estructuralmente simples |
| `temperature = 0` y esquema forzado | Reproducibilidad: dos ejecuciones iguales dan lo mismo |
| Monolito modular | Un solo desarrollador no puede operar microservicios |
| Español mexicano en la interfaz y en los prompts | Es el idioma de los usuarios y de las llamadas |
| Python + SQLAlchemy + Alembic + psycopg + pytest | Ya implícito en las reglas del código; probado limpio sobre Python 3.14 |
| `make` sobre Chocolatey, sin Docker/WSL | Decisión previa del desarrollador; Docker/WSL2 ya dieron problemas antes |
| FastAPI + plantillas Jinja2 (sin React/JS) para la web | Encaja con el stack Python ya elegido; cero Node.js/npm, coherente con "la herramienta más simple que resuelve el problema" |
| OpenAI (Whisper + GPT-4o-mini) como único proveedor de IA | Una sola cuenta/API key en vez de dos (transcripción + análisis por separado); menos fricción bajo presión de tiempo para la demo del 2026-08-10 |
| Procesar transcripción+análisis síncrono (sin `ops.processing_jobs`) | Simplificación temporal para la demo; la cola asíncrona ya existe en el esquema pero conectarla es más trabajo del que sobra en dos días |

---

## Contexto del dominio (importante para el análisis)

Cobranza en México. Vocabulario que aparece en las llamadas: SPEI, OXXO,
convenio, quita, reestructura, cartera vencida, promesa de pago, buró de
crédito, días de mora, titular, gestor.

Hay **prácticas prohibidas** que el sistema debe detectar porque exponen a la
empresa a sanciones: amenazas del gestor, revelar la deuda a un tercero,
lenguaje ofensivo, insistir tras una solicitud de no contactar. Se modelan
como banderas con `is_legal_risk = true` en `app.taxonomy_values`.

Las llamadas contienen datos personales sensibles (nombre, RFC, CURP, cuentas
bancarias, a veces datos de salud). El diseño contempla enmascararlos antes de
enviarlos al modelo de IA. **No lo omitas por conveniencia.**

---

## Módulo de clientes (`/clients`) — construido el 2026-08-09/10, para la demo

Pedido explícito y "muy importante" para el usuario: una pantalla donde
Calidad (o admin) da de alta un cliente (cartera), le asigna gestores, y le
"entrena la IA" escribiendo en sus propias palabras los criterios que debe
cumplir cada llamada de ese cliente — sin tocar código.

- `GET /clients` — lista de clientes con conteo de gestores y criterios.
- `GET /clients/new` + `POST /clients` — alta de cliente. Crea también,
  automáticamente, un checklist vacío y activo para ese cliente (así
  siempre hay dónde agregar criterios sin un paso extra).
- `GET /clients/{id}` — pantalla principal: gestores asignados + formulario
  para asignar uno nuevo (mueve al gestor si ya estaba en otro cliente —
  usa el mismo `ON CONFLICT (user_id)` de `app.agent_assignments` que ya
  existía); checklist actual + formulario para agregar un criterio nuevo en
  texto libre, con casilla de "es riesgo legal".
- `POST /clients/{id}/checklist-items`: el texto libre se convierte en una
  fila de `app.taxonomy_values` (con un `code` generado automáticamente —
  slug del texto + sufijo aleatorio, para garantizar que sea único sin
  pedirle al usuario que invente un código) y un `app.checklist_items` que
  lo liga al checklist activo del cliente. Reutiliza (o crea si hace falta)
  las categorías `riesgo_legal`/`elementos_llamada` que ya existían del seed
  — no hizo falta ninguna migración nueva, todas las tablas ya existían
  desde las Fases 2-3 del Bloque A.
- **No hizo falta tocar `webapp/services/analysis.py` para que esto
  "funcionara con la IA"**: el análisis ya leía el checklist activo del
  cliente de la base de datos en cada llamada (desde la Fase 2). En cuanto
  se agrega un criterio aquí, la siguiente llamada de ese cliente ya lo
  incluye.
- Acceso: `calidad` y `admin` (igual que `/reports`). De paso se corrigió
  un hueco real: `/reports` y `/reports/pulso-diario` solo ocultaban el
  enlace para roles sin permiso, pero no revisaban el rol dentro de la
  ruta — cualquier `agente` logueado podía entrar tecleando la URL. Se
  agregó `webapp.auth.require_role()` (genérico, reemplaza el chequeo
  suelto) y ya protege ambas rutas de verdad.
- **Probado en vivo de punta a punta** (no hay pruebas automáticas
  todavía, por la hora — pendiente si hay tiempo): se creó un cliente
  "Bradescard", se le asignó el gestor de prueba (que traía 24 llamadas
  viejas con "Cartera Demo"), se le agregaron dos criterios nuevos
  (uno normal, uno de riesgo legal), y se subió una llamada nueva. Resultado
  verificado en la base de datos: la llamada nueva quedó con
  `client_id` = Bradescard (regla #2), las 24 llamadas viejas se quedaron
  con "Cartera Demo" (no se movieron), el análisis solo evaluó los 2
  criterios de Bradescard (no los 7 de la cartera demo), y detectó
  correctamente `true` en "no debe presionar con lenguaje intimidante"
  porque la grabación de prueba era justo un asesor brusco — con evidencia
  real, no inventada. **El cliente "Bradescard" y esa llamada de prueba se
  quedaron en la base de datos** (no se borraron) — podrían servir como
  segundo ejemplo en la demo, o borrarse si no se quieren mostrar.

**Ampliación — "guion esperado de la llamada" (2026-08-10):** el checklist
puntual (sí/no por criterio) no alcanza para expresar "todo lo que debe
llevar la llamada" en general — solo sirve para prohibiciones o afirmaciones
puntuales. Se agregó un segundo campo por cliente, texto libre y sin
estructura, para eso:

- Migración `0012_client_call_script`: columna `app.clients.call_script`
  (nullable, sin `CHECK` — es una instrucción escrita por un humano, no una
  afirmación de la IA, así que no aplica la regla #4 de evidencia).
- `POST /clients/{id}/call-script` guarda el texto. `GET /clients/{id}`
  ahora muestra dos secciones separadas: "Guion esperado de la llamada"
  (el texto libre, editable) arriba, y "Checklist puntual" (los criterios
  de sí/no de siempre) abajo.
- `webapp/services/analysis.py`: `analyze()` recibe un cuarto argumento
  opcional `call_script`. Si el cliente tiene uno guardado, se inyecta como
  bloque de contexto al inicio del prompt del usuario ("Instrucciones
  generales del cliente..."), antes del checklist puntual. El `SYSTEM_PROMPT`
  aclara que ese texto es contexto para juzgar el checklist y el coaching,
  pero no genera findings nuevos por sí solo — los findings con evidencia
  obligatoria siguen saliendo únicamente del checklist puntual. Así el
  guion enriquece el análisis sin debilitar la regla #4.
- Se quitó el párrafo explicativo que estaba junto al checklist ("Esto es lo
  que la IA usa para analizar...") por pedido del usuario.

**Nota operativa (bug recurrente de `uvicorn --reload` en Windows):** el
proceso "reloader" (StatReload) a veces truena al recargar
(`make: *** [Makefile:25: run] Error -1`) pero no mata a su proceso hijo —
el hijo se queda huérfano, sigue contestando en el puerto 8000, pero con el
código viejo de antes del último cambio (síntoma: rutas nuevas devuelven 404
aunque el código ya las tenga). `Get-NetTCPConnection -LocalPort 8000` puede
mostrar el PID del reloader ya muerto (falso), no el del hijo huérfano real.
Diagnóstico: comparar `/openapi.json` del servidor vivo contra lo que
debería tener el código actual. Solución: buscar el proceso python real con
`Get-Process -Id <PID>` (probando varios, no solo el que reporta `netstat`)
y matarlo, luego `make run` de nuevo limpio.

---

## Módulo de reportería (PDF) — arrancado el 2026-08-09

Pedido explícito: reproducir un reporte "Pulso Diario Operativo" que el
usuario ya usa en otro software (captura de pantalla compartida en el
chat). Es un reporte operativo por agente: tiempo muerto en llamadas
(silencio largo sin colgar) y llamadas que cayeron en buzón de voz sin que
el agente colgara a tiempo, con los teléfonos exactos a revisar.

**Actualizado el mismo día: ya usa datos reales, no de muestra.** El
usuario pidió explícitamente quitar los datos inventados — si un día no
tiene llamadas, el reporte dice "No hay información para esta fecha" en
vez de mostrar números falsos.

- `GET /reports` — pantalla con filtro de fecha (`<input type="date">`,
  autoenvía con `onchange`, sin JS propio), solo para `calidad`/`admin`.
  Muestra métricas reales + lista de agentes, o el mensaje de "sin
  información" si no hay llamadas ese día.
- `GET /reports/pulso-diario?date=YYYY-MM-DD` — el mismo reporte en PDF,
  con el botón "Descargar PDF" desde la pantalla anterior.
- `webapp/services/reports.py::build_pulso_diario()` calcula el **tiempo
  muerto de verdad**: un hueco de más de 60 segundos entre el `end_ms` de
  un `transcript_segment` y el `start_ms` del siguiente cuenta como un
  evento. Sin IA nueva, sin datos inventados — solo lo que ya
  guardábamos. Probado en vivo: el 2026-08-09 salieron 15 llamadas reales
  y 0 eventos de tiempo muerto (las grabaciones de prueba eran cortas,
  sin silencios largos) — el reporte lo dice tal cual, no lo esconde ni
  lo rellena.
- **"Buzón de voz" quedó fuera del reporte a propósito** (ni como columna
  ni como número en cero) porque no lo detectamos todavía — se muestra un
  aviso de texto explicando que está pendiente, en vez de fingir un dato.
  Cuando se implemente (ver idea abajo: campo nuevo en el prompt de
  `analysis.py`, con evidencia como el resto), se agrega como columna real.
- Sigue faltando el **teléfono del deudor por llamada** — hoy `app.calls`
  no tiene ese campo, así que la lista de eventos por agente muestra el
  nombre del archivo de la grabación (con link a la pantalla de detalle)
  en vez de un número para pegar en el marcador. Se resuelve cuando exista
  la carga por CSV (trae el teléfono real).

**Decisión técnica: `xhtml2pdf`** para generar el PDF (HTML/CSS con Jinja2,
igual que el resto de la app, convertido a PDF). Se probó primero porque es
puro Python — sin GTK/Cairo como pediría `weasyprint`, sin dolores de
instalación en Windows. Limitaciones reales encontradas al construir la
plantilla (`webapp/templates/report_pulso_diario.html`), por si se toca de
nuevo:
- **No soporta emojis** (🎧📞📮) — la fuente por default (Helvetica) no
  tiene esos glifos; salen como cuadros negros. Se quitaron del diseño.
- **El color de fondo de un `<div>` no envuelve bien contenido anidado
  complejo** (una tabla dentro de un div con `background-color` — el fondo
  se "cortaba" a la mitad, dejando texto claro sobre blanco, casi
  invisible). La solución fue hacer toda la tarjeta una sola `<table>`, con
  el color puesto en cada `<td>` directamente, nunca en un `<div>` que
  envuelve otra tabla.
- No soporta flexbox/grid — todo el layout usa `<table>`, como en HTML de
  correos electrónicos viejos.
- `date.today().strftime("%A %d de %B de %Y")` sale en **inglés** (usa el
  idioma del sistema operativo). Se reemplazó por
  `webapp/services/reports.py::format_date_es()`, una tabla fija de
  días/meses en español, para no depender de la configuración regional de
  Windows.

---

## Siguiente fase (después de la demo, no antes): carga por CSV

El usuario compartió un CSV real de ejemplo (`grabaciones-07082026.csv`,
exportado de su sistema de telefonía) el 2026-08-09. Formato real:

```
start_time,user,location
07/08/2026,6465,http://192.168.1.235/RECORDINGS/MP3/20260807-082813_6317528127621645-all.mp3
```

- `start_time` → `app.calls.occurred_at` (ya existe, columna pensada para esto).
- `user` → un código numérico interno del asesor (ej. `6465`), **no** su
  nombre. Se cruza contra `app.users.external_code` (ya existe, columna
  agregada en la Fase 2 justo para esto). Si no hay ningún usuario con ese
  `external_code`, esa fila no se puede procesar — decidir entonces si se
  omite, se reporta como error, o se crea un usuario placeholder (no
  decidido todavía, preguntar).
- `location` → URL de descarga directa del `.mp3`. Hay que descargarlo
  (ya quedó instalado `requests` como dependencia transitiva de
  `xhtml2pdf` — no hace falta agregar nada nuevo) y guardarlo en
  `RECORDINGS_DIR` igual que un archivo subido a mano;
  `app.recordings.source_url` (ya existe) guarda esta URL original.
- **El teléfono del deudor viene escondido en el propio nombre del
  archivo** (confirmado por el usuario el 2026-08-09, con ejemplos reales):
  son los **últimos 10 dígitos** del número que precede a `-all` en el
  nombre. Ej. `20260807-082813_6317528127621645-all.mp3` → `8127621645`;
  `20260807-133043_522219674703-all.mp3` → `2219674703`. Extraer con algo
  como `re.search(r'(\d+)-all\.\w+$', filename).group(1)[-10:]`. Esto
  resuelve el pendiente de "teléfono real por llamada" — falta agregar la
  columna (`app.calls.phone_number` o similar, migración nueva) y usarla en
  vez del nombre de archivo en `/reports`.
- Algunas filas del CSV de ejemplo vienen con `location` vacío (sin URL) —
  hay que decidir qué hacer con esas (¿se ignoran? ¿se guardan como
  "pendiente"?).

**Decisión ya tomada con el usuario**: los ~250 registros de un CSV típico
no se procesan (transcribir+analizar) de un jalón en la misma petición HTTP
— tardaría demasiado. Esto se construye usando `ops.processing_jobs` (ya
existe en el esquema, sin usar todavía) con un worker de verdad en segundo
plano, no el atajo síncrono que usa `POST /calls/upload` hoy. No empezar
esta fase sin acordar el diseño del worker primero.

**El usuario va a dar de alta el 2026-08-10 (o después) a los agentes reales
de este CSV** (`6465`, `6532`, `6537`, `6501` en el ejemplo) con nombre real
y su `external_code`, usando `/users/new` — no lo hagas tú a menos que te lo
pida explícitamente.

---

## Cuando arranques una sesión

1. Lee este archivo y el `README.md`.
2. Verifica el estado: `make test-all`.
3. Pregunta en qué vamos a trabajar si no está claro, en lugar de suponerlo.
4. Al terminar un bloque de trabajo, **actualiza este archivo** con lo que
   cambió, para que la siguiente sesión no empiece a ciegas.
