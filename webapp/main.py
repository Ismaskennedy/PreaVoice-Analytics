import hashlib
import re
import unicodedata
import uuid
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
from xhtml2pdf import pisa

from webapp.auth import (
    current_user,
    find_user_by_email,
    hash_password,
    log_in,
    log_out,
    require_admin,
    require_login,
    require_role,
    verify_password,
)
from webapp.config import RECORDINGS_DIR, SECRET_KEY
from webapp.db import tenant_connection
from webapp.openai_client import get_openai_client
from webapp.services.analysis import analyze, compute_score
from webapp.services.reports import build_pulso_diario
from webapp.services.transcription import assess_audio_quality, transcribe

app = FastAPI(title="PREA Voice Analytics")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/audio", StaticFiles(directory=str(RECORDINGS_DIR)), name="audio")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _agent_id(conn) -> str:
    return conn.execute(
        text(
            "SELECT u.id FROM app.users u "
            "JOIN app.user_roles ur ON ur.user_id = u.id "
            "JOIN app.roles r ON r.id = ur.role_id "
            "WHERE r.code = 'agente' LIMIT 1"
        )
    ).scalar_one()


def process_call(call_id: str) -> None:
    """Transcribe y analiza una llamada, de punta a punta, sincrono.
    Pensado para el volumen de una demo, no para produccion: en volumen
    real esto es exactamente lo que ops.processing_jobs deberia repartir
    entre varios workers en segundo plano en vez de bloquear la carga."""
    openai_client = get_openai_client()

    with tenant_connection() as (conn, _tenant_id):
        storage_path = conn.execute(
            text("SELECT storage_path FROM app.recordings WHERE call_id = :call_id"),
            {"call_id": call_id},
        ).scalar_one()
        client_id = conn.execute(
            text("SELECT client_id FROM app.calls WHERE id = :call_id"), {"call_id": call_id}
        ).scalar_one()
        conn.execute(text("UPDATE app.calls SET status = 'transcribing' WHERE id = :call_id"), {"call_id": call_id})

    segments, raw_segments = transcribe(openai_client, Path(storage_path))
    audio_quality = assess_audio_quality(raw_segments)

    with tenant_connection() as (conn, tenant_id):
        transcript_id = conn.execute(
            text(
                "INSERT INTO app.transcripts (tenant_id, call_id, provider) "
                "VALUES (:tenant_id, :call_id, 'openai-whisper') RETURNING id"
            ),
            {"tenant_id": tenant_id, "call_id": call_id},
        ).scalar_one()
        for segment in segments:
            conn.execute(
                text(
                    "INSERT INTO app.transcript_segments "
                    "(tenant_id, transcript_id, speaker, start_ms, end_ms, text) "
                    "VALUES (:tenant_id, :transcript_id, :speaker, :start_ms, :end_ms, :text)"
                ),
                {"tenant_id": tenant_id, "transcript_id": transcript_id, **segment},
            )
        conn.execute(text("UPDATE app.calls SET status = 'transcribed' WHERE id = :call_id"), {"call_id": call_id})

        checklist_items = conn.execute(
            text(
                "SELECT ci.id AS checklist_item_id, tv.code, tv.label, tv.is_legal_risk "
                "FROM app.checklist_items ci "
                "JOIN app.checklists cl ON cl.id = ci.checklist_id "
                "JOIN app.taxonomy_values tv ON tv.id = ci.taxonomy_value_id "
                "WHERE cl.client_id = :client_id AND cl.is_active = true "
                "ORDER BY ci.display_order, tv.code"
            ),
            {"client_id": client_id},
        ).mappings().all()
        checklist_items = [dict(row) for row in checklist_items]
        call_script = conn.execute(
            text("SELECT call_script FROM app.clients WHERE id = :client_id"), {"client_id": client_id}
        ).scalar_one()
        conn.execute(text("UPDATE app.calls SET status = 'analyzing' WHERE id = :call_id"), {"call_id": call_id})

    findings, agent_emotion, debtor_emotion, coaching_suggestions, call_summary, raw_response_text = analyze(
        openai_client, segments, checklist_items, call_script
    )
    score, _legal_risk_triggered = compute_score(findings)

    with tenant_connection() as (conn, tenant_id):
        analysis_id = conn.execute(
            text(
                "INSERT INTO app.call_analyses "
                "(tenant_id, call_id, transcript_id, model, prompt_version, raw_response, "
                "agent_emotion, agent_emotion_quote, agent_emotion_start_ms, agent_emotion_end_ms, "
                "debtor_emotion, debtor_emotion_quote, debtor_emotion_start_ms, debtor_emotion_end_ms, "
                "audio_quality, audio_quality_notes, "
                "who_answered, who_answered_quote, who_answered_start_ms, who_answered_end_ms, summary) "
                "VALUES (:tenant_id, :call_id, :transcript_id, :model, 'v1', :raw_response, "
                ":agent_emotion, :agent_emotion_quote, :agent_emotion_start_ms, :agent_emotion_end_ms, "
                ":debtor_emotion, :debtor_emotion_quote, :debtor_emotion_start_ms, :debtor_emotion_end_ms, "
                ":audio_quality, :audio_quality_notes, "
                ":who_answered, :who_answered_quote, :who_answered_start_ms, :who_answered_end_ms, :summary) "
                "RETURNING id"
            ),
            {
                "tenant_id": tenant_id,
                "call_id": call_id,
                "transcript_id": transcript_id,
                "model": "gpt-4o-mini",
                "raw_response": raw_response_text,
                "agent_emotion": agent_emotion["label"],
                "agent_emotion_quote": agent_emotion["quote"],
                "agent_emotion_start_ms": agent_emotion["start_ms"],
                "agent_emotion_end_ms": agent_emotion["end_ms"],
                "debtor_emotion": debtor_emotion["label"],
                "debtor_emotion_quote": debtor_emotion["quote"],
                "debtor_emotion_start_ms": debtor_emotion["start_ms"],
                "debtor_emotion_end_ms": debtor_emotion["end_ms"],
                "audio_quality": audio_quality["label"],
                "audio_quality_notes": audio_quality["notes"],
                **call_summary,
            },
        ).scalar_one()

        for finding in findings:
            conn.execute(
                text(
                    "INSERT INTO app.call_analysis_findings "
                    "(tenant_id, analysis_id, checklist_item_id, is_met, "
                    "evidence_start_ms, evidence_end_ms, evidence_quote, notes) "
                    "VALUES (:tenant_id, :analysis_id, :checklist_item_id, :is_met, "
                    ":evidence_start_ms, :evidence_end_ms, :evidence_quote, :notes)"
                ),
                {
                    "tenant_id": tenant_id,
                    "analysis_id": analysis_id,
                    "checklist_item_id": finding["checklist_item_id"],
                    "is_met": finding["is_met"],
                    "evidence_start_ms": finding["evidence_start_ms"],
                    "evidence_end_ms": finding["evidence_end_ms"],
                    "evidence_quote": finding["evidence_quote"],
                    "notes": finding["notes"],
                },
            )

        for suggestion in coaching_suggestions:
            conn.execute(
                text(
                    "INSERT INTO app.call_coaching_suggestions "
                    "(tenant_id, analysis_id, category, suggestion, reference_quote, "
                    "reference_start_ms, reference_end_ms) "
                    "VALUES (:tenant_id, :analysis_id, :category, :suggestion, :reference_quote, "
                    ":reference_start_ms, :reference_end_ms)"
                ),
                {"tenant_id": tenant_id, "analysis_id": analysis_id, **suggestion},
            )

        conn.execute(
            text(
                "INSERT INTO app.call_evaluations (tenant_id, call_id, analysis_id, score) "
                "VALUES (:tenant_id, :call_id, :analysis_id, :score)"
            ),
            {"tenant_id": tenant_id, "call_id": call_id, "analysis_id": analysis_id, "score": score},
        )
        conn.execute(text("UPDATE app.calls SET status = 'analyzed' WHERE id = :call_id"), {"call_id": call_id})


