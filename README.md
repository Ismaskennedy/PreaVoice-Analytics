# PREA Voice Analytics

Plataforma de análisis de llamadas para contact centers de cobranza en
México. Ver [CLAUDE.md](CLAUDE.md) para el contexto completo del proyecto,
alcance del v1 y reglas del código.

## Estado

**Bloque A (cimientos) completo**: los tres esquemas de base de datos
(`app`, `ops`, `audit`), la función `app.tenant_session()` que activa el
aislamiento por cliente, las tablas de control de acceso (`tenants`,
`users`, `roles`, `permissions`, `role_permissions`, `user_roles`), las de
llamadas (`clients`, `agent_assignments`, `calls`, `recordings`,
`transcripts`, `transcript_segments`), las de taxonomías y checklist
(`taxonomy_categories`, `taxonomy_values`, `checklists`, `checklist_items`),
las de análisis de IA con evidencia obligatoria (`call_analyses`,
`call_analysis_findings`), las de evaluación humana (`call_evaluations`,
`call_evaluation_corrections`), la bitácora de auditoría
(`audit.activity_log`) y la cola de trabajos en segundo plano
(`ops.processing_jobs`). `make seed` deja todo listo para probar: roles,
permisos, un checklist real con 7 ítems.

Aparte del esquema, ya existe el **camino completo del v1**: login → subir
un archivo → transcribir (Whisper) → analizar con IA (GPT-4o-mini,
incluidas emociones y calidad de audio) → calificar → ver → confirmar
(`webapp/`), más un dashboard con métricas y gráficas, y una pantalla de
alta de usuarios para el admin. Ver `make run` abajo. La carga procesa
síncrono (sin cola en segundo plano) — es una simplificación a propósito,
ver `CLAUDE.md`.

Usuarios de prueba tras correr `make seed` (contraseña `demo1234` los tres):
`agente-demo@example.com`, `calidad-demo@example.com`,
`admin-demo@example.com`.

Lo que sigue: carga por CSV con descarga de URLs, desplegar en un servidor,
y conectar `ops.processing_jobs` para procesar en segundo plano en vez de
síncrono.

## Requisitos

- Python 3.12+
- PostgreSQL corriendo (local o en la nube)
- `make` (en Windows: `choco install make`)
- Una API key de OpenAI (para transcribir y analizar llamadas)

## Levantar el entorno

```
make venv                      # crea .venv e instala dependencias
cp .env.example .env           # y llena DATABASE_URL, OPENAI_API_KEY y SECRET_KEY con tus valores reales
make check-db                  # confirma que te puedes conectar a Postgres
make migrate                   # aplica las migraciones
make seed                      # crea el tenant/cliente/checklist/usuarios de prueba
make test-all                  # corre las pruebas (necesitan Postgres, no gastan OpenAI)
make run                       # levanta la pantalla en http://127.0.0.1:8000/
```

## Comandos

Ver la tabla completa en [CLAUDE.md](CLAUDE.md#comandos). Los que ya
funcionan hoy: `check-db`, `migrate`, `seed`, `run`, `test`, `test-all`,
`db-reset`. `lint` y `enable-vector` son placeholders hasta que existan
reglas de lint o pgvector.
