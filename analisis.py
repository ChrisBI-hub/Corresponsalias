"""
analisis.py
===========
Analiza los PDFs "PEDIMENTO SIMPLIFICADO" descargados por laredo.py y genera
un Excel con los campos extraídos.

CAMBIO respecto a la versión anterior
--------------------------------------
laredo.py ya no guarda los PDFs en una carpeta plana (antes
Descargas_LT/) — ahora los clasifica en:
    Descargas/{RazonSocial}/{Año}/{Aduana}/{Referencia}/{Referencia}_{Pedimento}_{Tag}.pdf
Este script recorre esa estructura de forma recursiva (os.walk) buscando
PDFs cuyo nombre empiece con una referencia LT.

Dependencias:
    pip install pdfplumber pandas openpyxl
"""

import os
import re
import logging
import pdfplumber
import pandas as pd
from datetime import datetime

import common

PATH_REPORTES = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class AnalizadorLaredo:

    def __init__(self):
        self.resultados = []

    # -------------------------------------------------------------------------
    # HELPER
    # -------------------------------------------------------------------------

    def buscar(self, patron: str, texto: str, grupo: int = 1) -> str:
        """Devuelve el grupo capturado o 'S/D' si no hay coincidencia."""
        m = re.search(patron, texto, re.IGNORECASE | re.DOTALL)
        return m.group(grupo).strip() if m else "S/D"

    # -------------------------------------------------------------------------
    # DESCUBRIMIENTO DE ARCHIVOS (recursivo, sobre la nueva estructura)
    # -------------------------------------------------------------------------

    def encontrar_pdfs_lt(self) -> list[str]:
        """Recorre Descargas/{RazonSocial}/{Año}/LAREDO/{Referencia}/ y
        devuelve las rutas completas de los PDFs de referencias LT."""
        rutas = []
        for raiz, _dirs, archivos in os.walk(common.PATH_DESCARGAS_BASE):
            for nombre in archivos:
                if nombre.lower().endswith(".pdf") and nombre.upper().startswith("LT"):
                    rutas.append(os.path.join(raiz, nombre))
        return sorted(rutas)

    # -------------------------------------------------------------------------
    # EXTRACCIÓN DE CAMPOS
    # -------------------------------------------------------------------------

    def extraer_datos(self, texto: str, nombre_archivo: str) -> dict:
        """
        Campos extraídos y sus patrones (basados en el texto real del PDF):

        FECHAS
        ──────
        ENTRADA: 28/01/2026        →  r'ENTRADA:\\s*(\\d{2}/\\d{2}/\\d{4})'
        PAGO: 28/01/2026           →  r'PAGO:\\s*(\\d{2}/\\d{2}/\\d{4})'

        PEDIMENTO / ADUANA
        ──────────────────
        PEDIMENTO: 6000306         →  r'PEDIMENTO:\\s*(\\d+)'
        ADUANA: 240                →  r'ADUANA:\\s*(\\d+)'
        PATENTE: 3813              →  r'PATENTE:\\s*(\\d+)'

        PAGO
        ────
        IMPORTE PAGADO: $ 91,504.00  →  r'IMPORTE PAGADO:\\s*\\$\\s*([\\d,\\.]+)'
        FECHA DE PAGO: 28/01/2026    →  r'FECHA DE PAGO:\\s*(\\d{2}/\\d{2}/\\d{4})'

        REFERENCIA
        ──────────
        Se extrae directamente del nombre de archivo (formato
        "{Referencia}_{Pedimento}_{Tag}.pdf"), más fiable que buscarla en el texto.
        """

        # — Referencia desde nombre de archivo —
        ref_match = re.match(r"([A-Z]+\d+)", nombre_archivo, re.IGNORECASE)
        referencia = ref_match.group(1).upper() if ref_match else nombre_archivo.replace(".pdf", "")

        # — Fechas —
        fecha_entrada = self.buscar(r'ENTRADA:\s*(\d{2}/\d{2}/\d{4})', texto)
        fecha_pago    = self.buscar(r'\bPAGO:\s*(\d{2}/\d{2}/\d{4})', texto)
        if fecha_pago == "S/D":
            fecha_pago = self.buscar(r'FECHA DE PAGO:\s*(\d{2}/\d{2}/\d{4})', texto)

        # — Pedimento / aduana —
        pedimento = self.buscar(r'PEDIMENTO:\s*(\d[\d\s]*)', texto)
        pedimento = pedimento.replace(" ", "")
        aduana    = self.buscar(r'ADUANA:\s*(\d+)', texto)
        patente   = self.buscar(r'PATENTE:\s*(\d+)', texto)

        # — Importe —
        importe = self.buscar(r'IMPORTE PAGADO:\s*\$\s*([\d,\.]+)', texto)

        # — Descripción de mercancías —
        patron_desc = re.compile(
            r'^\d+\s+\d{8}\s+[\d\s\.\,A-Z]+\n'
            r'([A-ZÁÉÍÓÚÑ][^\n]{3,})',
            re.MULTILINE
        )
        descs = [d.strip() for d in patron_desc.findall(texto)
                 if not re.match(r'^[\d\s\.\,]+$', d)]
        descripcion = " / ".join(descs) if descs else "S/D"

        return {
            "Referencia":          referencia,
            "Pedimento":           pedimento,
            "Aduana":              aduana,
            "Patente":             patente,
            "Fecha Entrada":       fecha_entrada,
            "Fecha Pago":          fecha_pago,
            "Importe Pagado":      importe,
            "Descripcion":         descripcion,
            "Archivo":             nombre_archivo,
        }

    # -------------------------------------------------------------------------
    # LOOP PRINCIPAL
    # -------------------------------------------------------------------------

    def analizar_archivos(self):
        logger.info("🧪 Iniciando análisis de PDFs...")

        rutas = self.encontrar_pdfs_lt()
        if not rutas:
            logger.warning("⚠️  No se encontraron PDFs LT en Descargas/.")
            return None

        logger.info(f"📂 {len(rutas)} archivo(s) encontrado(s).")

        for ruta in rutas:
            archivo = os.path.basename(ruta)
            logger.info(f"📄 Analizando: {archivo}")

            try:
                with pdfplumber.open(ruta) as pdf:
                    texto = "\n".join(p.extract_text() or "" for p in pdf.pages)

                datos = self.extraer_datos(texto, archivo)
                self.resultados.append(datos)

                logger.info(
                    f"   ✔ Pedimento: {datos['Pedimento']} | "
                    f"Fecha Pago: {datos['Fecha Pago']} | "
                    f"Importe: {datos['Importe Pagado']}"
                )

            except Exception as e:
                logger.error(f"❌ Error analizando {archivo}: {e}")

        return self.generar_excel()

    # -------------------------------------------------------------------------
    # GENERAR EXCEL
    # -------------------------------------------------------------------------

    def generar_excel(self):
        if not self.resultados:
            logger.error("No se extrajeron datos de ningún PDF.")
            return None

        df = pd.DataFrame(self.resultados, columns=[
            "Referencia", "Pedimento", "Aduana", "Patente",
            "Fecha Entrada", "Fecha Pago", "Importe Pagado",
            "Descripcion", "Archivo"
        ])

        timestamp    = datetime.now().strftime("%Y%m%d_%H%M")
        nombre_excel = f"ANALISIS_LT_{timestamp}.xlsx"
        ruta_final   = os.path.join(PATH_REPORTES, nombre_excel)

        with pd.ExcelWriter(ruta_final, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Pedimentos")

            ws = writer.sheets["Pedimentos"]
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

        logger.info(f"✅ Reporte generado: {ruta_final}")
        logger.info(f"   Filas: {len(df)} | Columnas: {len(df.columns)}")
        return df


# =============================================================================
if __name__ == "__main__":
    AnalizadorLaredo().analizar_archivos()
