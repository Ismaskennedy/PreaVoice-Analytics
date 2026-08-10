"""agrega password_hash a app.users, para el login

Pedido explicito del usuario (login real, con alta de usuarios). El hash
se calcula con hashlib.pbkdf2_hmac (libreria estandar de Python, sin
depender de un paquete nuevo) -- ver webapp/auth.py.

password_hash es nullable a proposito: un usuario puede existir sin poder
loguearse todavia (por ejemplo, si mas adelante se crean usuarios por
integracion antes de que alguien les ponga contrasena).

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-09
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.users ADD COLUMN password_hash text;")


def downgrade() -> None:
    op.execute("ALTER TABLE app.users DROP COLUMN IF EXISTS password_hash;")
