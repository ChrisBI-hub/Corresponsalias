"""
main.py
=======
Punto de entrada del proyecto de respaldo de documentos digitales
(Corresponsalias / SANOFI).

Flujo:
  1. Ejecuta Sanofi_V6.sql tal cual está (sin tocar el WHERE ni las fechas).
  2. De los resultados, separa las referencias LT (Laredo) y MN (Manzanillo);
     el resto (AÉREO, MARÍTIMO, CORRESPONSALIAS) se ignora porque no tiene
     portal que extraer.
  3. Arma la metadata de cada referencia (Cliente, Año, Aduana, Pedimento)
     que usan laredo.py y manzanillo.py para nombrar y clasificar cada
     archivo descargado.
  4. Las referencias de SANOFI MEXICO (sin credenciales todavía en el
     portal Laredo) se registran en faltantes_sanofi_mexico.txt con el
     formato "<Referencia>-SM" y se omiten de la descarga.
  5. Ejecuta laredo.py (una corrida por cada razón social/credencial) y
     manzanillo.py (cuenta única) — cada uno guarda sus archivos ya
     clasificados en:
         Descargas/{RazonSocial}/{Año}/{Aduana}/REF-{Referencia} - PEDIMENTO {Pedimento}/{tipo_documento}.ext
  6. Al final escribe REPORTE_DESCARGAS_{fecha_hora}.xlsx con qué
     referencias se descargaron correctamente y cuáles no (y por qué).
"""

import logging

import common
from laredo import LaredoExtractor
from manzanillo import ManzanilloExtractor

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main():
    logger.info("📡 Ejecutando consulta Sanofi_V6.sql...")
    df = common.ejecutar_query_v6()
    logger.info(f"✅ {len(df)} fila(s) obtenidas de la consulta.")

    metadata, grupos_laredo, grupos_manzanillo, faltantes_sm = common.construir_metadata_y_grupos(df)

    total_laredo = sum(len(v) for v in grupos_laredo.values())
    logger.info(f"📦 Laredo: {total_laredo} referencia(s) en {len(grupos_laredo)} razón(es) social(es).")
    for razon, refs in grupos_laredo.items():
        logger.info(f"   - {razon}: {len(refs)} referencia(s)")
    logger.info(f"📦 Manzanillo: {len(grupos_manzanillo)} referencia(s).")

    if faltantes_sm:
        common.guardar_faltantes_sm(faltantes_sm)

    resultados_laredo = []
    resultados_manzanillo = []

    if grupos_laredo:
        logger.info("\n🚚 Iniciando descarga — LAREDO")
        resultados_laredo = LaredoExtractor().procesar(grupos_laredo, metadata)
    else:
        logger.info("Sin referencias de Laredo este periodo.")

    if grupos_manzanillo:
        logger.info("\n🚢 Iniciando descarga — MANZANILLO")
        resultados_manzanillo = ManzanilloExtractor(headless=False).procesar(grupos_manzanillo, metadata)
    else:
        logger.info("Sin referencias de Manzanillo este periodo.")

    resultados_faltantes_sm = [common.resultado_faltante_sm(ref, metadata) for ref in faltantes_sm]
    todos_resultados = resultados_laredo + resultados_manzanillo + resultados_faltantes_sm
    common.escribir_reporte_excel(todos_resultados)

    logger.info(
        "\n🎉 Proceso completo. Revisa la carpeta Descargas/, el reporte "
        "REPORTE_DESCARGAS_*.xlsx y faltantes_sanofi_mexico.txt (si se generó)."
    )


if __name__ == "__main__":
    main()
