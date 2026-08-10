PYTHON := .venv/Scripts/python.exe

.PHONY: venv check-db migrate seed test test-all lint db-reset enable-vector run

# Crea el entorno virtual e instala las dependencias del proyecto.
venv:
	python -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install ".[dev]"

# Verifica que se puede conectar a Postgres y que los esquemas ya existen.
check-db:
	$(PYTHON) scripts/check_db.py

# Aplica todas las migraciones pendientes.
migrate:
	$(PYTHON) -m alembic upgrade head

# Crea el tenant/cliente/gestor de prueba para poder usar la pantalla de carga.
seed:
	$(PYTHON) scripts/seed.py

# Levanta el servidor de desarrollo en http://127.0.0.1:8000
run:
	$(PYTHON) -m uvicorn webapp.main:app --reload

# Pruebas que no requieren base de datos. Por ahora todas las pruebas
# necesitan Postgres, asi que este target queda igual que test-all.
test:
	$(PYTHON) -m pytest

# Todas las pruebas, incluidas las que necesitan Postgres real.
test-all:
	$(PYTHON) -m pytest

# Formato, tipos y contratos de arquitectura. Llega mas adelante.
lint:
	@echo "Aun no configurado (llega mas adelante)."

# Destruye y recrea el esquema completo. Borra datos.
db-reset:
	$(PYTHON) -m alembic downgrade base
	$(PYTHON) -m alembic upgrade head

# Habilita pgvector si lo instalas. El v1 no usa embeddings todavia.
enable-vector:
	@echo "pgvector es opcional y el Bloque A (v1) todavia no lo necesita."
