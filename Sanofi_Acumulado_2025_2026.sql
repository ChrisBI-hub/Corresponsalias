/* ============================================================================
   REPORTE ACUMULADO DE OPERACIONES — GRUPO SANOFI (2025-2026)
   ============================================================================
   Solicitado para compartir con Sanofi: acumulado de operaciones de
   Importación, Exportación, Corresponsalías, R1/T3, pedimentos globales o
   complementarios (si existen) y operaciones facturadas a terceros/pacientes,
   de los pedimentos emitidos a nombre de:
     - Sanofi Pasteur, S.A. de C.V.
     - Sanofi Aventis de México, S.A. de C.V.
     - Azteca Vacunas, S.A. de C.V.
     - Sanofi México, S.A. de C.V.
   en las aduanas AICM, AIFA, Veracruz, Laredo, Manzanillo (+ Corresponsalías).

   El rango de fechas [Fecha de Pago funcion] >= / <= al final se sustituye en
   automático desde reporte_acumulado_sanofi.py (mismo mecanismo de
   Aventis_1.sql) — no lo edites a mano, solo sirve como valor por defecto.

   SUPUESTOS A VALIDAR (ver también hoja "Notas y Supuestos" del Excel):
   1) Universo de pedimentos: se filtra por [Cliente] con el mismo patrón que
      ya usa Sanofi_V6.sql en este repo (probado contra la base real):
      Cliente IN (Pasteur/Azteca Vacunas/Sanofi México) OR Cliente LIKE
      '%AVENTIS%'. Si el RFC/razón social real del pedimento no coincide
      siempre con el Cliente comercial, esto podría dejar fuera pedimentos
      válidos — revisar con el equipo SN.
   2) BU (CHC/GENMED/INV. CLÍNICA): se asume que [EJE UNIDAD DE NEGOCIO] trae
      esos valores literales (confirmado por el usuario). Cualquier valor que
      no matchee ninguno de los 3 se etiqueta 'OTRAS/SIN BU' en vez de
      perderse en silencio.
   3) Responsable de pago en R1/T3: columna [Recti A Cargo De] tal cual la
      indicó el usuario, solo poblada cuando [Clave Pedimento] empieza con 'R'.
   4) "Operaciones facturadas a terceros / Otros (Pacientes)": el usuario
      indicó identificarlas por [RazonSocial de Proveedores] / [Facturas].
      NO se garantiza que esas columnas representen al facturado real (podrían
      ser el proveedor/exportador extranjero); por eso el query expone un
      campo [Posible Facturado a Terceros] como heurística tentativa, NO como
      clasificación definitiva — debe validarse contra datos reales antes de
      confiar en ella.
   5) Pedimentos globales/complementarios: no se identificó una [Clave
      Pedimento] específica y confirmada para "global" o "complementario", así
      que NO se filtran ni se marcan aparte — quedan incluidos igual que
      cualquier otro pedimento (columna [Clave Pedimento] queda visible para
      que el equipo los ubique manualmente si existen).
   ============================================================================ */