# --- autenticacion ---

@app.get("/login")
def login_form(request: Request):
    if current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"user": None})


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    with tenant_connection() as (conn, _tenant_id):
        user = find_user_by_email(conn, email)

    if not user or not user["password_hash"] or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request, "login.html", {"user": None, "error": "Correo o contraseña incorrectos."}
        )

    log_in(request, user)
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    log_out(request)
    return RedirectResponse(url="/login", status_code=303)


# --- dashboard ---

@app.get("/")
def dashboard(request: Request):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, _tenant_id):
        totals = conn.execute(
            text(
                "SELECT count(*) AS total_calls, round(avg(ev.score), 1) AS avg_score "
                "FROM app.calls c "
                "LEFT JOIN app.call_evaluations ev ON ev.call_id = c.id AND ev.state = 'CURRENT'"
            )
        ).mappings().one()

        legal_risk_count = conn.execute(
            text(
                "SELECT count(DISTINCT a.call_id) FROM app.call_analyses a "
                "JOIN app.call_analysis_findings f ON f.analysis_id = a.id "
                "JOIN app.checklist_items ci ON ci.id = f.checklist_item_id "
                "JOIN app.taxonomy_values tv ON tv.id = ci.taxonomy_value_id "
                "WHERE a.state = 'CURRENT' AND f.is_met = true AND tv.is_legal_risk = true"
            )
        ).scalar_one()

        checklist_rows = conn.execute(
            text(
                "SELECT tv.label AS label, "
                "count(*) FILTER (WHERE f.is_met = true) AS cumplidos, "
                "count(*) FILTER (WHERE f.is_met IS NOT NULL) AS evaluados "
                "FROM app.call_analysis_findings f "
                "JOIN app.call_analyses a ON a.id = f.analysis_id AND a.state = 'CURRENT' "
                "JOIN app.checklist_items ci ON ci.id = f.checklist_item_id "
                "JOIN app.taxonomy_values tv ON tv.id = ci.taxonomy_value_id "
                "GROUP BY tv.label, tv.code ORDER BY tv.code"
            )
        ).mappings().all()

        trend_rows = conn.execute(
            text(
                "SELECT c.uploaded_at, ev.score FROM app.calls c "
                "JOIN app.call_evaluations ev ON ev.call_id = c.id AND ev.state = 'CURRENT' "
                "WHERE ev.score IS NOT NULL ORDER BY c.uploaded_at"
            )
        ).mappings().all()

    checklist_stats = [
        {"label": row["label"], "porcentaje": round(row["cumplidos"] / row["evaluados"] * 100, 1) if row["evaluados"] else 0}
        for row in checklist_rows
        if row["evaluados"]
    ]
    score_trend = [
        {"etiqueta": row["uploaded_at"].strftime("%d/%m %H:%M"), "score": float(row["score"])} for row in trend_rows
    ]

    metrics = {
        "total_calls": totals["total_calls"],
        "avg_score": float(totals["avg_score"]) if totals["avg_score"] is not None else None,
        "legal_risk_count": legal_risk_count,
    }

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "metrics": metrics, "checklist_stats": checklist_stats, "score_trend": score_trend},
    )


