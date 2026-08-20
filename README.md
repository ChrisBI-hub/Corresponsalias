# Corresponsalias — Respaldo de expedientes digitales (SANOFI)

Descarga y clasifica localmente los documentos de los portales de
corresponsalías (Laredo / SLAM.Digital y Manzanillo / OWCIA) a partir de
las referencias que arroja la consulta `Sanofi_V6.sql`.

## Estructura

```
main.py         orquestador: corre la consulta, separa LT/MN, dispara laredo.py / manzanillo.py
common.py       config, credenciales (desde .env), consulta SQL, clasificación de rutas
laredo.py       descarga todos los documentos de cada referencia LT (SLAM.Digital)
manzanillo.py   descarga CASAWIN/EXPEDIENTES/Proforma glosada/DODAs/Gastos de cada referencia MN (OWCIA)
analisis.py     analiza los PDF de Laredo ya descargados y genera un Excel
Sanofi_V6.sql   consulta maestra (se ejecuta tal cual, sin modificar WHERE ni fechas)
```

## Clasificación de archivos descargados

```
Descargas/{RazonSocial}/{Año}/{Aduana}/REF-{Referencia} - PEDIMENTO {Pedimento}/{tipo_documento}.ext
```

Ejemplo:

```
Descargas/SANOFI MEXICO S.A. DE C.V./2026/470 - NUEVO LAREDO/REF-LT2591286 - PEDIMENTO 12345678/
    pedimento_completo.pdf
    foto_mercancia.jpeg
    foto_mercancia_1.jpeg
    cove_acuse_xml.xml
    ...
```

- `RazonSocial`: nombre completo del `Cliente` tal como aparece en el WHERE del SQL.
- `Año`: año de `[Fecha de Pago funcion]`.
- `Aduana`: columna `[Aduana/Sección Despacho]` (código + nombre, ej. `470 - NUEVO LAREDO`); si viene vacía se usa `[Tipo Sucursal]` (`LAREDO`/`MANZANILLO`) como respaldo.
- `Pedimento`: viene directo de la columna `Pedimento` de la consulta.
- `tipo_documento`: nombre del documento tal como lo entrega el portal (ej. `PEDIMENTO COMPLETO`, `FOTO MERCANCIA`, `CASAWIN`, `PROFORMA_GLOSADA`, `DODA`, `GASTOS`), en minúsculas y con guiones bajos. Si hay varios del mismo tipo en la misma referencia (ej. varias fotos), se numeran automáticamente (`_1`, `_2`, ...).

Las referencias de **SANOFI MEXICO S.A. DE C.V.** que no tengan credenciales
en el portal Laredo no se descargan; se registran en
`faltantes_sanofi_mexico.txt` con el formato `<Referencia>-SM`.

## Puesta en marcha

1. `pip install -r requirements.txt`
2. GeckoDriver / Firefox instalados (`sudo apt install firefox-esr geckodriver`).
3. Copia `.env.example` a `.env` y llena los valores reales (SQL Server y
   credenciales de cada portal). El `.env` nunca se sube a git.
4. `python main.py`

## Notas

- Los scripts de Manzanillo no se pudieron probar contra el portal real
  (requiere sesión autenticada); revisa los comentarios `# VERIFICAR` en
  `manzanillo.py` la primera vez que corras cada clasificación.
- El id de expediente de Manzanillo (`CargarDocumentosPorClasificacion('7777|<id>|0')`)
  se extrae dinámicamente por referencia — nunca se hardcodea.
- `laredo.py` y `manzanillo.py` se pueden correr de forma independiente
  (`python laredo.py`, `python manzanillo.py`) para pruebas: en ese caso
  arman su propia metadata ejecutando `Sanofi_V6.sql` directamente.