WITH ConsultaBase AS (
    SELECT
        Sucursal AS Sucursal,
        CASE
            WHEN Sucursal = 'CIUDAD DE MÉXICO' THEN 'AÉREO AICM'
            WHEN Sucursal = 'AIFA ESTADO DE MEXICO' THEN 'AÉREO AIFA'
            WHEN Sucursal = 'VERACRUZ' THEN 'MARÍTIMO VERACRUZ'
            WHEN CHARINDEX('LT', Referencia) > 0 THEN 'LAREDO'
            WHEN CHARINDEX('MNS', Referencia) > 0 THEN 'MANZANILLO'
            ELSE 'CORRESPONSALIAS'
        END AS [Tipo Sucursal],
        Referencia,
        Cliente,

        /* Bucket de 5 categorías solicitado (Sanofi Pasteur / Sanofi Aventis
           de México / Azteca Vacunas / Sanofi México / Otros (Pacientes)).
           Se resuelve por Cliente; ver supuesto (1) arriba. */
        CASE
            WHEN Cliente LIKE '%PASTEUR%' THEN 'Sanofi Pasteur'
            WHEN Cliente LIKE '%AVENTIS%' THEN 'Sanofi Aventis de México'
            WHEN Cliente LIKE '%AZTECA%' OR Cliente LIKE '%VACUNAS%' THEN 'Azteca Vacunas'
            WHEN Cliente LIKE '%SANOFI MEXICO%' OR Cliente LIKE '%SANOFI MÉXICO%' THEN 'Sanofi México'
            ELSE 'Otros (Pacientes)'
        END AS [Razón Social Reporte],

        /* Heurística TENTATIVA de facturación a terceros/pacientes — ver
           supuesto (4). Requiere validación contra datos reales. */
        CASE
            WHEN ([RazonSocial de Proveedores] IS NOT NULL AND LTRIM(RTRIM([RazonSocial de Proveedores])) <> ''
                  AND [RazonSocial de Proveedores] NOT LIKE '%SANOFI%'
                  AND [RazonSocial de Proveedores] NOT LIKE '%AVENTIS%'
                  AND [RazonSocial de Proveedores] NOT LIKE '%AZTECA%')
              OR
                 ([Facturas] IS NOT NULL AND LTRIM(RTRIM([Facturas])) <> ''
                  AND [Facturas] NOT LIKE '%SANOFI%'
                  AND [Facturas] NOT LIKE '%AVENTIS%'
                  AND [Facturas] NOT LIKE '%AZTECA%')
            THEN 'Posible tercero/paciente (revisar)'
            ELSE 'No'
        END AS [Posible Facturado a Terceros],

        CASE
            WHEN [EJE UNIDAD DE NEGOCIO] LIKE 'CHC%' THEN 'CHC'
            WHEN [EJE UNIDAD DE NEGOCIO] LIKE 'GENMED%' THEN 'GENMED'
            WHEN [EJE UNIDAD DE NEGOCIO] LIKE 'INV%CLIN%' THEN 'INV. CLÍNICA'
            ELSE 'OTRAS/SIN BU'
        END AS [BU],
        [EJE UNIDAD DE NEGOCIO] AS [BU Original (sin normalizar)],

        [Patente],
        CASE WHEN [Clave Pedimento] NOT LIKE 'R%' THEN Pedimento ELSE NULL END AS [Pedimento Original A1],
        CASE WHEN [Clave Pedimento] LIKE 'R%' THEN Pedimento ELSE NULL END AS [Pedimento R1],
        CASE WHEN [Clave Pedimento] LIKE 'R%' THEN [Recti A Cargo De] ELSE NULL END AS [Responsable de Pago R1],
        [Tipo Operación Desc],
        [Clave Pedimento],
        RFC_Importador AS [Tax ID],
        [Tipo de Cambio de Pedimento],
        [Aduana/Sección Despacho] AS 'Aduana Despacho',
        [Contenedores],
        CASE
            WHEN ISNULL([Contenedores], '') = '' THEN 0
            ELSE LEN([Contenedores]) - LEN(REPLACE([Contenedores], ',', '')) + 1
        END AS [QTY Contenedor],
        [Peso Bruto],
        [Total de Bultos],
        [Valor Comercial MXP],
        [Valor Aduana],
        [Seguros],
        [Fletes],
        [Embalajes],
        [OtrosIncPed] AS 'Otros',
        [Valor Tasa Partida] AS 'TASA IGI/IGE',
        [IGI] AS 'IGI/IGIE',
        [Recargos],
        [Importe DTA FP1] AS 'DTA',
        [Importe IVA 1] AS 'IVA',
        [importeprv] AS 'Prevalidación',
        [IVA PRV] AS 'IVA Prevalidación',
        (
            ISNULL([IGI], 0) +
            ISNULL([Recargos], 0) +
            ISNULL([Importe DTA FP1], 0) +
            ISNULL([Importe IVA 1], 0) +
            ISNULL([importeprv], 0) +
            ISNULL([IVA PRV], 0)
        ) AS 'Impuestos',
        [Facturas],
        [RazonSocial de Proveedores],
        [Clave Incoterm],
        [Clave de País Origen/Destino],
        [Clave de País Vendedor/Comprador],
        [Moneda Factura] AS 'Moneda',
        [Guia Master] AS 'Bls MASTER',
        [Guia House] AS 'Bls HOUSE',
        [Fracciones],
        Mercancía AS [Descripcion de la mercancía],
        [EJE TIPO DE MERCANCÍA] AS 'PT',
        [EMBARQUE REFRIGERADO] AS 'Tempreratura',
        COALESCE(NULLIF([UNIDAD RENTADA], ''), [UNIDAD SUPER EXPRESS]) AS [UNIDAD],
        COALESCE(NULLIF([PLACAS RENTADA], ''), [PLACAS SUPER EXPRESS]) AS [PLACAS],
        [Fecha Entrada/Presentación],

        CONVERT(VARCHAR(10),
            CASE
                WHEN Sucursal = 'CORRESPONSALIAS' THEN [Corresponsalias Fecha de Pago]
                ELSE [ Fecha de Pago]
            END, 103) AS [Fecha de Pago funcion],

        (
            DATEDIFF(DAY,
                TRY_CAST([Fecha Entrada/Presentación] AS DATE),
                TRY_CAST(
                    CASE
                        WHEN Sucursal = 'CORRESPONSALIAS' THEN [Corresponsalias Fecha de Pago]
                        ELSE [ Fecha de Pago]
                    END AS DATE
                )
            ) + 1
            - (DATEDIFF(WEEK,
                TRY_CAST([Fecha Entrada/Presentación] AS DATE),
                TRY_CAST(
                    CASE
                        WHEN Sucursal = 'CORRESPONSALIAS' THEN [Corresponsalias Fecha de Pago]
                        ELSE [ Fecha de Pago]
                    END AS DATE
                )
            ) * 2)
            - (CASE WHEN DATENAME(WEEKDAY, TRY_CAST([Fecha Entrada/Presentación] AS DATE)) = 'Domingo' THEN 1 ELSE 0 END)
            - (CASE WHEN DATENAME(WEEKDAY, TRY_CAST(
                    CASE
                        WHEN Sucursal = 'CORRESPONSALIAS' THEN [Corresponsalias Fecha de Pago]
                        ELSE [ Fecha de Pago]
                    END AS DATE
                )) = 'Sábado' THEN 1 ELSE 0 END)
        ) AS [Entrada de pago],

        CASE
            WHEN [MOTIVO DE RETRASO] = 'OTRO' THEN [MOTIVO DE RETRASO OTRO]
            ELSE [MOTIVO DE RETRASO]
        END AS "MOTIVO DE RETRASO COMPLETO",
        [FolioCuentaGastos] AS 'Cuenta de Gastos',
        [PermisosPartida] AS 'Permisos'

    FROM [SIR].[Admin].[SIR_VT_Sabana_Pedimento_ABC] main

    WHERE
        (
            Cliente IN ('SANOFI PASTEUR, S.A DE C.V.', 'AZTECA VACUNAS, SA DE CV', 'SANOFI MEXICO S.A. DE C.V.')
            OR Cliente LIKE '%AVENTIS%'
        )
)
SELECT
    *,
    CASE
        WHEN [Impuestos] BETWEEN 1 AND 24999 THEN 'FINANCIADO'
        WHEN [Impuestos] >= 25000 THEN 'PECE'
        ELSE 'SIN CLASIFICAR'
    END AS [PECE]
FROM ConsultaBase
WHERE
    TRY_CONVERT(DATE, [Fecha de Pago funcion], 103) >= '2025-01-01'
    AND TRY_CONVERT(DATE, [Fecha de Pago funcion], 103) <= '2026-08-31'
ORDER BY [BU], TRY_CONVERT(DATE, [Fecha de Pago funcion], 103);