# --- llamadas ---

@app.get("/calls")
def list_calls(request: Request, agent_id: str | None = None, status: str = "", solo_riesgo: bool = False):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, _tenant_id):
        agents = conn.execute(
            text(
                """
                SELECT u.id, u.full_name, count(c.id) AS call_count
                FROM app.users u
                JOIN app.user_roles ur ON ur.user_id = u.id
                JOIN app.roles r ON r.id = ur.role_id AND r.code = 'agente'
                LEFT JOIN app.calls c ON c.agent_id = u.id
                GROUP BY u.id, u.full_name
                ORDER BY u.full_name
                """
            )
        ).mappings().all()
        agents = [
            {**dict(a), "initials": "".join(p[0] for p in a["full_name"].split()[:2]).upper()} for a in agents
        ]

        # Sin agente elegido todavia: se para en el primero de la lista,
        # como una bandeja de WhatsApp que abre la primera conversacion.
        selected_agent_id = agent_id or (str(agents[0]["id"]) if agents else None)

        calls = []
        if selected_agent_id:
            calls = conn.execute(
                text(
                    """
                    SELECT
                        c.id, c.uploaded_at, c.status, r.original_filename,
                        ev.score, ev.status AS evaluation_status,
                        EXISTS (
                            SELECT 1 FROM app.call_analysis_findings f
                            JOIN app.call_analyses a ON a.id = f.analysis_id AND a.state = 'CURRENT'
                            JOIN app.checklist_items ci ON ci.id = f.checklist_item_id
                            JOIN app.taxonomy_values tv ON tv.id = ci.taxonomy_value_id
                            WHERE a.call_id = c.id AND f.is_met = true AND tv.is_legal_risk = true
                        ) AS has_legal_risk
                    FROM app.calls c
                    LEFT JOIN app.recordings r ON r.call_id = c.id
                    LEFT JOIN app.call_evaluations ev ON ev.call_id = c.id AND ev.state = 'CURRENT'
                    WHERE c.agent_id = :agent_id
                      AND (:status = '' OR c.status = :status)
                    ORDER BY c.uploaded_at DESC
                    """
                ),
                {"agent_id": selected_agent_id, "status": status},
            ).mappings().all()
            if solo_riesgo:
                calls = [c for c in calls if c["has_legal_risk"]]

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "ancho": True,
            "agents": agents,
            "selected_agent_id": selected_agent_id,
            "calls": calls,
            "status": status,
            "solo_riesgo": solo_riesgo,
            "call_statuses": ["uploaded", "transcribing", "transcribed", "analyzing", "analyzed", "failed"],
        },
    )


