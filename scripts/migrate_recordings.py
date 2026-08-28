"""Mueve las grabaciones de app.recordings a una carpeta nueva, repartidas
en subcarpetas por los primeros 2 caracteres del call_id (256 posibles:
00-ff), y actualiza storage_path en la base para que apunten ahi.

Por que: FAT32 (el formato de la USB que estamos usando) tiene un limite
real de entradas por carpeta -- con nombres largos como un UUID.mp3, se
topa alrededor de 13,000 archivos en una sola carpeta plana. Repartiendo en
256 subcarpetas, cada una se queda con una fraccion chica incluso a
500,000 grabaciones totales.

No borra el origen -- eso se hace aparte, a mano, despues de confirmar que
todo se copio bien.

Uso:
    .venv/Scripts/python.exe scripts/migrate_recordings.py "D:\\prea-recordings"
    .venv/Scripts/python.exe scripts/migrate_recordings.py "D:\\prea-recordings" --limit 20  # probar primero
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from webapp.db import tenant_connection

CHUNK_SIZE = 500  # cuantas filas se actualizan por conexion/transaccion


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_base", help="carpeta nueva donde van a vivir las grabaciones, ya repartidas")
    parser.add_argument("--limit", type=int, default=None, help="migrar solo las primeras N (para probar)")
    args = parser.parse_args()

    new_base = Path(args.new_base)
    new_base.mkdir(parents=True, exist_ok=True)

    with tenant_connection() as (conn, _tenant_id):
        query = "SELECT id, call_id, storage_path, format FROM app.recordings WHERE storage_path IS NOT NULL"
        if args.limit:
            query += f" LIMIT {args.limit}"
        rows = conn.execute(text(query)).mappings().all()

    total = len(rows)
    print(f"{total} grabaciones a migrar hacia {new_base}...")

    moved = 0
    already_there = 0
    missing = 0
    failed: list[str] = []
    by_shard: dict[str, int] = {}
    project_root = Path(__file__).resolve().parent.parent

    for chunk_start in range(0, total, CHUNK_SIZE):
        chunk = rows[chunk_start : chunk_start + CHUNK_SIZE]
        updates = []

        for row in chunk:
            source = Path(row["storage_path"])
            if not source.is_absolute():
                source = project_root / source

            shard = str(row["call_id"])[:2]
            extension = f".{row['format']}" if row["format"] else source.suffix
            dest_dir = new_base / shard
            dest_path = dest_dir / f"{row['call_id']}{extension}"

            if dest_path.exists():
                already_there += 1
            elif not source.exists():
                missing += 1
                failed.append(f"{row['id']}: no existe el origen {source}")
            else:
                try:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest_path)
                    moved += 1
                except Exception as exc:  # noqa: BLE001 - un archivo malo no debe tumbar el resto
                    failed.append(f"{row['id']}: {exc}")
                    continue

            if dest_path.exists():
                updates.append({"p": str(dest_path), "id": row["id"]})
                by_shard[shard] = by_shard.get(shard, 0) + 1

        if updates:
            with tenant_connection() as (conn, _tenant_id):
                conn.execute(text("UPDATE app.recordings SET storage_path = :p WHERE id = :id"), updates)

        done = min(chunk_start + CHUNK_SIZE, total)
        print(f"  {done}/{total} (movidos: {moved}, ya estaban: {already_there}, faltantes: {missing})")

    print(f"\nListo: {moved} copiados, {already_there} ya estaban, {missing} con el origen perdido.")
    print(f"Subcarpetas usadas: {len(by_shard)} (max archivos en una: {max(by_shard.values()) if by_shard else 0})")
    if failed:
        print(f"\n{len(failed)} fallos, primeros 10:")
        for line in failed[:10]:
            print(f"  {line}")


if __name__ == "__main__":
    main()
