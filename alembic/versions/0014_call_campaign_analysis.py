"""analisis de campaña por IA (GPT-4o-mini), separado del reporte por
palabra clave

El reporte de campañas (webapp/services/reports.py) busca "spin"/"oxxo"/
etc. literal en la transcripcion -- gratis, pero se pierde variantes
foneticas que Whisper transcribe mal ("espin", "es pin", "spim", "esbin",
"SPI"...). Esta tabla guarda el resultado de un prompt de GPT-4o-mini que
si entiende esas variantes, ademas de extraer telefono/nombre del gestor/
nombre del cliente mencionados. Costo por llamada es minimo (~$0.0003,
casi nada comparado con la transcripcion) asi que vale la pena como capa
extra, sin reemplazar el reporte gratis existente.

Mismo patron CURRENT/SUPERSEDED que transcripts/call_analyses (regla #3:
nada se borra, un reanalisis crea una fila nueva). Regla #4 (evidencia
obligatoria) aplicada solo cuando la campaña es una de las conocidas
(SPIN/BRADESCARD/AZTECA/INVEX) -- "OTRA"/"NO_DETERMINADA" no afirman nada
puntual, no las exige.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-29
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app.call_campaign_analysis (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            uuid NOT NULL REFERENCES app.tenants(id),
            call_id              uuid NOT NULL REFERENCES app.calls(id),
            state                text NOT NULL DEFAULT 'CURRENT' CHECK (state IN ('CURRENT', 'SUPERSEDED')),
            model                text NOT NULL,
            campana              text NOT NULL
                CHECK (campana IN ('SPIN', 'BRADESCARD', 'AZTECA', 'INVEX', 'OTRA', 'NO_DETERMINADA')),
            confianza            text CHECK (confianza IN ('alta', 'media', 'baja')),
            evidencia            text,
            telefono_mencionado  text,
            nombre_gestor        text,
            nombre_cliente       text,
            raw_response         text,
            created_at           timestamptz NOT NULL DEFAULT now(),
            CHECK (campana NOT IN ('SPIN', 'BRADESCARD', 'AZTECA', 'INVEX') OR evidencia IS NOT NULL)
        );
    """)

    op.execute("""
        CREATE INDEX call_campaign_analysis_call_id_current_idx
            ON app.call_campaign_analysis (call_id)
            WHERE state = 'CURRENT';
    """)

    op.execute("ALTER TABLE app.call_campaign_analysis ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE app.call_campaign_analysis FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation ON app.call_campaign_analysis
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.call_campaign_analysis;")
