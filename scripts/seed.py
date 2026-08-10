"""make seed: crea los datos minimos para poder usar la pantalla de carga
manual y tener un checklist real con el que probar el analisis: tenant,
cliente, gestor y revisor de Calidad de prueba, roles/permisos base, una
taxonomia real de cobranza (riesgo legal + elementos esperados de la
llamada), y un checklist que la usa. Se puede correr varias veces sin
duplicar nada (usa upsert por la columna unica de cada tabla)."""
import os
import sys
from pathlib import Path

# Permite "python scripts/seed.py" directo (sys.path[0] queda en scripts/,
# no en la raiz del repo) e importar webapp.auth de todos modos, para no
# duplicar la logica de hash de contrasena que tambien usa el login.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from webapp.auth import hash_password

TENANT_SLUG = os.environ.get("TENANT_SLUG", "demo")
DEMO_PASSWORD = os.environ.get("SEED_PASSWORD", "demo1234")

ROLES = [
    {"code": "agente", "name": "Agente", "description": "Hace las llamadas de cobranza"},
    {"code": "calidad", "name": "Calidad", "description": "Revisa y confirma evaluaciones"},
    {"code": "admin", "name": "Administrador", "description": "Administra usuarios, roles y catalogos"},
]

PERMISSIONS = [
    {"code": "calls.view", "description": "Ver llamadas y su detalle"},
    {"code": "calls.upload", "description": "Subir grabaciones"},
    {"code": "evaluations.confirm", "description": "Confirmar una evaluacion como oficial"},
    {"code": "evaluations.correct", "description": "Corregir un hallazgo de la IA"},
    {"code": "taxonomy.manage", "description": "Administrar taxonomias y checklist"},
    {"code": "users.manage", "description": "Administrar usuarios y roles"},
]

ROLE_PERMISSIONS = {
    "agente": ["calls.view", "calls.upload"],
    "calidad": ["calls.view", "evaluations.confirm", "evaluations.correct"],
    "admin": [p["code"] for p in PERMISSIONS],
}

# Datos, no codigo (regla #6): esta es la taxonomia inicial, no la unica
# posible. Para agregar un valor nuevo se corre este script de nuevo con la
# lista ampliada, no se escribe una migracion.
TAXONOMY = [
    {
        "category_code": "riesgo_legal",
        "category_name": "Practicas prohibidas (riesgo legal)",
        "values": [
            {"code": "amenaza", "label": "No se amenazo al titular", "is_legal_risk": True},
            {"code": "revelar_deuda_tercero", "label": "No se revelo la deuda a un tercero", "is_legal_risk": True},
            {"code": "lenguaje_ofensivo", "label": "No se uso lenguaje ofensivo", "is_legal_risk": True},
            {"code": "insistir_no_contacto", "label": "No se insistio tras solicitud de no contacto", "is_legal_risk": True},
        ],
    },
    {
        "category_code": "elementos_llamada",
        "category_name": "Elementos esperados de la llamada",
        "values": [
            {"code": "confirmar_identidad", "label": "Se confirmo la identidad del titular", "is_legal_risk": False},
            {"code": "informar_monto", "label": "Se informo el monto total de la deuda", "is_legal_risk": False},
            {"code": "ofrecer_convenio", "label": "Se ofrecieron opciones de convenio o reestructura", "is_legal_risk": False},
        ],
    },
]


def _upsert_user(conn, tenant_id, email, full_name, external_code, password=None):
    return conn.execute(
        text(
            "INSERT INTO app.users (tenant_id, email, full_name, external_code, password_hash) "
            "VALUES (:tenant_id, :email, :full_name, :external_code, :password_hash) "
            "ON CONFLICT (tenant_id, email) DO UPDATE SET password_hash = EXCLUDED.password_hash "
            "RETURNING id"
        ),
        {
            "tenant_id": tenant_id,
            "email": email,
            "full_name": full_name,
            "external_code": external_code,
            "password_hash": hash_password(password) if password else None,
        },
    ).scalar_one()


