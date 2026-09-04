"""
reporte_acumulado_sanofi.py
============================
Genera el reporte ACUMULADO de operaciones del Grupo Sanofi (2025 y 2026)
solicitado por el cliente: Importaciones, Exportaciones, Corresponsalías,
R1/T3, pedimentos globales/complementarios (si existen) y operaciones
facturadas a terceros/pacientes, de los pedimentos emitidos a nombre de
Sanofi Pasteur, Sanofi Aventis de México, Azteca Vacunas y Sanofi México, en
las aduanas AICM/AIFA/Veracruz/Laredo/Manzanillo (+ Corresponsalías),
segregado por BU (CHC / GENMED / INV. CLÍNICA).

Usa la misma consulta base que Sanofi_V6.sql (patrón de Cliente ya probado
contra la base real) pero con el WHERE ampliado de
Sanofi_Acumulado_2025_2026.sql. El rango de fechas se puede ajustar por CLI:

    python reporte_acumulado_sanofi.py
    python reporte_acumulado_sanofi.py --fecha-ini 2025-01-01 --fecha-fin 2026-08-31

Credenciales de SQL Server: se toman de common.py / .env (SIR_SQL_*), igual
que el resto del repo — no hay contraseñas en este archivo.

IMPORTANTE — revisar antes de enviar a Sanofi:
  Este script NO valida que el ejercicio 2025 esté "completamente facturado y
  sin adeudos pendientes" (esa condición del cliente no tiene un campo claro
  en la sábana); es una verificación manual que debe hacerse antes de enviar.
  Ver también la hoja "Notas y Supuestos" del Excel generado, con el resto de
  supuestos aplicados (BU, responsable de pago R1, heurística de
  terceros/pacientes, pedimentos globales/complementarios).
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

import common

BASE_DIR = Path(__file__).resolve().parent
SQL_FILE = BASE_DIR / "Sanofi_Acumulado_2025_2026.sql"
OUTPUT_DIR = BASE_DIR / "salidas"

DEFAULT_FECHA_INI = "2025-01-01"
DEFAULT_FECHA_FIN = "2026-08-31"

BU_SHEETS = ["CHC", "GENMED", "INV. CLÍNICA", "OTRAS/SIN BU"]

# Columnas explícitamente pedidas por el cliente para no quedar vacías.
# El resto de columnas numéricas/texto también se rellenan (ver rellenar_vacios).
COLUMNAS_CRITICAS_NO_VACIAS = ["Impuestos", "Moneda", "Tax ID", "BU"]


def leer_query(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def ajustar_rango_fechas(query: str, fecha_ini: str, fecha_fin: str) -> str:
    query = re.sub(
        r"(TRY_CONVERT\(DATE,\s*\[Fecha de Pago funcion\],\s*103\)\s*>=\s*)'[^']+'",
        rf"\1'{fecha_ini}'",
        query,
    )
    query = re.sub(
        r"(TRY_CONVERT\(DATE,\s*\[Fecha de Pago funcion\],\s*103\)\s*<=\s*)'[^']+'",
        rf"\1'{fecha_fin}'",
        query,
    )
    return query


def obtener_datos(fecha_ini: str, fecha_fin: str) -> pd.DataFrame:
    query = ajustar_rango_fechas(leer_query(SQL_FILE), fecha_ini, fecha_fin)

    conn_url = (
        f"mssql+pyodbc://{common.SQL_USER}:{common.SQL_PASS}@{common.SQL_SERVER}/{common.SQL_DATABASE}?"
        f"driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes"
    )
    engine = sa.create_engine(conn_url)
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(query), conn)

    df.columns = df.columns.str.strip()
    return df


def agregar_anio_mes(df: pd.DataFrame) -> pd.DataFrame:
    fecha = pd.to_datetime(df["Fecha de Pago funcion"], dayfirst=True, errors="coerce")
    df = df.copy()
    df["Año"] = fecha.dt.year
    df["Mes"] = fecha.dt.month
    return df


def rellenar_vacios(df: pd.DataFrame) -> pd.DataFrame:
    """
    El cliente pidió explícitamente que el reporte no tenga celdas vacías,
    en particular en Impuestos, Moneda y otros campos ya usados en pruebas
    previas. Se aplica de forma general: numéricos -> 0, texto -> 'SIN DATO'.
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].fillna(0)
        else:
            df[col] = df[col].fillna("SIN DATO")
            df[col] = df[col].replace(r"^\s*$", "SIN DATO", regex=True)
    return df