@app.get("/calls/{call_id}")
def call_detail(request: Request, call_id: str):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, _tenant_id):
        call = conn.execute(
            text(
                """
                SELECT c.id, c.status, c.uploaded_at, cl.name AS client_name, u.full_name AS agent_name,
                       r.storage_path, r.original_filename
                FROM app.calls c
                JOIN app.clients cl ON cl.id = c.client_id
                JOIN app.users u ON u.id = c.agent_id
                LEFT JOIN app.recordings r ON r.call_id = c.id
                WHERE c.id = :call_id
                """
            ),
            {"call_id": call_id},
        ).mappings().one()

        segments = conn.execute(
            text(
                "SELECT ts.speaker, ts.start_ms, ts.end_ms, ts.text "
                "FROM app.transcript_segments ts "
                "JOIN app.transcripts t ON t.id = ts.transcript_id "
                "WHERE t.call_id = :call_id AND t.state = 'CURRENT' "
                "ORDER BY ts.start_ms"
            ),
            {"call_id": call_id},
        ).mappings().all()

        analysis = conn.execute(
            text(
                "SELECT agent_emotion, agent_emotion_quote, agent_emotion_start_ms, agent_emotion_end_ms, "
                "debtor_emotion, debtor_emotion_quote, debtor_emotion_start_ms, debtor_emotion_end_ms, "
                "audio_quality, audio_quality_notes, "
                "who_answered, who_answered_quote, who_answered_start_ms, who_answered_end_ms, summary "
                "FROM app.call_analyses WHERE call_id = :call_id AND state = 'CURRENT'"
            ),
            {"call_id": call_id},
        ).mappings().one_or_none()

        findings = conn.execute(
            text(
                "SELECT tv.label, tv.is_legal_risk, f.is_met, f.evidence_quote, "
                "f.evidence_start_ms, f.evidence_end_ms, f.notes "
                "FROM app.call_analysis_findings f "
                "JOIN app.call_analyses a ON a.id = f.analysis_id "
                "JOIN app.checklist_items ci ON ci.id = f.checklist_item_id "
                "JOIN app.taxonomy_values tv ON tv.id = ci.taxonomy_value_id "
                "WHERE a.call_id = :call_id AND a.state = 'CURRENT' "
                "ORDER BY tv.is_legal_risk DESC, tv.code"
            ),
            {"call_id": call_id},
        ).mappings().all()

        suggestions = conn.execute(
            text(
                "SELECT category, suggestion, reference_quote, reference_start_ms, reference_end_ms "
                "FROM app.call_coaching_suggestions s "
                "JOIN app.call_analyses a ON a.id = s.analysis_id "
                "WHERE a.call_id = :call_id AND a.state = 'CURRENT' "
                "ORDER BY s.created_at"
            ),
            {"call_id": call_id},
        ).mappings().all()
        mejora_llamada = [s for s in suggestions if s["category"] == "mejora_llamada"]
        cierre_negociacion = [s for s in suggestions if s["category"] == "cierre_negociacion"]

        evaluation = conn.execute(
            text(
                "SELECT ev.id, ev.score, ev.status, ev.confirmed_at, ru.full_name AS confirmed_by_name "
                "FROM app.call_evaluations ev "
                "LEFT JOIN app.users ru ON ru.id = ev.confirmed_by "
                "WHERE ev.call_id = :call_id AND ev.state = 'CURRENT'"
            ),
            {"call_id": call_id},
        ).mappings().one_or_none()

    audio_url = f"/audio/{Path(call['storage_path']).name}" if call["storage_path"] else None

    return templates.TemplateResponse(
        request,
        "call_detail.html",
        {
            "user": user,
            "call": call,
            "audio_url": audio_url,
            "segments": segments,
            "findings": findings,
            "evaluation": evaluation,
            "analysis": analysis,
            "mejora_llamada": mejora_llamada,
            "cierre_negociacion": cierre_negociacion,
            "who_answered_labels": {
                "titular": "El titular",
                "familiar": "Un familiar",
                "tercero": "Un tercero",
                "no_contesto": "No contestó",
            },
            "can_confirm": user["role"] in ("calidad", "admin"),
        },
    )


