"""
laredo.py
=========
Descarga TODOS los documentos disponibles de cada referencia LT desde
SLAM.Digital y los guarda ya clasificados en:

    Descargas/{RazonSocial}/{Año}/{Aduana}/REF-{Referencia} - PEDIMENTO {Pedimento}/{tipo_documento}.ext

Las referencias y su metadata (Cliente, Año, Aduana, Pedimento) las resuelve
main.py a partir de Sanofi_V6.sql. Este script ya NO consulta SQL por su
cuenta cuando se ejecuta desde main.py — solo recibe grupos + metadata y
descarga.

Estrategia de descarga (por cada documento encontrado en la carpeta de la
referencia):
  1. Navegar a ConsultaRefGrupo.aspx?...&ref=LT...
  2. Localizar TODOS los bloques de documento disponibles (div.G000) — cada
     referencia puede traer decenas (PEDIMENTO COMPLETO, FOTO MERCANCIA,
     FACTURA, COVE, DODA, EDOCUMENT, XML de cuenta de gastos, etc.), NO
     solo PDFs: también hay imágenes (jpeg), XML y TXT.
  3. Por cada uno: abrir VisorB.aspx con Selenium -> genera el archivo en
     /tmp/ del servidor.
  4. Extraer la URL /tmp/{id}.{ext}: normalmente del botón "Abrir" dentro
     del Visor; algunos tipos (visto en XML, también aplica a PDF/TXT) no
     traen ese botón — en ese caso el archivo real ya está en el "src" del
     <iframe> que lo muestra en pantalla, y se toma de ahí.
  5. Descargar el archivo con requests reutilizando cookies de Selenium,
     usando el "content_type" real que ya viene en la URL del documento
     (pdf/jpeg/xml/txt) — antes se asumía PDF para todo y se descartaban
     silenciosamente los demás tipos (bug que dejaba "solo un documento").

En corridas largas (cientos de referencias en una sola sesión de Firefox)
la sesión de SLAM.Digital puede expirar a mitad de camino: el portal
redirige a la pantalla de login en vez de mostrar ConsultaRefGrupo.aspx,
lo que antes se reportaba erróneamente como "sin documentos" para esa
referencia Y para todas las siguientes (nunca se detectaba ni se
reautenticaba). Ahora pagina_actual_es_login() detecta esa redirección y
reautenticar() vuelve a iniciar sesión con las mismas credenciales antes
de reintentar esa referencia.

Dependencias:
    pip install selenium sqlalchemy pyodbc pandas requests
    GeckoDriver: sudo apt install firefox-esr geckodriver
"""

import os
import re
import time
import logging
import requests
from urllib.parse import urlparse, parse_qs, quote
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

import common

URL_LOGIN = "https://slamnldo.alvelais.mx/slamdigital4/default.aspx"
URL_BASE  = "http://slamnldo.alvelais.mx/slamdigital4"

# El tile de cada documento en ConsultaRefGrupo.aspx trae su propio
# "content_type" en la URL del VisorB (ej. ...&content_type=jpeg&...).
# Ese valor manda sobre cualquier suposición: ya NO se asume que todo es PDF.
EXTENSIONES_POR_CONTENT_TYPE = {
    "pdf":  ".pdf",
    "jpeg": ".jpg",
    "jpg":  ".jpg",
    "png":  ".png",
    "xml":  ".xml",
    "txt":  ".txt",
}