def construir_hoja_notas(df: pd.DataFrame, fecha_ini: str, fecha_fin: str) -> pd.DataFrame:
    conteo_bu = df.groupby("BU").size().to_dict()
    conteo_razon = df.groupby("Razón Social Reporte").size().to_dict()
    conteo_terceros = df.groupby("Posible Facturado a Terceros").size().to_dict()

    notas = [
        ("Rango de fechas consultado", f"{fecha_ini} a {fecha_fin}"),
        ("Total de operaciones", str(len(df))),
        ("Operaciones por BU", "; ".join(f"{k}: {v}" for k, v in conteo_bu.items())),
        ("Operaciones por Razón Social (bucket)", "; ".join(f"{k}: {v}" for k, v in conteo_razon.items())),
        ("Posible Facturado a Terceros (heurística)", "; ".join(f"{k}: {v}" for k, v in conteo_terceros.items())),
        ("", ""),
        ("SUPUESTO — Universo de pedimentos",
         "Filtrado por columna Cliente (mismo patrón que Sanofi_V6.sql): "
         "'SANOFI PASTEUR, S.A DE C.V.', 'AZTECA VACUNAS, SA DE CV', "
         "'SANOFI MEXICO S.A. DE C.V.' o Cliente LIKE '%AVENTIS%'. "
         "Verificar que cubra los 4 RFC oficiales solicitados."),
        ("SUPUESTO — BU (CHC/GENMED/INV. CLÍNICA)",
         "Se asume que [EJE UNIDAD DE NEGOCIO] trae esos valores literales. "
         "Filas que no matchean ninguno quedan en 'OTRAS/SIN BU' (columna "
         "'BU Original (sin normalizar)' conserva el valor crudo para revisar)."),
        ("SUPUESTO — Responsable de pago R1/T3",
         "Columna [Recti A Cargo De], solo poblada cuando Clave Pedimento "
         "empieza con 'R'. Confirmar que sus valores sean 'Socio' / 'ABC'."),
        ("SUPUESTO — Operaciones de pacientes / facturadas a terceros",
         "Heurística TENTATIVA basada en [RazonSocial de Proveedores] / "
         "[Facturas] (columna 'Posible Facturado a Terceros'). No se garantiza "
         "que esas columnas representen al facturado real (podrían ser el "
         "proveedor/exportador extranjero) — validar contra casos reales "
         "antes de usarla como clasificación definitiva."),
        ("SUPUESTO — Pedimentos globales/complementarios",
         "No se identificó una Clave de Pedimento específica y confirmada "
         "para 'global' o 'complementario'; no se filtran ni marcan aparte, "
         "quedan incluidos igual que cualquier pedimento (columna "
         "'Clave Pedimento' visible para ubicarlos manualmente)."),
        ("PENDIENTE — Ejercicio 2025 facturado y sin adeudos",
         "No hay un campo en la sábana para validar esto automáticamente; "
         "confirmar manualmente con el equipo de cuentas antes de enviar."),
        ("Celdas vacías", "Se rellenaron: numéricos -> 0, texto -> 'SIN DATO'."),
    ]
    return pd.DataFrame(notas, columns=["Punto", "Detalle"])


def ajustar_ancho_columnas(writer: pd.ExcelWriter, sheet_name: str) -> None:
    ws = writer.sheets[sheet_name]
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)


def guardar_excel(df: pd.DataFrame, output_path: Path, fecha_ini: str, fecha_fin: str) -> Path:
    notas = construir_hoja_notas(df, fecha_ini, fecha_fin)
    df_relleno = rellenar_vacios(df)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        notas.to_excel(writer, sheet_name="Notas y Supuestos", index=False)
        ajustar_ancho_columnas(writer, "Notas y Supuestos")

        df_relleno.to_excel(writer, sheet_name="TODAS", index=False)
        ajustar_ancho_columnas(writer, "TODAS")

        for bu in BU_SHEETS:
            df_bu = df_relleno[df_relleno["BU"] == bu]
            if df_bu.empty:
                continue
            sheet_name = bu[:31]
            df_bu.to_excel(writer, sheet_name=sheet_name, index=False)
            ajustar_ancho_columnas(writer, sheet_name)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera el reporte acumulado de operaciones del Grupo Sanofi (2025-2026)."
    )
    parser.add_argument("--fecha-ini", default=DEFAULT_FECHA_INI, help="Fecha inicial (YYYY-MM-DD).")
    parser.add_argument("--fecha-fin", default=DEFAULT_FECHA_FIN, help="Fecha final (YYYY-MM-DD).")
    args = parser.parse_args()

    print(f"Consultando operaciones Sanofi del {args.fecha_ini} al {args.fecha_fin}...")
    df = obtener_datos(args.fecha_ini, args.fecha_fin)
    if df.empty:
        print("La consulta no devolvió operaciones para ese rango de fechas.")
        return

    df = agregar_anio_mes(df)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = OUTPUT_DIR / f"Sanofi_Acumulado_{args.fecha_ini}_a_{args.fecha_fin}_{timestamp}.xlsx"

    guardar_excel(df, output_path, args.fecha_ini, args.fecha_fin)

    print(f"Reporte generado: {output_path}")
    print(f"Total de operaciones: {len(df)}")
    print("Revisa la hoja 'Notas y Supuestos' antes de compartir el archivo con Sanofi.")


if __name__ == "__main__":
    main()
