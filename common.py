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
        Descargas/{RazonSocial}/{Año}/{Aduana}/{Referencia}/{Referencia}_{Pedimento}_{Tag}.ext
    Crea las carpetas intermedias si no existen. Si el nombre ya existe,
    agrega un sufijo numérico para no sobrescribir.
    """
    razon   = sanear_nombre(meta.get("cliente"))
    anio    = sanear_nombre(meta.get("anio") or "SIN_ANIO")
    aduana  = sanear_nombre(meta.get("aduana"))
    carpeta = os.path.join(PATH_DESCARGAS_BASE, razon, anio, aduana, referencia)
    os.makedirs(carpeta, exist_ok=True)

    pedimento  = sanear_nombre(meta.get("pedimento") or "SINPEDIMENTO")
    tag_limpio = re.sub(r"[^A-Za-z0-9]+", "_", tag or "DOCUMENTO").strip("_") or "DOCUMENTO"
    ext = extension if extension.startswith(".") else f".{extension}"

    destino = os.path.join(carpeta, f"{referencia}_{pedimento}_{tag_limpio}{ext}")

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
        meta = {
            "cliente":   cliente,
            "aduana":    fila.get("Tipo Sucursal"),
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
