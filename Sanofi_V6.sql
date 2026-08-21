/* QUERY TIEMPOS DE OPERACIÓN SANOFI SIN FINES DE SEMANA CON CLASIFICACIÓN PRODUCTIVO/NO PRODUCTIVO */
/* Ultima modificacion: [21/08/2026] */
/* Incluye clasificación de mercancías - CORREGIDO COLLATION */

WITH ClasificacionMercancias AS (

    SELECT DISTINCT
        [DESCRIPCIÓN_PRODUCTO] COLLATE SQL_Latin1_General_CP1_CI_AS AS [DESCRIPCIÓN_PRODUCTO],
        'PRODUCTIVO' AS Clasificacion
    FROM [BI].[dbo].[Track and Trace productivo]
    WHERE [DESCRIPCIÓN_PRODUCTO] IS NOT NULL
      AND LTRIM(RTRIM([DESCRIPCIÓN_PRODUCTO])) <> ''

    UNION ALL

    -- No productivos
    SELECT DISTINCT
        [DESCRIPCIÓN_PRODUCTO] COLLATE SQL_Latin1_General_CP1_CI_AS AS [DESCRIPCIÓN_PRODUCTO],
        'NO PRODUCTIVO' AS Clasificacion
    FROM [BI].[dbo].[Track and Trace No productivo]
    WHERE [DESCRIPCIÓN_PRODUCTO] IS NOT NULL
      AND LTRIM(RTRIM([DESCRIPCIÓN_PRODUCTO])) <> ''
),
RefEntradaPorPedimento AS (
    SELECT
        REPLACE(TRIM(Referencia), '/', '') AS ReferenciaNormalizada,
        RIGHT(TRIM(CAST(Pedimento AS VARCHAR(20))), 7) AS PedimentoUltimos7,
        MAX(NULLIF(TRIM(Mercancia), '')) AS MercanciaRefPedimento
    FROM [SIR].[Admin].[ADMIN_VT_CGReferenciaEntrada]
    GROUP BY
        REPLACE(TRIM(Referencia), '/', ''),
        RIGHT(TRIM(CAST(Pedimento AS VARCHAR(20))), 7)
),
RefEntradaPorReferencia AS (
    SELECT
        REPLACE(TRIM(Referencia), '/', '') AS ReferenciaNormalizada,
        MAX(NULLIF(TRIM(Mercancia), '')) AS MercanciaRefReferencia
    FROM [SIR].[Admin].[ADMIN_VT_CGReferenciaEntrada]
    GROUP BY REPLACE(TRIM(Referencia), '/', '')
),
ConsultaBase AS (
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
        Pedimento,
        [Aduana/Sección Despacho],
        [dFechaPago],

        CASE
            WHEN Sucursal = 'CORRESPONSALIAS' THEN MONTH([Corresponsalias Fecha de Pago])
            ELSE MONTH([dFechaPago])
        END AS "MES",

        [Fecha Entrada/Presentación],

        /*---------------Función de Fecha de pago---------------*/
        CONVERT(VARCHAR(10),
        CASE
            WHEN Sucursal = 'CORRESPONSALIAS' THEN [Corresponsalias Fecha de Pago]
            WHEN Sucursal <> 'CORRESPONSALIAS' THEN [dFechaPago]
            ELSE [dFechaPago]
        END, 103) AS [Fecha de Pago funcion],




        /*------------------------------Entrada a Cruce -----------------------------*/
        [Fecha primera Selección],


        /*------------------------------ Etiqueta de fecha factura -----------------------------*/
        CuentaG_FechaFactura AS "FECHA DE FACTURACIÓN",



        /*  FECHA QUE RECIBE FACTURA  */
        CONVERT(VARCHAR(10), [FAC RECEPCION EXP. A FACTURACION], 103) AS "FECHA RECIBE FACT",

        CONVERT(VARCHAR(10),[Fechas de Cuentas de Gastos], 103) AS [FECHA TIMBRADO],




        /*------------------------------ Campos de clasificación -----------------------------*/
        [Clave Pedimento],
        Ejecutivo_ABC,
        [Guia Master],
        [Guia House],
        [Tipo Operación Desc],
        COALESCE(MERC.MercanciaFinal, main.Mercancía) AS Mercancía,
        [Tipo Mercancía] AS "Tipo de mercancía",
        Cliente,
        [EJE UNIDAD DE NEGOCIO] AS "Unidad de negocio",
        CASE
            WHEN [MOTIVO DE RETRASO] = 'OTRO' THEN [MOTIVO DE RETRASO OTRO]
            ELSE [MOTIVO DE RETRASO]
        END AS "MOTIVO DE RETRASO COMPLETO",
        Ejecutivo_Tipo_Mercancia,
        [Primera Selección],
        [TRANSPORTISTA.],
        RFC_Importador,
        OtrosIncPed,
        Nico,
        Mercancia_CovesSubModelo


    FROM [SIR].[Admin].[SIR_VT_Sabana_Pedimento_ABC] main
    LEFT JOIN RefEntradaPorPedimento AS REF_E
        ON REPLACE(TRIM(main.Referencia), '/', '') = REF_E.ReferenciaNormalizada
        AND RIGHT(TRIM(CAST(main.Pedimento AS VARCHAR(20))), 7) = REF_E.PedimentoUltimos7
    LEFT JOIN RefEntradaPorReferencia AS REF_E2
        ON REPLACE(TRIM(main.Referencia), '/', '') = REF_E2.ReferenciaNormalizada
    OUTER APPLY (
        SELECT
            CASE
                WHEN main.Sucursal = 'CORRESPONSALIAS'
                     OR main.Referencia LIKE '%LT%'
                     OR main.Referencia LIKE '%MNS%'
                    THEN COALESCE(
                        REF_E.MercanciaRefPedimento,
                        NULLIF(TRIM(main.Mercancía), ''),
                        REF_E2.MercanciaRefReferencia
                    )
                ELSE COALESCE(
                    NULLIF(TRIM(main.Mercancía), ''),
                    REF_E.MercanciaRefPedimento,
                    REF_E2.MercanciaRefReferencia
                )
            END AS MercanciaFinal
    ) AS MERC
    /* JOIN para clasificación de mercancías CON COLLATION CORREGIDO */
    LEFT JOIN ClasificacionMercancias cm ON COALESCE(MERC.MercanciaFinal, main.Mercancía) COLLATE SQL_Latin1_General_CP1_CI_AS = cm.[DESCRIPCIÓN_PRODUCTO]

    WHERE
        --(Cliente like '%AVENTIS%')
        (Cliente IN ('SANOFI PASTEUR, S.A DE C.V.','AZTECA VACUNAS, SA DE CV','SANOFI MEXICO S.A. DE C.V.')
        OR (Cliente LIKE '%AVENTIS%'))
        --AND [Tipo Operación Desc] = 'Importación'
        --and [Clave Pedimento] not like 'R%'
)
SELECT *
FROM ConsultaBase
WHERE --"CLASIFICACIÓN DE MERCANCIA" IN ('PRODUCTIVO') --('PRODUCTIVO','SIN CLASIFICACION')
   --AND
   (
    -- Convertir el campo unificado de vuelta a DATE para la comparación
             TRY_CONVERT(DATE, [Fecha de Pago funcion], 103) >= '2026-01-01'
             AND TRY_CONVERT(DATE, [Fecha de Pago funcion], 103) <= '2026-08-15')
--[MOTIVO DE RETRASO COMPLETO] not like 'NULL'
and [Sucursal] LIKE 'CORRESPONSALIAS'
ORDER BY [MES]
