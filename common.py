"""
common.py
=========
Configuración y utilidades compartidas por main.py, laredo.py y manzanillo.py:

  - Conexión a SQL Server y ejecución de la consulta maestra (Sanofi_V6.sql),
    tal cual está, sin modificar su WHERE ni sus fechas.
  - Mapeo Cliente -> credencial del portal Laredo (SLAM.Digital).
  - Credenciales del portal Manzanillo (OWCIA) — cuenta única de acceso general.
  - Construcción de la ruta de clasificación acordada:
        Descargas/{RazonSocial}/{Año}/{Aduana}/{Referencia}/{Referencia}_{Pedimento}_{Tag}.ext

Todas las credenciales se leen de variables de entorno (ver .env.example).
No hay contraseñas escritas en este archivo a propósito: este repo se
sube a GitHub y las credenciales de portales/SQL no deben quedar en el
historial de git.
"""

import os
import re
import logging
from datetime import datetime

import pandas as pd
import sqlalchemy as sa

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv es opcional; si no está instalado, exporta las vars manualmente

logger = logging.getLogger(__name__)

# =============================================================================
# RUTAS DEL PROYECTO
# =============================================================================

PATH_PROYECTO_BASE  = os.getenv("PROYECTO_BASE", "/home/christian/Documentos/Corresponsalias")
PATH_DESCARGAS_BASE = os.path.join(PATH_PROYECTO_BASE, "Descargas")
PATH_TEMP_DESCARGAS = os.path.join(PATH_PROYECTO_BASE, "_tmp_descargas")  # carpeta de descarga de Firefox
RUTA_FALTANTES_SM   = os.path.join(PATH_PROYECTO_BASE, "faltantes_sanofi_mexico.txt")
RUTA_SQL            = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Sanofi_V6.sql")

os.makedirs(PATH_DESCARGAS_BASE, exist_ok=True)
os.makedirs(PATH_TEMP_DESCARGAS, exist_ok=True)

# =============================================================================
# SQL SERVER
# =============================================================================

SQL_SERVER   = os.getenv("SIR_SQL_SERVER")
SQL_DATABASE = os.getenv("SIR_SQL_DATABASE", "SIR")
SQL_USER     = os.getenv("SIR_SQL_USER")
SQL_PASS     = os.getenv("SIR_SQL_PASS")

# =============================================================================
# CREDENCIALES LAREDO (SLAM.Digital) por razón social
# =============================================================================

CREDENCIALES_LAREDO = {
    "PASTEUR": {
        "usuario": os.getenv("LAREDO_PASTEUR_USER"),
        "contra":  os.getenv("LAREDO_PASTEUR_PASS"),
    },
    "AZVA2025": {
        "usuario": os.getenv("LAREDO_AZVA2025_USER"),
        "contra":  os.getenv("LAREDO_AZVA2025_PASS"),
    },
    "AVENTIS": {
        "usuario": os.getenv("LAREDO_AVENTIS_USER"),
        "contra":  os.getenv("LAREDO_AVENTIS_PASS"),
    },
}

# Mapeo directo Cliente (columna SQL) -> clave de credencial Laredo.
# 'SANOFI MEXICO S.A. DE C.V.' se deja en None a propósito: todavía no
# tiene clave en el portal (ver guardar_faltantes_sm).
CLIENTE_A_CLAVE = {
    "SANOFI PASTEUR, S.A DE C.V.": "PASTEUR",
    "AZTECA VACUNAS, SA DE CV":    "AZVA2025",
    "SANOFI MEXICO S.A. DE C.V.":  None,
}


def clave_credencial(cliente: str, unidad_negocio: str) -> str | None:
    """Resuelve la clave de credencial Laredo para un Cliente de la consulta."""
    if cliente in CLIENTE_A_CLAVE:
        return CLIENTE_A_CLAVE[cliente]
    if cliente and "AVENTIS" in cliente.upper() and (unidad_negocio or "").upper().startswith("GENMED"):
        return "AVENTIS"
    return None


# =============================================================================
# CREDENCIALES MANZANILLO (OWCIA) — cuenta única, acceso general
# =============================================================================

USUARIO_OWCIA = os.getenv("OWCIA_USER")
CONTRA_OWCIA  = os.getenv("OWCIA_PASS")


# =============================================================================
# CLASIFICACIÓN DE ARCHIVOS DESCARGADOS
# =============================================================================