# ConsultaRefGrupo.aspx puede tardar bastante en pintar los bloques de
# documento cuando la referencia tiene muchos (visto: >35s para referencias
# que sí tenían documentos). Se usa un timeout dedicado y más generoso solo
# para esta espera, con un reintento (recargar la página) antes de darla
# por vacía — para no confundir "está lenta" con "no tiene documentos".
TIMEOUT_PAGINA_REFERENCIA = 90

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class LaredoExtractor:

    def __init__(self):
        self.driver  = None
        self.wait    = None
        self.uid     = None
        self.perfil  = None
        self.session = requests.Session()
        self._usuario_actual = None
        self._contra_actual  = None

    # -------------------------------------------------------------------------
    # DRIVER
    # -------------------------------------------------------------------------

    def configurar_driver(self):
        # Este Firefox nunca necesita descargar nada por sí mismo: el
        # archivo real se obtiene con requests (ver descargar_documento).
        # Antes se configuraba browser.download.dir + pdfjs.disabled + una
        # lista neverAsk.saveToDisk que incluía "application/pdf" — eso
        # hacía que Firefox guardara en silencio, en _tmp_descargas, una
        # copia de cada PDF que VisorB.aspx mostraba en la pestaña
        # (efecto secundario no deseado: ese archivo nunca se usaba ni se
        # limpiaba). Se quita esa configuración por completo.
        options = webdriver.FirefoxOptions()
        self.driver = webdriver.Firefox(options=options)
        self.wait   = WebDriverWait(self.driver, 35)
        logger.info("🦊 Firefox iniciado.")

    # -------------------------------------------------------------------------
    # LOGIN + PARÁMETROS DE SESIÓN
    # -------------------------------------------------------------------------

    def ejecutar_login(self, usuario: str, contra: str):
        logger.info(f"🔑 Iniciando sesión en SLAM.Digital como '{usuario}'...")
        self.driver.get(URL_LOGIN)
        self.wait.until(EC.presence_of_element_located((By.ID, "uname"))).send_keys(usuario)
        campo_pass = self.driver.find_element(By.ID, "unamep")
        campo_pass.send_keys(contra)
        campo_pass.send_keys(Keys.ENTER)
        self.wait.until(EC.presence_of_element_located((By.CLASS_NAME, "navbar-brand")))
        logger.info("✅ Login exitoso.")

    def leer_parametros_sesion(self):
        try:
            self.wait.until(EC.presence_of_element_located((By.ID, "txtarg4")))
        except Exception:
            self.driver.get(f"{URL_BASE}/Defaultb.aspx")
            self.wait.until(EC.presence_of_element_located((By.ID, "txtarg4")))

        self.uid    = self.driver.find_element(By.ID, "txtarg4").get_attribute("value")
        self.perfil = self.driver.find_element(By.ID, "txtarg7").get_attribute("value")
        logger.info(f"🔑 Sesión — uid: {self.uid} | perfil: {self.perfil}")

    def sincronizar_cookies(self):
        self.session.cookies.clear()
        for cookie in self.driver.get_cookies():
            self.session.cookies.set(cookie["name"], cookie["value"])

    def pagina_actual_es_login(self) -> bool:
        """
        Detecta si el driver quedó en la pantalla de login de SLAM.Digital
        (Default.aspx) en vez de en la página que se pidió — señal de que
        la sesión expiró a mitad de la corrida. El formulario de login
        siempre trae el campo #uname; ConsultaRefGrupo.aspx nunca lo tiene.
        """
        try:
            return bool(self.driver.find_elements(By.ID, "uname"))
        except Exception:
            return False

    def reautenticar(self, ref: str) -> bool:
        """
        Vuelve a iniciar sesión con las credenciales de la razón social que
        se está procesando (guardadas por procesar_razon_social) y relee
        uid/perfil. Se usa cuando pagina_actual_es_login() detecta que la
        sesión expiró a mitad de una corrida larga.
        """
        if not self._usuario_actual or not self._contra_actual:
            logger.error(f"   [{ref}] ❌ No hay credenciales guardadas para reautenticar.")
            return False
        logger.warning(f"   [{ref}] ⚠ La sesión de SLAM.Digital expiró; reautenticando...")
        try:
            self.ejecutar_login(self._usuario_actual, self._contra_actual)
            self.leer_parametros_sesion()
            return True
        except Exception as e:
            logger.error(f"   [{ref}] ❌ No se pudo reautenticar: {e}")
            return False

    def capturar_diagnostico(self, nombre: str):
        """
        Guarda un screenshot + el HTML de la página actual en _debug/ dentro
        de PROYECTO_BASE. Úsalo cuando un selector no encuentra el elemento
        esperado: manda esos dos archivos para ajustar el XPath exacto.
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

    # -------------------------------------------------------------------------
    # PROCESAR UNA REFERENCIA (TODOS los documentos, de cualquier tipo)
    # -------------------------------------------------------------------------

    def obtener_todos_los_documentos(self, ref: str) -> list[dict]:
        """
        Navega a la página de resultados de la referencia y devuelve una
        lista de dicts {etiqueta, extension, href} para TODOS los
        documentos disponibles en esa carpeta (PDF, imágenes, XML, TXT).

        El content_type real de cada documento viene en la propia URL de
        VisorB.aspx (parámetro "content_type"), así que se lee de ahí en
        vez de asumir que todo es PDF.

        `ref` siempre llega como una referencia individual: cuando el SQL
        trae varias combinadas con "/" (ej. "LT2696671/LT2696668"),
        common.construir_metadata_y_grupos() ya las separó antes de armar
        los grupos que procesa este script.
        """
        token = "AutoScriptABC1234"

        def _url_ref():
            # uid/perfil pueden cambiar si hubo que reautenticar a mitad
            # de la corrida, así que se arma de nuevo en cada intento.
            return (
                f"{URL_BASE}/ConsultaRefGrupo.aspx"
                f"?token={token}&ref={quote(ref, safe='')}&p={self.perfil}&uid={self.uid}"
            )

        espera_larga = WebDriverWait(self.driver, TIMEOUT_PAGINA_REFERENCIA)
        encontrado = False
        intentos_carga = 0   # solo cuenta los reintentos por lentitud, no las reautenticaciones
        intentos_reauth = 0  # tope de seguridad: nunca reautenticar en bucle sin fin
        while intentos_carga < 2:
            sufijo = " (reintento — la primera carga tardó demasiado)" if intentos_carga else ""
            logger.info(f"   [{ref}] Cargando página de resultados{sufijo}...")
            self.driver.get(_url_ref())

            if self.pagina_actual_es_login():
                intentos_reauth += 1
                if intentos_reauth > 2:
                    logger.error(f"   [{ref}] ❌ Sigue cayendo en el login tras reautenticar {intentos_reauth - 1} vez(es). Se omite.")
                    break
                if not self.reautenticar(ref):
                    break
                continue  # reintenta la misma carga ya reautenticado, sin gastar un intento

            try:
                espera_larga.until(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'G000')]"))
                )
                encontrado = True
                break
            except Exception:
                intentos_carga += 1
                if intentos_carga < 2:
                    logger.warning(
                        f"   [{ref}] ⚠ La página tardó más de {TIMEOUT_PAGINA_REFERENCIA}s en "
                        f"cargar los documentos; recargando para reintentar una vez..."
                    )

        if not encontrado:
            logger.warning(f"   [{ref}] ⚠ No se encontraron bloques de documentos en la carpeta (tras reintentar).")
            self.capturar_diagnostico(f"sin_documentos_{common.sanear_nombre(ref)}")
            return []

        # El primer bloque en aparecer no garantiza que ya estén todos
        # pintados si la carga es progresiva; un margen corto evita perder
        # documentos por leer la lista a medio renderizar.
        time.sleep(1.5)
        bloques = self.driver.find_elements(By.XPATH, "//div[contains(@class,'G000')]")
        documentos = []
        for bloque in bloques:
            try:
                enlace = bloque.find_element(By.XPATH, ".//a[contains(@href,'VisorB.aspx')]")
                href = enlace.get_attribute("href")
            except Exception:
                continue  # bloque sin documento cargado para ese tipo

            if not href:
                continue

            parametros = parse_qs(urlparse(href).query)
            doctypename = (parametros.get("doctypename", [""])[0] or "").strip()
            content_type = (parametros.get("content_type", [""])[0] or "").strip().lower()

            if not doctypename:
                # Respaldo: si el link no trae doctypename (portal cambió el
                # formato), se usa el texto visible del bloque.
                try:
                    doctypename = bloque.find_element(By.XPATH, ".//span").text.strip()
                except Exception:
                    doctypename = "DOCUMENTO"

            extension = EXTENSIONES_POR_CONTENT_TYPE.get(content_type, ".pdf")

            documentos.append({"etiqueta": doctypename or "DOCUMENTO", "extension": extension, "href": href})

        etiquetas = [d["etiqueta"] for d in documentos]
        logger.info(f"   [{ref}] {len(documentos)} documento(s) encontrado(s): {etiquetas}")
        return documentos

    def abrir_visor_y_obtener_url_archivo(self, ref: str, etiqueta: str, visor_href: str) -> str | None:
        """Abre VisorB.aspx en una nueva pestaña para forzar la generación del archivo."""
        self.driver.execute_script("window.open(arguments[0], '_visor');", visor_href)
        self.driver.switch_to.window(self.driver.window_handles[-1])

        try:
            try:
                btn_abrir = self.wait.until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//a[contains(@class,'btn-info') and .//span[text()='Abrir']]")
                    )
                )
                url_archivo = btn_abrir.get_attribute("href")
                logger.info(f"   [{ref}] [{etiqueta}] Archivo generado en: {url_archivo}")
                return url_archivo
            except Exception:
                pass

            # Algunos tipos de documento (confirmado con XML "EDOCUMENT ACUSE
            # XML"; el mismo visor lo usa también para PDF/TXT/otros, según
            # SucceededCallback1 en el HTML de VisorB) no traen botón "Abrir":
            # el archivo se muestra directo en un <iframe> cuyo "src" YA es
            # la URL real y descargable del archivo
            # (https://slamnldo.alvelais.mx/slamdigital4/tmp/{id}.{ext}).
            # Ese <iframe> viene renderizado en el HTML inicial de la página
            # (no por AJAX), así que basta una espera corta.
            try:
                espera_corta = WebDriverWait(self.driver, 8)
                iframe = espera_corta.until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//div[@id='hs_show_file_for_category']//iframe")
                    )
                )
                url_archivo = iframe.get_attribute("src")
                if url_archivo:
                    logger.info(f"   [{ref}] [{etiqueta}] Archivo (visor sin botón 'Abrir') en: {url_archivo}")
                    return url_archivo
            except Exception:
                pass

            logger.error(f"   [{ref}] [{etiqueta}] ❌ No se encontró el botón 'Abrir' ni el iframe del visor en VisorB.")
            nombre_ref = common.sanear_nombre(ref).replace(" ", "_")
            nombre_etq = re.sub(r'[^A-Za-z0-9]+', '_', etiqueta)
            self.capturar_diagnostico(f"visor_sin_boton_abrir_{nombre_ref}_{nombre_etq}")
            return None

        finally:
            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])

    def descargar_documento(self, ref: str, meta: dict, etiqueta: str, extension: str, url_archivo: str) -> bool:
        """Descarga el documento (de cualquier tipo) con requests y lo guarda ya clasificado."""
        destino = common.construir_ruta_destino(meta, ref, etiqueta, extension)
        self.sincronizar_cookies()

        try:
            logger.info(f"   [{ref}] [{etiqueta}] Descargando ({extension})...")
            resp = self.session.get(url_archivo, timeout=60, stream=True)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                # Un archivo real nunca vuelve como text/html; esto sí es
                # síntoma de sesión expirada / redirección al login.
                logger.error(
                    f"   [{ref}] [{etiqueta}] ❌ Respuesta HTML en vez del archivo. "
                    f"Posible redirección al login — las cookies pueden haber expirado."
                )
                return False

            with open(destino, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            common.extraer_zip_si_aplica(destino)

            tamanio_kb = os.path.getsize(destino) / 1024
            logger.info(f"   [{ref}] [{etiqueta}] ✅ Guardado: {destino} ({tamanio_kb:.1f} KB)")
            return True

        except Exception as e:
            logger.error(f"   [{ref}] [{etiqueta}] ❌ Error al descargar: {e}")
            return False

    def _resultado_base(self, ref: str, meta: dict) -> dict:
        return {
            "Referencia": ref,
            "RazonSocial": meta.get("cliente"),
            "Portal": "LAREDO",
            "Aduana": meta.get("aduana"),
            "Anio": meta.get("anio"),
            "Pedimento": meta.get("pedimento"),
            "DocumentosEncontrados": 0,
            "DocumentosDescargados": 0,
            "Estado": "ERROR",
            "Detalle": "",
        }

    def procesar_referencia(self, ref: str, meta: dict) -> dict:
        resultado = self._resultado_base(ref, meta)
        try:
            documentos = self.obtener_todos_los_documentos(ref)
            resultado["DocumentosEncontrados"] = len(documentos)
            if not documentos:
                logger.warning(f"   [{ref}] Sin documentos disponibles. Se omite.")
                resultado["Estado"] = "SIN_DOCUMENTOS"
                resultado["Detalle"] = "No se encontraron documentos en el portal para esta referencia."
                return resultado

            descargados = 0
            for doc in documentos:
                etiqueta  = doc["etiqueta"]
                extension = doc["extension"]
                try:
                    url_archivo = self.abrir_visor_y_obtener_url_archivo(ref, etiqueta, doc["href"])
                    if not url_archivo:
                        logger.warning(f"   [{ref}] [{etiqueta}] Sin URL de archivo. Se omite.")
                        continue
                    if self.descargar_documento(ref, meta, etiqueta, extension, url_archivo):
                        descargados += 1
                except Exception as e:
                    logger.error(f"   [{ref}] [{etiqueta}] ⚠ Error procesando documento: {e}")
                    while len(self.driver.window_handles) > 1:
                        self.driver.switch_to.window(self.driver.window_handles[-1])
                        self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
                    time.sleep(1)

            resultado["DocumentosDescargados"] = descargados
            if descargados == len(documentos):
                resultado["Estado"] = "OK"
            elif descargados > 0:
                resultado["Estado"] = "PARCIAL"
                resultado["Detalle"] = f"{descargados}/{len(documentos)} documentos descargados."
            else:
                resultado["Estado"] = "ERROR"
                resultado["Detalle"] = "Se encontraron documentos pero ninguno se pudo descargar."

            logger.info(f"   [{ref}] ✅ {descargados}/{len(documentos)} documento(s) descargado(s).")
            return resultado

        except Exception as e:
            logger.error(f"⚠️  Error procesando [{ref}]: {e}")
            resultado["Detalle"] = str(e)
            while len(self.driver.window_handles) > 1:
                self.driver.switch_to.window(self.driver.window_handles[-1])
                self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
            time.sleep(2)
            return resultado

    # -------------------------------------------------------------------------
    # EJECUCIÓN MAESTRA
    # -------------------------------------------------------------------------

    def procesar_razon_social(self, razon: str, referencias: list[str], metadata: dict, on_resultado=None) -> list[dict]:
        resultados = []
        credenciales = common.CREDENCIALES_LAREDO.get(razon)
        if not credenciales or not credenciales.get("usuario") or not credenciales.get("contra"):
            logger.error(f"❌ Razón social '{razon}' sin credenciales configuradas (revisa .env). Se omite.")
            for ref in referencias:
                meta = metadata.get(ref, {})
                r = self._resultado_base(ref, meta)
                r["RazonSocial"] = meta.get("cliente", razon)
                r["Estado"] = "SIN_CREDENCIALES"
                r["Detalle"] = f"Sin credenciales configuradas para '{razon}' (revisa .env)."
                resultados.append(r)
                if on_resultado:
                    on_resultado(r)
            return resultados

        self._usuario_actual = credenciales["usuario"]
        self._contra_actual  = credenciales["contra"]

        self.configurar_driver()
        try:
            self.ejecutar_login(credenciales["usuario"], credenciales["contra"])
            self.leer_parametros_sesion()

            total = len(referencias)
            for i, ref in enumerate(referencias, start=1):
                meta = metadata.get(ref)
                if not meta:
                    logger.warning(f"   [{ref}] ⚠ Sin metadata (no viene de la consulta SQL). Se omite.")
                    r = self._resultado_base(ref, {})
                    r["Estado"] = "SIN_METADATA"
                    r["Detalle"] = "La referencia no viene en la consulta SQL."
                    resultados.append(r)
                    if on_resultado:
                        on_resultado(r)
                    continue
                logger.info(f"\n{'='*55}")
                logger.info(f"  [{razon}] [{i}/{total}]  {ref}")
                logger.info(f"{'='*55}")
                resultado = self.procesar_referencia(ref, meta)
                resultados.append(resultado)
                if on_resultado:
                    on_resultado(resultado)

            logger.info(f"\n🎉 [{razon}] Todas sus referencias procesadas.")

        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None
                logger.info(f"🏁 [{razon}] Firefox cerrado.")

        return resultados

    def procesar(self, grupos: dict, metadata: dict, on_resultado=None) -> list[dict]:
        """
        Punto de entrada usado por main.py.
        grupos = {clave_credencial: [Referencia, ...]}
        on_resultado(resultado_dict): callback opcional invocado justo después de
        procesar CADA referencia (antes de pasar a la siguiente) — usado por
        main.py para ir guardando el log de procesadas en disco de inmediato,
        así una corrida larga no pierde el progreso si se interrumpe a medias.
        Devuelve la lista de resultados (uno por referencia) para el reporte.
        """
        resultados = []
        if not grupos:
            logger.info("No hay referencias de Laredo para procesar.")
            return resultados

        for razon, referencias in grupos.items():
            if not referencias:
                continue
            resultados.extend(self.procesar_razon_social(razon, referencias, metadata, on_resultado))

        logger.info("\n🎉 Todas las razones sociales (Laredo) procesadas.")
        return resultados


# =============================================================================
if __name__ == "__main__":
    # Ejecución independiente (sin main.py): arma su propia metadata desde SQL.
    df = common.ejecutar_query_v6()
    metadata, grupos_laredo, _, faltantes_sm = common.construir_metadata_y_grupos(df)
    common.guardar_faltantes_sm(faltantes_sm)
    resultados = LaredoExtractor().procesar(grupos_laredo, metadata)
    common.escribir_reporte_excel(resultados)
