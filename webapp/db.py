from contextlib import contextmanager

from sqlalchemy import create_engine, text

from webapp.config import DATABASE_URL, TENANT_SLUG

engine = create_engine(DATABASE_URL)


@contextmanager
def tenant_connection():
    """Abre una conexion, activa app.tenant_session() para el tenant fijo de
    esta pantalla (regla #1: ninguna consulta de negocio se hace sin esto),
    y hace commit al salir si no hubo errores."""
    with engine.begin() as conn:
        tenant_id = conn.execute(
            text("SELECT id FROM app.tenants WHERE slug = :slug"), {"slug": TENANT_SLUG}
        ).scalar_one_or_none()
        if tenant_id is None:
            raise RuntimeError(
                f"No existe el tenant '{TENANT_SLUG}'. Corre 'make seed' antes de usar la pantalla."
            )
        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})
        yield conn, tenant_id
