"""
laredo.py
=========
Descarga TODOS los documentos disponibles de cada referencia LT desde
SLAM.Digital y los guarda ya clasificados en:

    Descargas/{RazonSocial}/{Año}/{Aduana}/{Referencia}/{Referencia}_{Pedimento}_{Tag}.pdf

Las referencias y su metadata (Cliente, Año, Aduana, Pedimento) las resuelve
main.py a partir de Sanofi_V6.sql. Este script ya NO consulta SQL por su
cuenta cuando se ejecuta desde main.py — solo recibe grupos + metadata y
descarga.

Estrategia de descarga (por cada documento encontrado en la carpeta de la
referencia):
  1. Navegar a ConsultaRefGrupo.aspx?...&ref=LT...
  2. Localizar TODOS los bloques de documento disponibles (div.G000)
  3. Por cada uno: abrir VisorB.aspx con Selenium  -> genera el PDF en /tmp/ del servidor
  4. Extraer la URL /tmp/{id}.pdf del botón "Abrir" dentro del Visor
  5. Descargar el PDF con requests reutilizando cookies actualizadas de Selenium

Dependencias:
    pip install selenium sqlalchemy pyodbc pandas requests
    GeckoDriver: sudo apt install firefox-esr geckodriver
"""

import os
import time
import logging
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

import common