@app.post("/calls/{call_id}/confirm")
def confirm_evaluation(request: Request, call_id: str):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in ("calidad", "admin"):
        return RedirectResponse(url=f"/calls/{call_id}", status_code=303)

    with tenant_connection() as (conn, _tenant_id):
        conn.execute(
            text(
                "UPDATE app.call_evaluations "
                "SET status = 'confirmed', confirmed_by = :reviewer_id, confirmed_at = now() "
                "WHERE call_id = :call_id AND state = 'CURRENT'"
            ),
            {"reviewer_id": user["id"], "call_id": call_id},
        )
    return RedirectResponse(url=f"/calls/{call_id}", status_code=303)


@app.post("/calls/upload")
async def upload_call(request: Request, file: UploadFile = File(...)):
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user

    contents = await file.read()
    checksum = hashlib.sha256(contents).hexdigest()
    extension = Path(file.filename).suffix.lstrip(".") or "bin"

    with tenant_connection() as (conn, tenant_id):
        # Si quien subio el archivo es un agente, la llamada es suya. Si no
        # (Calidad o un admin subiendo en nombre de alguien), se usa el
        # primer agente que haya -- todavia no hay un selector en pantalla.
        agent_id = user["id"] if user["role"] == "agente" else _agent_id(conn)

        call_id = conn.execute(
            text(
                "INSERT INTO app.calls (tenant_id, agent_id, uploaded_by) "
                "VALUES (:tenant_id, :agent_id, :uploaded_by) RETURNING id"
            ),
            {"tenant_id": tenant_id, "agent_id": agent_id, "uploaded_by": user["id"]},
        ).scalar_one()

        storage_path = RECORDINGS_DIR / f"{call_id}.{extension}"
        storage_path.write_bytes(contents)

        conn.execute(
            text(
                "INSERT INTO app.recordings "
                "(tenant_id, call_id, storage_path, original_filename, checksum_sha256, format) "
                "VALUES (:tenant_id, :call_id, :storage_path, :original_filename, :checksum, :format)"
            ),
            {
                "tenant_id": tenant_id,
                "call_id": call_id,
                "storage_path": str(storage_path),
                "original_filename": file.filename,
                "checksum": checksum,
                "format": extension,
            },
        )

    try:
        process_call(call_id)
    except Exception as exc:  # noqa: BLE001 - no queremos tumbar la carga si falla la IA
        with tenant_connection() as (conn, _tenant_id):
            conn.execute(text("UPDATE app.calls SET status = 'failed' WHERE id = :call_id"), {"call_id": call_id})
        print(f"Fallo el procesamiento de la llamada {call_id}: {exc}")

    return RedirectResponse(url="/calls", status_code=303)


# --- reportes ---

def _parse_report_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


@app.get("/reports")
def reports_home(request: Request, date: str | None = None):  # "date" (str) tapa a proposito el modulo datetime.date dentro de esta funcion
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user

    report_date = _parse_report_date(date)
    with tenant_connection() as (conn, _tenant_id):
        data = build_pulso_diario(conn, report_date)

    return templates.TemplateResponse(
        request,
        "reports.html",
        {"user": user, "data": data, "selected_date": report_date.isoformat()},
    )


@app.get("/reports/pulso-diario")
def report_pulso_diario(request: Request, date: str | None = None):
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user

    report_date = _parse_report_date(date)
    with tenant_connection() as (conn, _tenant_id):
        data = build_pulso_diario(conn, report_date)

    html = templates.get_template("report_pulso_diario.html").render(
        data=data, team_name="PREA · Cobranza Preventiva"
    )

    pdf_buffer = BytesIO()
    pisa.CreatePDF(html, dest=pdf_buffer)

    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=pulso-diario-{report_date.isoformat()}.pdf"},
    )


# --- clientes (carteras): alta, asignar gestores, armar su checklist ---