def main() -> int:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL no esta definida. Copia .env.example a .env y llena la cadena de conexion.")
        return 1

    engine = create_engine(url)
    with engine.begin() as conn:
        # --- roles y permisos: catalogo global, no llevan tenant_id ---
        role_ids = {}
        for role in ROLES:
            role_ids[role["code"]] = conn.execute(
                text(
                    "INSERT INTO app.roles (code, name, description) VALUES (:code, :name, :description) "
                    "ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code "
                    "RETURNING id"
                ),
                role,
            ).scalar_one()

        permission_ids = {}
        for permission in PERMISSIONS:
            permission_ids[permission["code"]] = conn.execute(
                text(
                    "INSERT INTO app.permissions (code, description) VALUES (:code, :description) "
                    "ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code "
                    "RETURNING id"
                ),
                permission,
            ).scalar_one()

        for role_code, permission_codes in ROLE_PERMISSIONS.items():
            for permission_code in permission_codes:
                conn.execute(
                    text(
                        "INSERT INTO app.role_permissions (role_id, permission_id) "
                        "VALUES (:role_id, :permission_id) ON CONFLICT DO NOTHING"
                    ),
                    {"role_id": role_ids[role_code], "permission_id": permission_ids[permission_code]},
                )

        # --- tenant/cliente/gestor de prueba para la pantalla web ---
        tenant_id = conn.execute(
            text(
                "INSERT INTO app.tenants (name, slug) VALUES (:name, :slug) "
                "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug "
                "RETURNING id"
            ),
            {"name": "Cliente Demo", "slug": TENANT_SLUG},
        ).scalar_one()

        conn.execute(text("SELECT app.tenant_session(:tenant_id)"), {"tenant_id": tenant_id})

        client_id = conn.execute(
            text(
                "INSERT INTO app.clients (tenant_id, name, code) VALUES (:tenant_id, :name, :code) "
                "ON CONFLICT (tenant_id, code) DO UPDATE SET code = EXCLUDED.code "
                "RETURNING id"
            ),
            {"tenant_id": tenant_id, "name": "Cartera Demo", "code": "demo"},
        ).scalar_one()

        agent_id = _upsert_user(conn, tenant_id, "agente-demo@example.com", "Gestor Demo", "AGT001", DEMO_PASSWORD)
        reviewer_id = _upsert_user(conn, tenant_id, "calidad-demo@example.com", "Calidad Demo", "QA001", DEMO_PASSWORD)
        admin_id = _upsert_user(conn, tenant_id, "admin-demo@example.com", "Admin Demo", None, DEMO_PASSWORD)

        conn.execute(
            text(
                "INSERT INTO app.agent_assignments (tenant_id, user_id, client_id) "
                "VALUES (:tenant_id, :user_id, :client_id) "
                "ON CONFLICT (user_id) DO UPDATE SET client_id = EXCLUDED.client_id"
            ),
            {"tenant_id": tenant_id, "user_id": agent_id, "client_id": client_id},
        )

        for user_id, role_code in ((agent_id, "agente"), (reviewer_id, "calidad"), (admin_id, "admin")):
            conn.execute(
                text(
                    "INSERT INTO app.user_roles (tenant_id, user_id, role_id) "
                    "VALUES (:tenant_id, :user_id, :role_id) ON CONFLICT DO NOTHING"
                ),
                {"tenant_id": tenant_id, "user_id": user_id, "role_id": role_ids[role_code]},
            )

        # --- taxonomia y checklist ---
        value_ids = []
        for category in TAXONOMY:
            category_id = conn.execute(
                text(
                    "INSERT INTO app.taxonomy_categories (tenant_id, code, name) "
                    "VALUES (:tenant_id, :code, :name) "
                    "ON CONFLICT (tenant_id, code) DO UPDATE SET code = EXCLUDED.code "
                    "RETURNING id"
                ),
                {"tenant_id": tenant_id, "code": category["category_code"], "name": category["category_name"]},
            ).scalar_one()

            for value in category["values"]:
                value_id = conn.execute(
                    text(
                        "INSERT INTO app.taxonomy_values (tenant_id, category_id, code, label, is_legal_risk) "
                        "VALUES (:tenant_id, :category_id, :code, :label, :is_legal_risk) "
                        "ON CONFLICT (tenant_id, category_id, code) DO UPDATE SET code = EXCLUDED.code "
                        "RETURNING id"
                    ),
                    {"tenant_id": tenant_id, "category_id": category_id, **value},
                ).scalar_one()
                value_ids.append(value_id)

        checklist_id = conn.execute(
            text("SELECT id FROM app.checklists WHERE client_id = :client_id AND is_active = true"),
            {"client_id": client_id},
        ).scalar_one_or_none()
        if checklist_id is None:
            checklist_id = conn.execute(
                text(
                    "INSERT INTO app.checklists (tenant_id, client_id, name) "
                    "VALUES (:tenant_id, :client_id, :name) RETURNING id"
                ),
                {"tenant_id": tenant_id, "client_id": client_id, "name": "Checklist demo"},
            ).scalar_one()

        for value_id in value_ids:
            conn.execute(
                text(
                    "INSERT INTO app.checklist_items (tenant_id, checklist_id, taxonomy_value_id) "
                    "VALUES (:tenant_id, :checklist_id, :taxonomy_value_id) ON CONFLICT DO NOTHING"
                ),
                {"tenant_id": tenant_id, "checklist_id": checklist_id, "taxonomy_value_id": value_id},
            )

    print(
        f"Listo. Tenant '{TENANT_SLUG}': cliente 'demo', {len(ROLES)} roles, {len(PERMISSIONS)} permisos, "
        f"{len(value_ids)} valores de taxonomia y un checklist con esos {len(value_ids)} items.\n"
        f"Usuarios de prueba (contrasena '{DEMO_PASSWORD}' para los tres):\n"
        f"  agente-demo@example.com (agente)\n"
        f"  calidad-demo@example.com (calidad)\n"
        f"  admin-demo@example.com (admin)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
