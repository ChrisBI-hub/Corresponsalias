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

## Descompresión automática de ZIPs

Algunos documentos (por ejemplo `GASTOS` en Manzanillo) se descargan como
`.zip`. Para facilitar su uso, cada archivo `.zip` descargado se
descomprime automáticamente en una subcarpeta a su lado con el mismo
nombre (sin extensión); el `.zip` original se conserva tal cual junto a
esa subcarpeta:

```
.../GASTOS.zip
.../GASTOS/            <- contenido descomprimido
    factura.pdf
    ...
```

Si el zip está corrupto o incompleto la descompresión simplemente se
omite (se avisa en el log) — el `.zip` descargado sigue disponible de
todas formas, no se pierde nada.

## Corridas repetidas — no vuelve a descargar lo ya descargado

Cada referencia procesada queda registrada en `log_referencias_procesadas.json`
(en `PROYECTO_BASE`, no se sube a git). Al iniciar, `main.py` salta cualquier
referencia que ya haya quedado con `Estado: "OK"` en una corrida anterior;
las que quedaron `PARCIAL`, `ERROR`, `SIN_DOCUMENTOS`, etc. se reintentan.
El log se actualiza en disco referencia por referencia (no solo al final),
así una corrida larga no pierde el progreso si se corta a medias.

Si quieres forzar que se vuelva a descargar todo (o una referencia puntual),
borra `log_referencias_procesadas.json` (o edítalo a mano y quita esa
referencia).

## Puesta en marcha

1. `pip install -r requirements.txt`
2. GeckoDriver / Firefox instalados (`sudo apt install firefox-esr geckodriver`).
3. Copia `.env.example` a `.env` y llena los valores reales (SQL Server y
   credenciales de cada portal). El `.env` nunca se sube a git.
4. `python main.py`

## Descargas en el recurso de red (`\\10.10.10.97\corresponsal_Efile`)

Los archivos finales se pueden guardar directo en ese recurso compartido
de Windows en vez de en disco local. Desde Linux esa misma ruta se monta
por CIFS/SMB (nunca se escribe la contraseña en la línea de comandos ni
en un archivo que se suba a git — va en un archivo de credenciales local
con permisos 600):

```bash
sudo apt install cifs-utils

# Archivo de credenciales (NO se sube a git — está en .gitignore):
cat > ~/.smbcredentials <<'EOF'
username=christian.carbajal.bi
password=<LA_CONTRASEÑA_REAL>
EOF
chmod 600 ~/.smbcredentials

sudo mkdir -p /mnt/corresponsal
sudo mount -t cifs //10.10.10.97/corresponsal_Efile /mnt/corresponsal \
    -o credentials=/home/kali/.smbcredentials,vers=3.0
```

Para que quede montado automáticamente al reiniciar, agrega esta línea a
`/etc/fstab` (ajusta la ruta del archivo de credenciales y el usuario si
no es `kali`):

```
//10.10.10.97/corresponsal_Efile /mnt/corresponsal cifs credentials=/home/kali/.smbcredentials,vers=3.0,x-systemd.automount,_netdev 0 0
```

Y en `.env`, apunta `RUTA_DESCARGAS` al punto de montaje:

```
RUTA_DESCARGAS=/mnt/corresponsal
```

Si el share no está montado cuando corres `main.py`, el script se detiene
de inmediato con un error claro (en vez de fallar a medias durante la
descarga) — monta el recurso antes de ejecutar. También se valida que se
pueda **escribir** ahí: un mount hecho con `sudo` sin `uid=`/`gid=`/
`file_mode=`/`dir_mode=` deja los archivos con permisos que solo root
puede escribir, aunque el mount esté activo. Si tu usuario normal no es
quien puede escribir, vuelve a montar así:

```bash
sudo umount /mnt/corresponsal
sudo mount -t cifs //10.10.10.97/corresponsal_Efile /mnt/corresponsal \
    -o credentials=/home/christian/.smbcredentials,vers=3.0,uid=$(id -u),gid=$(id -g),file_mode=0777,dir_mode=0777
```

## Sobre `_tmp_descargas`

Es una carpeta de **paso intermedio**, solo la usa `manzanillo.py`: Firefox
necesita una carpeta de descargas fija antes de que el script sepa a qué
referencia/clasificación pertenece el archivo, así que descarga ahí
primero y luego el script lo mueve y renombra a su carpeta final en
`Descargas/`. En una corrida exitosa debería quedar **vacía**. Si algo se
queda ahí es porque el movimiento a la carpeta final falló (timeout o
error) — al inicio de cada corrida el script avisa si hay sobrantes de una
corrida anterior, para que se revisen y clasifiquen a mano si hace falta.
`laredo.py` no la usa para guardar nada: descarga directo a su carpeta
final vía `requests` (su Firefox ya no tiene configurada ninguna
descarga silenciosa).

## Notas

- Los scripts de Manzanillo no se pudieron probar contra el portal real
  (requiere sesión autenticada); revisa los comentarios `# VERIFICAR` en
  `manzanillo.py` la primera vez que corras cada clasificación.
- El id de expediente de Manzanillo (`CargarDocumentosPorClasificacion('7777|<id>|0')`)
  se extrae dinámicamente por referencia — nunca se hardcodea.
- `laredo.py` y `manzanillo.py` se pueden correr de forma independiente
  (`python laredo.py`, `python manzanillo.py`) para pruebas: en ese caso
  arman su propia metadata ejecutando `Sanofi_V6.sql` directamente.