def sanear_nombre(texto) -> str:
    """Limpia un texto para usarlo como nombre de carpeta/archivo en Linux."""
    texto = str(texto if texto not in (None, "nan") else "SIN_DATO").strip()
    texto = texto.replace("/", "-")
    texto = re.sub(r"\s+", " ", texto)
    return texto or "SIN_DATO"


def construir_ruta_destino(meta: dict, referencia: str, tag: str, extension: str) -> str:
    """
    Devuelve la ruta final del archivo según la clasificación acordada:
        Descargas/{RazonSocial}/{Año}/{Aduana}/REF-{Referencia} - PEDIMENTO {Pedimento}/{tag}.ext

    Ejemplo:
        Descargas/SANOFI MEXICO/2026/470 - NUEVO LAREDO/REF-ABC123 - PEDIMENTO 12345678/pedimento_completo.pdf

    Crea las carpetas intermedias si no existen. Si el nombre de archivo ya
    existe en esa carpeta (p.ej. varias fotos), agrega un sufijo numérico.
    """
    razon     = sanear_nombre(meta.get("cliente"))
    anio      = sanear_nombre(meta.get("anio") or "SIN_ANIO")
    aduana    = sanear_nombre(meta.get("aduana"))
    pedimento = sanear_nombre(meta.get("pedimento") or "SINPEDIMENTO")
    carpeta_ref = sanear_nombre(f"REF-{referencia} - PEDIMENTO {pedimento}")

    carpeta = os.path.join(PATH_DESCARGAS_BASE, razon, anio, aduana, carpeta_ref)
    os.makedirs(carpeta, exist_ok=True)

    tag_limpio = re.sub(r"[^A-Za-z0-9]+", "_", tag or "DOCUMENTO").strip("_").lower() or "documento"
    ext = extension if extension.startswith(".") else f".{extension}"

    destino = os.path.join(carpeta, f"{tag_limpio}{ext}")

    base, ext_final = os.path.splitext(destino)
    contador = 1
    while os.path.exists(destino):
        destino = f"{base}_{contador}{ext_final}"
        contador += 1

    return destino


# =============================================================================
# CONSULTA MAESTRA — se ejecuta UNA sola vez desde main.py
# =============================================================================

def ejecutar_query_v6() -> pd.DataFrame:
    """Ejecuta Sanofi_V6.sql tal cual está y devuelve el DataFrame completo."""
    with open(RUTA_SQL, "r", encoding="utf-8") as f:
        query_texto = f.read()

    conn_url = (
        f"mssql+pyodbc://{SQL_USER}:{SQL_PASS}@{SQL_SERVER}/{SQL_DATABASE}?"
        f"driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes"
    )
    engine = sa.create_engine(conn_url)
    with engine.connect() as conn:
        df = pd.read_sql(sa.text(query_texto), conn)
    return df


def _extraer_anio(fecha_pago_str) -> int | None:
    """[Fecha de Pago funcion] viene como 'dd/mm/yyyy' (CONVERT ... 103)."""
    if not fecha_pago_str or not isinstance(fecha_pago_str, str):
        return None
    try:
        return datetime.strptime(fecha_pago_str, "%d/%m/%Y").year
    except ValueError:
        return None


def construir_metadata_y_grupos(df: pd.DataFrame):
    """
    A partir del DataFrame de la consulta V6, arma:
      - metadata:          {Referencia: {cliente, aduana, anio, pedimento}}
      - grupos_laredo:      {clave_credencial: [Referencia, ...]}
      - grupos_manzanillo:  [Referencia, ...]
      - faltantes_sm:       [Referencia, ...]  (SANOFI MEXICO sin credenciales)

    Solo procesa referencias LT (Laredo) o MN (Manzanillo); el resto
    (AÉREO, MARÍTIMO, CORRESPONSALIAS) no tiene portal que extraer.
    """
    metadata: dict = {}
    grupos_laredo: dict = {}
    grupos_manzanillo: list = []
    faltantes_sm: list = []

    df = df.drop_duplicates(subset=["Referencia"])

    for _, fila in df.iterrows():
        ref = str(fila.get("Referencia") or "").strip()
        if not ref:
            continue

        ref_upper = ref.upper()
        es_laredo     = ref_upper.startswith("LT")
        es_manzanillo = ref_upper.startswith("MN")
        if not (es_laredo or es_manzanillo):
            continue

        cliente = fila.get("Cliente")
        # Carpeta de aduana con código + nombre (ej. "470 - NUEVO LAREDO"),
        # tal como lo pidió el usuario. Si [Aduana/Sección Despacho] viene
        # vacío, se usa [Tipo Sucursal] (LAREDO/MANZANILLO) como respaldo.
        aduana = fila.get("Aduana/Sección Despacho")
        if aduana is None or (isinstance(aduana, float) and pd.isna(aduana)) or str(aduana).strip() == "":
            aduana = fila.get("Tipo Sucursal")

        meta = {
            "cliente":   cliente,
            "aduana":    aduana,
            "anio":      _extraer_anio(fila.get("Fecha de Pago funcion")),
            "pedimento": fila.get("Pedimento"),
        }
        metadata[ref] = meta

        if es_laredo:
            clave = clave_credencial(cliente, fila.get("Unidad de negocio"))
            if clave is None:
                if cliente == "SANOFI MEXICO S.A. DE C.V.":
                    faltantes_sm.append(ref)
                continue
            grupos_laredo.setdefault(clave, []).append(ref)
        else:
            grupos_manzanillo.append(ref)

    return metadata, grupos_laredo, grupos_manzanillo, faltantes_sm