def _slugify(label: str) -> str:
    normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^\w\s-]", "", normalized).strip().lower()
    return re.sub(r"[\s-]+", "_", normalized)[:60] or "criterio"


@app.get("/clients")
def list_clients(request: Request):
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, _tenant_id):
        clients = conn.execute(
            text(
                """
                SELECT c.id, c.name, c.code, c.is_active,
                    (SELECT count(*) FROM app.agent_assignments aa WHERE aa.client_id = c.id) AS agent_count,
                    (SELECT count(*) FROM app.checklist_items ci
                        JOIN app.checklists cl ON cl.id = ci.checklist_id
                        WHERE cl.client_id = c.id AND cl.is_active = true) AS item_count
                FROM app.clients c
                ORDER BY c.name
                """
            )
        ).mappings().all()

    return templates.TemplateResponse(request, "clients.html", {"user": user, "clients": clients})


@app.get("/clients/new")
def new_client_form(request: Request):
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "client_new.html", {"user": user})


@app.post("/clients")
def create_client(request: Request, name: str = Form(...), code: str = Form(...)):
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, tenant_id):
        client_id = conn.execute(
            text("INSERT INTO app.clients (tenant_id, name, code) VALUES (:tenant_id, :name, :code) RETURNING id"),
            {"tenant_id": tenant_id, "name": name, "code": code},
        ).scalar_one()
        # Cada cliente arranca con un checklist propio y vacio -- el "entrenamiento"
        # de la IA para este cliente es agregarle criterios despues.
        conn.execute(
            text("INSERT INTO app.checklists (tenant_id, client_id, name) VALUES (:tenant_id, :client_id, :name)"),
            {"tenant_id": tenant_id, "client_id": client_id, "name": f"Checklist {name}"},
        )

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@app.get("/clients/{client_id}")
def client_detail(request: Request, client_id: str):
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, _tenant_id):
        client = conn.execute(
            text("SELECT id, name, code, is_active, call_script FROM app.clients WHERE id = :id"), {"id": client_id}
        ).mappings().one()

        assigned_agents = conn.execute(
            text(
                "SELECT u.id, u.full_name FROM app.agent_assignments aa "
                "JOIN app.users u ON u.id = aa.user_id "
                "WHERE aa.client_id = :client_id ORDER BY u.full_name"
            ),
            {"client_id": client_id},
        ).mappings().all()

        all_agents = conn.execute(
            text(
                "SELECT u.id, u.full_name FROM app.users u "
                "JOIN app.user_roles ur ON ur.user_id = u.id "
                "JOIN app.roles r ON r.id = ur.role_id AND r.code = 'agente' "
                "ORDER BY u.full_name"
            )
        ).mappings().all()

        checklist_items = conn.execute(
            text(
                "SELECT tv.label, tv.is_legal_risk FROM app.checklist_items ci "
                "JOIN app.taxonomy_values tv ON tv.id = ci.taxonomy_value_id "
                "JOIN app.checklists cl ON cl.id = ci.checklist_id "
                "WHERE cl.client_id = :client_id AND cl.is_active = true "
                "ORDER BY tv.is_legal_risk DESC, ci.display_order"
            ),
            {"client_id": client_id},
        ).mappings().all()

    return templates.TemplateResponse(
        request,
        "client_detail.html",
        {
            "user": user,
            "client": client,
            "assigned_agents": assigned_agents,
            "all_agents": all_agents,
            "checklist_items": checklist_items,
        },
    )


