"""
manzanillo.py  (antes "oñate_extraccion.py" — renombrado sin ñ por
                portabilidad de import/filesystem en Linux)
=============================================================================
Descarga TODOS los archivos digitales disponibles por referencia en el
portal de Manzanillo (OWCIA / satoWeb) y los guarda ya clasificados en:

    Descargas/{RazonSocial}/{Año}/{Aduana}/{Referencia}/{Referencia}_{Pedimento}_{Tag}.ext

Clasificaciones descargadas por cada referencia:
  - GASTOS COMPROBADOS
  - Expediente aduanal (CASAWIN)              código base '7777|<id>|0'
  - Expediente aduanal (CASAWIN) Expedientes    código base '7777|<id>|10'  (descarga el ZIP)
  - 7. Proforma glosada                         código base '4|<id>|5'
  - 11. DODAs                                   código base '4|<id>|15'

El '<id>' de expediente NO es fijo: lo genera el portal para cada
referencia consultada, así que se extrae dinámicamente del árbol de
documentos (onclick="CargarDocumentosPorClasificacion('7777|<id>|0')")
cada vez que se abre una referencia. Ver obtener_id_expediente().

Navegación por referencia (según el diagrama de flujo real de Oñate):
  1. Login (sin menús intermedios: no hay un botón "Aduana").
  2. Por cada referencia: cuadro de búsqueda rápida (#txtValorRapido) +
     ObtenerConsultaRapida() — NO el buscador por rango de fechas, que es
     para cuando todavía no se sabe si la referencia existe en el portal.
  3. Click en el ícono "Mostrar documentos" de la fila resultante.
  4. GASTOS COMPROBADOS / EXPEDIENTES (CASAWIN) / CONTROL INTERNO
     (Proforma glosada, DODAs) ya visibles en el árbol de clasificación.

Las referencias y su metadata (Cliente, Año, Aduana, Pedimento) las resuelve
main.py a partir de Sanofi_V6.sql. Este script ya NO consulta SQL por su
cuenta cuando se ejecuta desde main.py.

NOTA
----
No fue posible probar este script contra el portal real (requiere sesión
autenticada). La navegación de búsqueda rápida y clasificaciones se basa
en el diagrama de flujo real de Oñate que compartió el usuario. Revisa los
puntos marcados con "# VERIFICAR" la primera vez que corras cada paso, y
si algo no encuentra el elemento esperado revisa _debug/ (ver
capturar_diagnostico) para ajustar el selector exacto.

Dependencias:
    pip install selenium
"""

import os
import re
import time
import shutil
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import common

TIMEOUT_DESCARGA = 60

