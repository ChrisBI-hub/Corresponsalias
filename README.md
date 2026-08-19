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
Descargas/{RazonSocial}/{Año}/{Aduana}/{Referencia}/{Referencia}_{Pedimento}_{Tag}.ext
```

- `RazonSocial`: nombre completo del `Cliente` tal como aparece en el WHERE del SQL.
- `Año`: año de `[Fecha de Pago funcion]`.
- `Aduana`: `LAREDO` o `MANZANILLO` (columna `[Tipo Sucursal]`).
- `Pedimento`: viene directo de la columna `Pedimento` de la consulta.
- `Tag`: tipo de documento (ej. `CASAWIN`, `EXPEDIENTES`, `PROFORMA_GLOSADA`, `DODA`, `GASTOS`, o la etiqueta del documento en Laredo).

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