@app.post("/clients/{client_id}/assign-agent")
def assign_agent(request: Request, client_id: str, agent_id: str = Form(...)):
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, tenant_id):
        # Un gestor solo puede estar asignado a un cliente a la vez
        # (app.agent_assignments tiene PK en user_id): esto lo reasigna si
        # ya estaba en otro. Sus llamadas viejas se quedan con la cartera
        # vieja (regla #2) -- reasignar aqui no las toca.
        conn.execute(
            text(
                "INSERT INTO app.agent_assignments (tenant_id, user_id, client_id) "
                "VALUES (:tenant_id, :agent_id, :client_id) "
                "ON CONFLICT (user_id) DO UPDATE SET client_id = EXCLUDED.client_id"
            ),
            {"tenant_id": tenant_id, "agent_id": agent_id, "client_id": client_id},
        )

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/call-script")
def update_call_script(request: Request, client_id: str, call_script: str = Form("")):
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, _tenant_id):
        conn.execute(
            text("UPDATE app.clients SET call_script = :call_script WHERE id = :client_id"),
            {"call_script": call_script.strip() or None, "client_id": client_id},
        )

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@app.post("/clients/{client_id}/checklist-items")
def add_checklist_item(
    request: Request, client_id: str, label: str = Form(...), is_legal_risk: str = Form("")
):
    user = require_role(request, ("calidad", "admin"))
    if isinstance(user, RedirectResponse):
        return user

    is_risk = is_legal_risk == "true"
    category_code = "riesgo_legal" if is_risk else "elementos_llamada"
    category_name = "Practicas prohibidas (riesgo legal)" if is_risk else "Elementos esperados de la llamada"

    with tenant_connection() as (conn, tenant_id):
        # Reusa la categoria si ya existe (idempotente) -- asi no hace falta
        # haber corrido make seed para que esto funcione en un tenant nuevo.
        category_id = conn.execute(
            text(
                "INSERT INTO app.taxonomy_categories (tenant_id, code, name) "
                "VALUES (:tenant_id, :code, :name) "
                "ON CONFLICT (tenant_id, code) DO UPDATE SET code = EXCLUDED.code "
                "RETURNING id"
            ),
            {"tenant_id": tenant_id, "code": category_code, "name": category_name},
        ).scalar_one()

        # El codigo de la taxonomia tiene que ser unico; con texto libre del
        # usuario, la forma mas simple de garantizarlo es pegarle un sufijo.
        value_code = f"{_slugify(label)}_{uuid.uuid4().hex[:6]}"
        value_id = conn.execute(
            text(
                "INSERT INTO app.taxonomy_values (tenant_id, category_id, code, label, is_legal_risk) "
                "VALUES (:tenant_id, :category_id, :code, :label, :is_legal_risk) "
                "RETURNING id"
            ),
            {
                "tenant_id": tenant_id,
                "category_id": category_id,
                "code": value_code,
                "label": label,
                "is_legal_risk": is_risk,
            },
        ).scalar_one()

        checklist_id = conn.execute(
            text("SELECT id FROM app.checklists WHERE client_id = :client_id AND is_active = true"),
            {"client_id": client_id},
        ).scalar_one()

        conn.execute(
            text(
                "INSERT INTO app.checklist_items (tenant_id, checklist_id, taxonomy_value_id) "
                "VALUES (:tenant_id, :checklist_id, :taxonomy_value_id)"
            ),
            {"tenant_id": tenant_id, "checklist_id": checklist_id, "taxonomy_value_id": value_id},
        )

    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


# --- usuarios (solo admin) ---

@app.get("/users")
def list_users(request: Request):
    user = require_admin(request)
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, _tenant_id):
        rows = conn.execute(
            text(
                "SELECT u.full_name, u.email, u.external_code, r.code AS role "
                "FROM app.users u "
                "LEFT JOIN app.user_roles ur ON ur.user_id = u.id "
                "LEFT JOIN app.roles r ON r.id = ur.role_id "
                "ORDER BY u.full_name"
            )
        ).mappings().all()
    return templates.TemplateResponse(request, "users.html", {"user": user, "users": rows})


@app.get("/users/new")
def new_user_form(request: Request):
    user = require_admin(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "user_new.html", {"user": user})


@app.post("/users")
def create_user(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    external_code: str = Form(""),
):
    user = require_admin(request)
    if isinstance(user, RedirectResponse):
        return user

    with tenant_connection() as (conn, tenant_id):
        role_id = conn.execute(text("SELECT id FROM app.roles WHERE code = :code"), {"code": role}).scalar_one()
        new_user_id = conn.execute(
            text(
                "INSERT INTO app.users (tenant_id, email, full_name, password_hash, external_code) "
                "VALUES (:tenant_id, :email, :full_name, :password_hash, :external_code) "
                "RETURNING id"
            ),
            {
                "tenant_id": tenant_id,
                "email": email,
                "full_name": full_name,
                "password_hash": hash_password(password),
                "external_code": external_code or None,
            },
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO app.user_roles (tenant_id, user_id, role_id) "
                "VALUES (:tenant_id, :user_id, :role_id)"
            ),
            {"tenant_id": tenant_id, "user_id": new_user_id, "role_id": role_id},
        )

    return RedirectResponse(url="/users", status_code=303)