def guardar_faltantes_sm(referencias: list) -> None:
    """
    Registra en un .txt las referencias de SANOFI MEXICO que no se pudieron
    descargar por falta de credenciales en el portal Laredo, con el formato
    solicitado "<Referencia>-SM", para llevar la cuenta de cuántas faltan
    en cuanto agreguen esa clave al servidor del portal.
    """
    if not referencias:
        return
    with open(RUTA_FALTANTES_SM, "w", encoding="utf-8") as f:
        f.write("Referencias de SANOFI MEXICO S.A. DE C.V. sin credenciales en el portal SLAM.Digital\n")
        f.write("Formato: <Referencia>-SM\n")
        f.write("=" * 70 + "\n")
        for ref in sorted(set(referencias)):
            f.write(f"{ref}-SM\n")
    logger.warning(
        f"⚠️  {len(referencias)} referencia(s) de SANOFI MEXICO sin clave de portal. "
        f"Guardadas en: {RUTA_FALTANTES_SM}"
    )


# =============================================================================
# REPORTE FINAL — qué referencias se descargaron y cuáles no
# =============================================================================

COLUMNAS_REPORTE = [
    "Referencia", "RazonSocial", "Portal", "Aduana", "Anio", "Pedimento",
    "DocumentosEncontrados", "DocumentosDescargados", "Estado", "Detalle",
]


def resultado_faltante_sm(ref: str, metadata: dict) -> dict:
    """Arma la fila de reporte para una referencia de SANOFI MEXICO sin credenciales."""
    meta = metadata.get(ref, {})
    return {
        "Referencia": ref,
        "RazonSocial": meta.get("cliente", "SANOFI MEXICO S.A. DE C.V."),
        "Portal": "LAREDO",
        "Aduana": meta.get("aduana"),
        "Anio": meta.get("anio"),
        "Pedimento": meta.get("pedimento"),
        "DocumentosEncontrados": 0,
        "DocumentosDescargados": 0,
        "Estado": "SIN_CREDENCIALES",
        "Detalle": "SANOFI MEXICO sin credenciales en el portal Laredo (SLAM.Digital).",
    }


def escribir_reporte_excel(resultados: list) -> str | None:
    """
    Genera un Excel con el resultado de cada referencia procesada (Laredo +
    Manzanillo + SANOFI MEXICO sin credenciales): una hoja "Resumen" con el
    conteo por portal/estado y una hoja "Detalle" con una fila por
    referencia (documentos encontrados/descargados, estado, detalle del
    error si aplica). Devuelve la ruta del archivo generado, o None si no
    hubo resultados que reportar.
    """
    if not resultados:
        logger.warning("⚠️  No hay resultados que reportar; no se generó el Excel.")
        return None

    df = pd.DataFrame(resultados, columns=COLUMNAS_REPORTE)

    resumen = (
        df.groupby(["Portal", "Estado"], dropna=False)
          .size()
          .reset_index(name="Cantidad")
          .sort_values(["Portal", "Estado"])
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    ruta = os.path.join(PATH_PROYECTO_BASE, f"REPORTE_DESCARGAS_{timestamp}.xlsx")

    with pd.ExcelWriter(ruta, engine="openpyxl") as writer:
        resumen.to_excel(writer, index=False, sheet_name="Resumen")
        df.to_excel(writer, index=False, sheet_name="Detalle")

        for nombre_hoja in ("Resumen", "Detalle"):
            ws = writer.sheets[nombre_hoja]
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

    total_ok = int((df["Estado"] == "OK").sum())
    logger.info(f"✅ Reporte de descargas generado: {ruta} ({total_ok}/{len(df)} referencia(s) OK)")
    return ruta