# (prefijo, sufijo, etiqueta_para_log, tag_para_nombre_de_archivo)
# El código real que recibe CargarDocumentosPorClasificacion() se arma como
# f"{prefijo}|{id_expediente}|{sufijo}" con el id extraído en vivo.
# Confirmado contra el árbol jstree real (#trvDocumentos) de una referencia:
# EXPEDIENTES (7777|id|0 y 7777|id|10) y, dentro de CONTROL INTERNO, 6
# sub-clasificaciones — no solo Proforma glosada y DODAs.
CLASIFICACIONES_DOCUMENTOS = [
    ("7777", "0",  "Expediente aduanal (CASAWIN)",               "CASAWIN"),
    ("7777", "10", "Expediente aduanal (CASAWIN) - Expedientes",  "EXPEDIENTES"),
    ("4",    "4",  "5. Reporte previo",                           "REPORTE_PREVIO"),
    ("4",    "5",  "7. Proforma glosada",                         "PROFORMA_GLOSADA"),
    ("4",    "6",  "8. EIR",                                      "EIR"),
    ("4",    "7",  "9. Corte demoras",                            "CORTE_DEMORAS"),
    ("4",    "12", "10. MV y HC",                                 "MV_HC"),
    ("4",    "15", "11. DODAs",                                   "DODA"),
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ManzanilloExtractor:

    def __init__(self, headless=False):
        self.headless = headless
        self.driver = None
        self.wait = None

    # -------------------------------------------------------------------------
    # DRIVER / LOGIN
    # -------------------------------------------------------------------------

    def esperar_bloqueo(self):
        try:
            self.wait.until(EC.invisibility_of_element_located((By.CLASS_NAME, "blockUI")))
            self.wait.until(EC.invisibility_of_element_located((By.CLASS_NAME, "blockOverlay")))
            time.sleep(0.5)
        except Exception:
            pass

    def configurar_driver(self):
        options = webdriver.FirefoxOptions()
        if self.headless:
            options.add_argument("--headless")
        options.set_preference("browser.download.folderList", 2)
        options.set_preference("browser.download.dir", common.PATH_TEMP_DESCARGAS)
        options.set_preference(
            "browser.helperApps.neverAsk.saveToDisk",
            "application/zip,application/pdf,application/xml,text/xml,application/octet-stream"
        )
        options.set_preference("pdfjs.disabled", True)
        options.set_preference("browser.download.manager.showWhenStarting", False)
        self.driver = webdriver.Firefox(options=options)
        self.wait = WebDriverWait(self.driver, 25)

    def capturar_diagnostico(self, nombre: str):
        """
        Guarda un screenshot + el HTML de la página actual en _debug/ dentro
        de PROYECTO_BASE. Úsalo cuando un selector no encuentra el elemento
        esperado: manda esos dos archivos para ajustar el XPath/ID exacto.
        """
        carpeta = os.path.join(common.PATH_PROYECTO_BASE, "_debug")
        os.makedirs(carpeta, exist_ok=True)
        try:
            self.driver.save_screenshot(os.path.join(carpeta, f"{nombre}.png"))
            with open(os.path.join(carpeta, f"{nombre}.html"), "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logger.error(f"🩺 Diagnóstico guardado en {carpeta}/{nombre}.png y {nombre}.html")
        except Exception as e:
            logger.error(f"No se pudo guardar diagnóstico '{nombre}': {e}")

    def ejecutar_login(self):
        """
        Solo el login. La búsqueda por rango de fechas (Aduana > Fecha >
        Buscar) del portal es para cuando todavía no se sabe si una
        referencia existe en Oñate; como nosotros ya conocemos exactamente
        qué referencias buscar (vienen del SQL), usamos el cuadro de
        búsqueda rápida por cada una — ver buscar_referencia_rapida().
        """
        logger.info("🔑 Iniciando sesión en OWCIA (Oñate)...")
        if not common.USUARIO_OWCIA or not common.CONTRA_OWCIA:
            raise RuntimeError("Faltan OWCIA_USER / OWCIA_PASS en el entorno (.env).")

        self.driver.get("https://portal.owcia.com/owcia/satoWeb/Login.html")
        try:
            self.wait.until(EC.presence_of_element_located((By.ID, "usuario"))).send_keys(common.USUARIO_OWCIA)
            self.driver.find_element(By.ID, "pass").send_keys(common.CONTRA_OWCIA)
            self.driver.find_element(By.CSS_SELECTOR, "input[value='Accesar']").click()
        except Exception:
            self.capturar_diagnostico("login_fallo")
            raise
        self.esperar_bloqueo()
        time.sleep(2)

    def buscar_referencia_rapida(self, referencia: str) -> bool:
        """
        Busca una referencia puntual con el cuadro de búsqueda rápida
        (input#txtValorRapido + ObtenerConsultaRapida()), tal como lo
        documenta el flujo real de Oñate. Devuelve True si la búsqueda
        encontró resultados (aparece el ícono "Mostrar documentos").
        """
        # El panel de la referencia anterior puede tardar en cerrarse del
        # todo (cerrar_panel_documentos es "best effort"); esperar aquí
        # reduce el riesgo de que el campo no sea interactuable todavía.
        self.esperar_bloqueo()

        try:
            campo = self.wait.until(EC.element_to_be_clickable((By.ID, "txtValorRapido")))
        except Exception:
            self.capturar_diagnostico("sin_txtValorRapido")
            raise

        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", campo)
            campo.clear()
            campo.send_keys(referencia)
        except Exception:
            # Respaldo: si el campo sigue sin ser interactuable de forma
            # normal (ej. un overlay residual de la referencia anterior),
            # se fuerza el valor por JS disparando los eventos que el
            # portal espera, en vez de tronar la referencia completa.
            logger.warning(
                f"   [{referencia}] ⚠ Campo de búsqueda no interactuable directamente; "
                f"se usa JS de respaldo."
            )
            self.driver.execute_script(
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                campo, referencia
            )

        self.driver.execute_script("ObtenerConsultaRapida();")
        self.esperar_bloqueo()
        time.sleep(2)

        try:
            self.wait.until(EC.presence_of_element_located((By.XPATH, "//img[@title='Mostrar documentos']")))
            return True
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # id de expediente dinámico (cambia por cada referencia consultada)
    # -------------------------------------------------------------------------

    def obtener_id_expediente(self):
        """
        Extrae el id de expediente (segundo segmento del código que recibe
        CargarDocumentosPorClasificacion) desde cualquier nodo del árbol de
        documentos ya visible para la referencia actual. Este id lo genera
        el portal en automático al abrir la referencia — nunca es fijo.

        CONFIRMADO contra el HTML real del portal: el onclick trae un
        espacio antes del paréntesis de cierre, ej.
        onclick="CargarDocumentosPorClasificacion('7777|400127633|0' )"
        — por eso el regex permite \s* ahí; sin eso nunca hacía match y
        el id de expediente salía None en el 100% de las referencias.
        """
        patron = re.compile(r"CargarDocumentosPorClasificacion\('(\d+)\|(\d+)\|(\d+)'\s*\)")
        for _ in range(3):
            nodos = self.driver.find_elements(
                By.XPATH, "//a[contains(@onclick,'CargarDocumentosPorClasificacion')]"
            )
            for nodo in nodos:
                onclick = nodo.get_attribute("onclick") or ""
                m = patron.search(onclick)
                if m:
                    return m.group(2)
            time.sleep(1)
        return None

    # -------------------------------------------------------------------------
    # Descarga genérica: dispara en carpeta temporal y mueve ya clasificado
    # -------------------------------------------------------------------------

    def archivos_en_temp(self):
        return set(os.listdir(common.PATH_TEMP_DESCARGAS))

    def advertir_sobrantes_temp(self):
        """
        Revisa _tmp_descargas al iniciar: si ya hay archivos ahí, son
        sobrantes de una corrida anterior que no se alcanzaron a mover a su
        carpeta clasificada (por un timeout o un error a medio proceso).
        No se borran solos —se advierte para que se revisen a mano— porque
        podrían ser útiles para no perder esa descarga.
        """
        try:
            sobrantes = sorted(os.listdir(common.PATH_TEMP_DESCARGAS))
        except FileNotFoundError:
            sobrantes = []
        if sobrantes:
            logger.warning(
                f"⚠️  {len(sobrantes)} archivo(s) sin clasificar en {common.PATH_TEMP_DESCARGAS} "
                f"(de una corrida anterior que no los pudo mover a tiempo): {sobrantes}"
            )
            logger.warning(
                "    Revísalos a mano: si son descargas válidas, muévelos tú mismo a la "
                "carpeta de la referencia correspondiente en Descargas/."
            )

    def esperar_descarga_temp(self, archivos_previos, timeout=TIMEOUT_DESCARGA):
        """
        Espera a que aparezca un archivo NUEVO y completo (no .part/.tmp) en
        la carpeta temporal de descargas del navegador. Devuelve su ruta o
        None si se agota el tiempo (en ese caso el archivo se queda en
        _tmp_descargas sin clasificar — ver advertir_sobrantes_temp).
        """
        limite = time.time() + timeout
        while time.time() < limite:
            actuales = set(os.listdir(common.PATH_TEMP_DESCARGAS))
            candidatos = [f for f in (actuales - archivos_previos)
                          if not f.endswith(('.part', '.tmp', '.crdownload'))]
            if candidatos:
                ruta = os.path.join(common.PATH_TEMP_DESCARGAS, candidatos[0])
                try:
                    tam1 = os.path.getsize(ruta)
                    time.sleep(0.8)
                    tam2 = os.path.getsize(ruta)
                except FileNotFoundError:
                    time.sleep(0.5)
                    continue
                if tam1 == tam2:
                    return ruta
            time.sleep(0.5)
        return None

    def mover_a_clasificacion(self, ruta_temp: str, meta: dict, referencia: str, tag: str) -> str:
        ext = os.path.splitext(ruta_temp)[1] or ".bin"
        destino = common.construir_ruta_destino(meta, referencia, tag, ext)
        shutil.move(ruta_temp, destino)
        return destino

    def descargar_por_clasificacion(self, referencia: str, meta: dict, codigo: str, etiqueta: str, tag: str) -> int:
        """
        Llama directamente a la función JS CargarDocumentosPorClasificacion(codigo)
        (equivalente a hacer click en el nodo del árbol) y descarga TODOS los
        archivos que aparezcan en la tabla resultante (#lstDocumentos).

        # VERIFICAR: se asume que el grid siempre usa el id 'lstDocumentos' y
        # la columna de descarga 'lstDocumentos_act', igual que el bloque de
        # CASAWIN ya validado.
        """
        logger.info(f"   [{referencia}] 📂 Cargando '{etiqueta}' (código {codigo})...")
        try:
            self.driver.execute_script(f"CargarDocumentosPorClasificacion('{codigo}');")
            self.esperar_bloqueo()
            time.sleep(1.5)

            self.wait.until(EC.presence_of_element_located((By.XPATH, "//table[@id='lstDocumentos']")))
            filas = self.driver.find_elements(
                By.XPATH,
                "//table[@id='lstDocumentos']/tbody/tr[@role='row' and not(contains(@class,'jqgfirstrow'))]"
            )
        except Exception:
            logger.info(f"   [{referencia}] — Sin documentos en '{etiqueta}'.")
            return 0

        total_filas = len(filas)
        if total_filas == 0:
            logger.info(f"   [{referencia}] — '{etiqueta}' sin archivos.")
            return 0

        descargados = 0
        for idx in range(total_filas):
            try:
                # Re-localizar en cada iteración: el grid puede re-renderizarse
                filas_actuales = self.driver.find_elements(
                    By.XPATH,
                    "//table[@id='lstDocumentos']/tbody/tr[@role='row' and not(contains(@class,'jqgfirstrow'))]"
                )
                fila = filas_actuales[idx]
                btn_descargar = fila.find_element(
                    By.XPATH, ".//td[@aria-describedby='lstDocumentos_act']//img[@title='Descargar']"
                )
                previos = self.archivos_en_temp()
                self.driver.execute_script("arguments[0].click();", btn_descargar)

                ruta_temp = self.esperar_descarga_temp(previos)
                if ruta_temp:
                    destino = self.mover_a_clasificacion(ruta_temp, meta, referencia, tag)
                    logger.info(f"   [{referencia}] ✅ Guardado: {destino}")
                    descargados += 1
                else:
                    logger.warning(
                        f"   [{referencia}] ⚠ Fila {idx+1}/{total_filas} de '{etiqueta}': no se detectó "
                        f"descarga en {TIMEOUT_DESCARGA}s. Si llega tarde, puede aparecer sin clasificar "
                        f"en {common.PATH_TEMP_DESCARGAS} — revisa ahí."
                    )

            except Exception as e:
                logger.error(f"   [{referencia}] ❌ Error en fila {idx+1} de '{etiqueta}': {e}")

        logger.info(f"   [{referencia}] ✅ {descargados}/{total_filas} archivo(s) de '{etiqueta}' descargado(s).")
        return descargados

    def descargar_gastos_comprobados(self, referencia: str, meta: dict) -> int:
        try:
            btn_gastos = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'GASTOS COMPROBADOS')]")))
            self.driver.execute_script("arguments[0].click();", btn_gastos)
            time.sleep(2)
        except Exception:
            logger.info(f"   [{referencia}] — Sin 'GASTOS COMPROBADOS'.")
            return 0

        filas_gastos = self.driver.find_elements(
            By.XPATH,
            "//table[@id='lstDocumentosGastos']/tbody/tr[@role='row' and not(contains(@class,'jqgfirstrow'))]"
        )
        descargados = 0
        for fila in filas_gastos:
            try:
                btn_desc = fila.find_element(By.XPATH, ".//td[@aria-describedby='lstDocumentosGastos_act']//img")
                previos = self.archivos_en_temp()
                self.driver.execute_script("arguments[0].click();", btn_desc)
                ruta_temp = self.esperar_descarga_temp(previos)
                if ruta_temp:
                    destino = self.mover_a_clasificacion(ruta_temp, meta, referencia, "GASTOS")
                    logger.info(f"   [{referencia}] ✅ Guardado: {destino}")
                    descargados += 1
                else:
                    logger.warning(
                        f"   [{referencia}] ⚠ No se detectó descarga de un gasto comprobado en "
                        f"{TIMEOUT_DESCARGA}s. Si llega tarde, puede aparecer sin clasificar en "
                        f"{common.PATH_TEMP_DESCARGAS} — revisa ahí."
                    )
            except Exception as e:
                logger.error(f"   [{referencia}] ❌ Error descargando gasto comprobado: {e}")

        return descargados

    # -------------------------------------------------------------------------
    # DESCARGA POR REFERENCIA (Gastos + las 4 clasificaciones)
    # -------------------------------------------------------------------------

    def _resultado_base(self, ref: str, meta: dict) -> dict:
        return {
            "Referencia": ref,
            "RazonSocial": meta.get("cliente"),
            "Portal": "MANZANILLO",
            "Aduana": meta.get("aduana"),
            "Anio": meta.get("anio"),
            "Pedimento": meta.get("pedimento"),
            "DocumentosEncontrados": 0,
            "DocumentosDescargados": 0,
            "Estado": "ERROR",
            "Detalle": "",
        }

    def cerrar_panel_documentos(self, ref: str):
        """
        Cierra el panel de documentos de la referencia actual. No es crítico:
        si falla (ej. ElementClickIntercepted por un overlay que tarda en
        desaparecer), no debe tumbar el resultado de la referencia — las
        descargas ya se hicieron antes de este paso.
        """
        try:
            self.driver.find_element(By.XPATH, "//span[contains(@class, 'ui-icon-closethick')]").click()
            time.sleep(1)
        except Exception as e:
            logger.warning(f"   [{ref}] ⚠ No se pudo cerrar el panel de documentos (no crítico): {e}")

    def descargar_expedientes(self, referencias: list[str], metadata: dict) -> list[dict]:
        resultados = []
        for ref in referencias:
            meta = metadata.get(ref)
            if not meta:
                logger.warning(f"   [{ref}] ⚠ Sin metadata (no viene de la consulta SQL). Se omite.")
                r = self._resultado_base(ref, {})
                r["Estado"] = "SIN_METADATA"
                r["Detalle"] = "La referencia no viene en la consulta SQL."
                resultados.append(r)
                continue

            resultado = self._resultado_base(ref, meta)
            nombre_ref = common.sanear_nombre(ref).replace(" ", "_")
            logger.info(f"📦 Procesando Referencia: {ref}")
            try:
                if not self.buscar_referencia_rapida(ref):
                    logger.warning(f"   [{ref}] ⚠ Sin resultados en la búsqueda rápida. Se omite.")
                    self.capturar_diagnostico(f"sin_resultado_{nombre_ref}")
                    resultado["Estado"] = "SIN_DOCUMENTOS"
                    resultado["Detalle"] = "Sin resultados en la búsqueda rápida del portal."
                    resultados.append(resultado)
                    continue

                btn_mostrar = self.driver.find_element(By.XPATH, "//img[@title='Mostrar documentos']")
                self.driver.execute_script("arguments[0].click();", btn_mostrar)
                self.esperar_bloqueo()
                time.sleep(1)

                # El id de expediente se lee ANTES de tocar Gastos Comprobados:
                # se sospecha que el árbol de clasificación deja de ser
                # accesible en el DOM una vez que se hace clic en Gastos.
                id_expediente = self.obtener_id_expediente()

                descargados_gastos = self.descargar_gastos_comprobados(ref, meta)

                descargados_clasif = 0
                if not id_expediente:
                    logger.warning(
                        f"   [{ref}] ⚠ No se pudo determinar el id de expediente; "
                        f"se omiten CASAWIN/EXPEDIENTES/Proforma/DODA."
                    )
                    self.capturar_diagnostico(f"sin_id_expediente_{nombre_ref}")
                    resultado["Detalle"] = "No se pudo determinar el id de expediente (CASAWIN/EXPEDIENTES/Proforma/DODA omitidos)."
                else:
                    for prefijo, sufijo, etiqueta, tag in CLASIFICACIONES_DOCUMENTOS:
                        codigo = f"{prefijo}|{id_expediente}|{sufijo}"
                        descargados_clasif += self.descargar_por_clasificacion(ref, meta, codigo, etiqueta, tag)

                total_descargados = descargados_gastos + descargados_clasif
                resultado["DocumentosDescargados"] = total_descargados
                resultado["DocumentosEncontrados"] = total_descargados
                if total_descargados > 0 and id_expediente:
                    resultado["Estado"] = "OK"
                elif total_descargados > 0:
                    resultado["Estado"] = "PARCIAL"
                    resultado["Detalle"] = (resultado["Detalle"] + " Solo se descargó Gastos Comprobados.").strip()
                else:
                    resultado["Estado"] = "SIN_DOCUMENTOS"

                self.cerrar_panel_documentos(ref)

            except Exception as e:
                logger.error(f"❌ Error en {ref}: {e}")
                resultado["Detalle"] = (resultado["Detalle"] + f" Error: {e}").strip()
                self.capturar_diagnostico(f"error_referencia_{nombre_ref}")
                try:
                    self.driver.execute_script("document.querySelector('.ui-icon-closethick').click();")
                except Exception:
                    pass

            resultados.append(resultado)

        return resultados

    # -------------------------------------------------------------------------
    # PUNTO DE ENTRADA
    # -------------------------------------------------------------------------

    def procesar(self, referencias: list[str], metadata: dict) -> list[dict]:
        """Punto de entrada usado por main.py. Devuelve resultados por referencia."""
        if not referencias:
            logger.info("No hay referencias de Manzanillo para procesar.")
            return []
        self.advertir_sobrantes_temp()
        try:
            self.configurar_driver()
            self.ejecutar_login()
            return self.descargar_expedientes(referencias, metadata)
        finally:
            if self.driver is not None:
                self.driver.quit()
                self.driver = None
            logger.info("🎉 Manzanillo: proceso finalizado.")


# =============================================================================
if __name__ == "__main__":
    # Ejecución independiente (sin main.py): arma su propia metadata desde SQL.
    df = common.ejecutar_query_v6()
    metadata, _, grupos_manzanillo, _ = common.construir_metadata_y_grupos(df)
    resultados = ManzanilloExtractor(headless=False).procesar(grupos_manzanillo, metadata)
    common.escribir_reporte_excel(resultados)