URL_LOGIN = "https://slamnldo.alvelais.mx/slamdigital4/default.aspx"
URL_BASE  = "http://slamnldo.alvelais.mx/slamdigital4"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class LaredoExtractor:

    def __init__(self):
        self.driver  = None
        self.wait    = None
        self.uid     = None
        self.perfil  = None
        self.session = requests.Session()

    # -------------------------------------------------------------------------
    # DRIVER
    # -------------------------------------------------------------------------

    def configurar_driver(self):
        options = webdriver.FirefoxOptions()
        options.set_preference("browser.download.folderList", 2)
        options.set_preference("browser.download.dir", common.PATH_TEMP_DESCARGAS)
        options.set_preference(
            "browser.helperApps.neverAsk.saveToDisk",
            "application/pdf,application/zip,application/octet-stream"
        )
        options.set_preference("pdfjs.disabled", True)
        options.set_preference("browser.download.manager.showWhenStarting", False)
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

    # -------------------------------------------------------------------------
    # PROCESAR UNA REFERENCIA (TODOS los documentos)
    # -------------------------------------------------------------------------

    def obtener_todos_los_documentos(self, ref: str) -> list[tuple[str, str]]:
        """
        Navega a la página de resultados de la referencia y devuelve una
        lista de (etiqueta, href_visor) para TODOS los documentos
        disponibles en esa carpeta.
        """
        token   = "AutoScriptABC1234"
        url_ref = (
            f"{URL_BASE}/ConsultaRefGrupo.aspx"
            f"?token={token}&ref={ref}&p={self.perfil}&uid={self.uid}"
        )
        logger.info(f"   [{ref}] Cargando página de resultados...")
        self.driver.get(url_ref)

        try:
            self.wait.until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class,'G000')]"))
            )
        except Exception:
            logger.warning(f"   [{ref}] ⚠ No se encontraron bloques de documentos en la carpeta.")
            return []

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

            try:
                etiqueta = bloque.find_element(By.XPATH, ".//span").text.strip()
            except Exception:
                etiqueta = "DOCUMENTO"

            documentos.append((etiqueta or "DOCUMENTO", href))

        etiquetas = [d[0] for d in documentos]
        logger.info(f"   [{ref}] {len(documentos)} documento(s) encontrado(s): {etiquetas}")
        return documentos

    def abrir_visor_y_obtener_url_pdf(self, ref: str, etiqueta: str, visor_href: str) -> str | None:
        """Abre VisorB.aspx en una nueva pestaña para forzar la generación del PDF."""
        self.driver.execute_script("window.open(arguments[0], '_visor');", visor_href)
        self.driver.switch_to.window(self.driver.window_handles[-1])

        try:
            btn_abrir = self.wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, "//a[contains(@class,'btn-info') and .//span[text()='Abrir']]")
                )
            )
            url_pdf = btn_abrir.get_attribute("href")
            logger.info(f"   [{ref}] [{etiqueta}] PDF generado en: {url_pdf}")
            return url_pdf

        except Exception:
            logger.error(f"   [{ref}] [{etiqueta}] ❌ No se encontró el botón 'Abrir' en VisorB.")
            return None

        finally:
            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])

    def descargar_pdf(self, ref: str, meta: dict, etiqueta: str, url_pdf: str) -> bool:
        """Descarga el PDF con requests y lo guarda ya clasificado."""
        destino = common.construir_ruta_destino(meta, ref, etiqueta, ".pdf")
        self.sincronizar_cookies()

        try:
            logger.info(f"   [{ref}] [{etiqueta}] Descargando PDF...")
            resp = self.session.get(url_pdf, timeout=60, stream=True)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type and "octet" not in content_type:
                logger.error(
                    f"   [{ref}] [{etiqueta}] ❌ Respuesta inesperada ({content_type}). "
                    f"Posible redirección al login — las cookies pueden haber expirado."
                )
                return False

            with open(destino, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            tamanio_kb = os.path.getsize(destino) / 1024
            logger.info(f"   [{ref}] [{etiqueta}] ✅ Guardado: {destino} ({tamanio_kb:.1f} KB)")
            return True

        except Exception as e:
            logger.error(f"   [{ref}] [{etiqueta}] ❌ Error al descargar: {e}")
            return False

    def procesar_referencia(self, ref: str, meta: dict):
        try:
            documentos = self.obtener_todos_los_documentos(ref)
            if not documentos:
                logger.warning(f"   [{ref}] Sin documentos disponibles. Se omite.")
                return

            for etiqueta, visor_href in documentos:
                try:
                    url_pdf = self.abrir_visor_y_obtener_url_pdf(ref, etiqueta, visor_href)
                    if not url_pdf:
                        logger.warning(f"   [{ref}] [{etiqueta}] Sin URL de PDF. Se omite.")
                        continue
                    self.descargar_pdf(ref, meta, etiqueta, url_pdf)
                except Exception as e:
                    logger.error(f"   [{ref}] [{etiqueta}] ⚠ Error procesando documento: {e}")
                    while len(self.driver.window_handles) > 1:
                        self.driver.switch_to.window(self.driver.window_handles[-1])
                        self.driver.close()
                    self.driver.switch_to.window(self.driver.window_handles[0])
                    time.sleep(1)

        except Exception as e:
            logger.error(f"⚠️  Error procesando [{ref}]: {e}")
            while len(self.driver.window_handles) > 1:
                self.driver.switch_to.window(self.driver.window_handles[-1])
                self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
            time.sleep(2)

    # -------------------------------------------------------------------------
    # EJECUCIÓN MAESTRA
    # -------------------------------------------------------------------------

    def procesar_razon_social(self, razon: str, referencias: list[str], metadata: dict):
        credenciales = common.CREDENCIALES_LAREDO.get(razon)
        if not credenciales or not credenciales.get("usuario") or not credenciales.get("contra"):
            logger.error(f"❌ Razón social '{razon}' sin credenciales configuradas (revisa .env). Se omite.")
            return

        self.configurar_driver()
        try:
            self.ejecutar_login(credenciales["usuario"], credenciales["contra"])
            self.leer_parametros_sesion()

            total = len(referencias)
            for i, ref in enumerate(referencias, start=1):
                meta = metadata.get(ref)
                if not meta:
                    logger.warning(f"   [{ref}] ⚠ Sin metadata (no viene de la consulta SQL). Se omite.")
                    continue
                logger.info(f"\n{'='*55}")
                logger.info(f"  [{razon}] [{i}/{total}]  {ref}")
                logger.info(f"{'='*55}")
                self.procesar_referencia(ref, meta)

            logger.info(f"\n🎉 [{razon}] Todas sus referencias procesadas.")

        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None
                logger.info(f"🏁 [{razon}] Firefox cerrado.")

    def procesar(self, grupos: dict, metadata: dict):
        """
        Punto de entrada usado por main.py.
        grupos = {clave_credencial: [Referencia, ...]}
        """
        if not grupos:
            logger.info("No hay referencias de Laredo para procesar.")
            return

        for razon, referencias in grupos.items():
            if not referencias:
                continue
            self.procesar_razon_social(razon, referencias, metadata)

        logger.info("\n🎉 Todas las razones sociales (Laredo) procesadas.")


# =============================================================================
if __name__ == "__main__":
    # Ejecución independiente (sin main.py): arma su propia metadata desde SQL.
    df = common.ejecutar_query_v6()
    metadata, grupos_laredo, _, faltantes_sm = common.construir_metadata_y_grupos(df)
    common.guardar_faltantes_sm(faltantes_sm)
    LaredoExtractor().procesar(grupos_laredo, metadata)
