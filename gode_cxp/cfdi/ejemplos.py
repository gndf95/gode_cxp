"""CFDI de ejemplo (sin sello válido) para pruebas. RFC receptor = empresa de pruebas."""

RFC_EMPRESA = "GES200101ABC"

INGRESO_40 = b"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
 Version="4.0" Serie="A" Folio="1234" Fecha="2026-09-10T10:15:00" FormaPago="03" MetodoPago="PPD" Moneda="MXN"
 SubTotal="1000.00" Descuento="0.00" Total="1160.00" TipoDeComprobante="I" Exportacion="01" LugarExpedicion="03100"
 Sello="x" NoCertificado="00001000000500000000" Certificado="x">
  <cfdi:Emisor Rfc="AVI900101AB1" Nombre="AVICOLA DEL CARMEN SA DE CV" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="GES200101ABC" Nombre="GASTRONOMICA DE ESPECIALIDADES GODE" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601" UsoCFDI="G03"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="50112000" Cantidad="10" ClaveUnidad="KGM" Unidad="Kilogramo" Descripcion="Pechuga de pollo" ValorUnitario="100.00" Importe="1000.00" ObjetoImp="02">
      <cfdi:Impuestos><cfdi:Traslados><cfdi:Traslado Base="1000.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="160.00"/></cfdi:Traslados></cfdi:Impuestos>
    </cfdi:Concepto>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="160.00">
    <cfdi:Traslados><cfdi:Traslado Base="1000.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="160.00"/></cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital Version="1.1" UUID="6f2c3d48-1234-4a5b-9c8d-abcdef012345" FechaTimbrado="2026-09-10T10:16:00" RfcProvCertif="SAT970701NN3" SelloCFD="x" NoCertificadoSAT="00001000000400000000" SelloSAT="x"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""

# Persona física con retenciones de ISR (10 %) e IVA (10.6667 %), CFDI 3.3.
INGRESO_33_RETENCIONES = b"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
 Version="3.3" Serie="F" Folio="77" Fecha="2026-09-05T09:00:00" FormaPago="03" MetodoPago="PUE" Moneda="MXN"
 SubTotal="5000.00" Total="4766.67" TipoDeComprobante="I" LugarExpedicion="03100" Sello="x" NoCertificado="00001000000500000000" Certificado="x">
  <cfdi:Emisor Rfc="HESB850101AB1" Nombre="BRUNO RICARDO HERNANDEZ SILVA" RegimenFiscal="612"/>
  <cfdi:Receptor Rfc="GES200101ABC" Nombre="GASTRONOMICA DE ESPECIALIDADES GODE" UsoCFDI="G03"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="80101500" Cantidad="1" ClaveUnidad="E48" Unidad="Servicio" Descripcion="Asesoria de cocina septiembre" ValorUnitario="5000.00" Importe="5000.00">
      <cfdi:Impuestos>
        <cfdi:Traslados><cfdi:Traslado Base="5000.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="800.00"/></cfdi:Traslados>
        <cfdi:Retenciones><cfdi:Retencion Base="5000.00" Impuesto="001" TipoFactor="Tasa" TasaOCuota="0.100000" Importe="500.00"/><cfdi:Retencion Base="5000.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.106667" Importe="533.33"/></cfdi:Retenciones>
      </cfdi:Impuestos>
    </cfdi:Concepto>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosRetenidos="1033.33" TotalImpuestosTrasladados="800.00">
    <cfdi:Retenciones><cfdi:Retencion Impuesto="001" Importe="500.00"/><cfdi:Retencion Impuesto="002" Importe="533.33"/></cfdi:Retenciones>
    <cfdi:Traslados><cfdi:Traslado Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="800.00"/></cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital Version="1.1" UUID="A1B2C3D4-0000-4E5F-8A9B-000000000077" FechaTimbrado="2026-09-05T09:01:00" RfcProvCertif="SAT970701NN3" SelloCFD="x" NoCertificadoSAT="00001000000400000000" SelloSAT="x"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""

# Nota de crédito (egreso) relacionada con INGRESO_40.
EGRESO_40 = b"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
 Version="4.0" Serie="NC" Folio="9" Fecha="2026-09-12T12:00:00" FormaPago="99" MetodoPago="PUE" Moneda="MXN"
 SubTotal="200.00" Total="232.00" TipoDeComprobante="E" Exportacion="01" LugarExpedicion="03100" Sello="x" NoCertificado="00001000000500000000" Certificado="x">
  <cfdi:CfdiRelacionados TipoRelacion="01"><cfdi:CfdiRelacionado UUID="6F2C3D48-1234-4A5B-9C8D-ABCDEF012345"/></cfdi:CfdiRelacionados>
  <cfdi:Emisor Rfc="AVI900101AB1" Nombre="AVICOLA DEL CARMEN SA DE CV" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="GES200101ABC" Nombre="GASTRONOMICA DE ESPECIALIDADES GODE" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601" UsoCFDI="G02"/>
  <cfdi:Conceptos>
    <cfdi:Concepto ClaveProdServ="84111506" Cantidad="1" ClaveUnidad="ACT" Descripcion="Devolucion 2 kg pechuga" ValorUnitario="200.00" Importe="200.00" ObjetoImp="02">
      <cfdi:Impuestos><cfdi:Traslados><cfdi:Traslado Base="200.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="32.00"/></cfdi:Traslados></cfdi:Impuestos>
    </cfdi:Concepto>
  </cfdi:Conceptos>
  <cfdi:Impuestos TotalImpuestosTrasladados="32.00"><cfdi:Traslados><cfdi:Traslado Base="200.00" Impuesto="002" TipoFactor="Tasa" TasaOCuota="0.160000" Importe="32.00"/></cfdi:Traslados></cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital Version="1.1" UUID="99999999-1111-4222-8333-444444444444" FechaTimbrado="2026-09-12T12:01:00" RfcProvCertif="SAT970701NN3" SelloCFD="x" NoCertificadoSAT="00001000000400000000" SelloSAT="x"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""

# Complemento de pago (tipo P); el detalle del pago se procesa en el Plan C.
PAGO_40 = b"""<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" xmlns:pago20="http://www.sat.gob.mx/Pagos20"
 Version="4.0" Serie="P" Folio="3" Fecha="2026-09-20T08:00:00" Moneda="XXX" SubTotal="0" Total="0" TipoDeComprobante="P" Exportacion="01" LugarExpedicion="03100" Sello="x" NoCertificado="00001000000500000000" Certificado="x">
  <cfdi:Emisor Rfc="AVI900101AB1" Nombre="AVICOLA DEL CARMEN SA DE CV" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="GES200101ABC" Nombre="GASTRONOMICA DE ESPECIALIDADES GODE" DomicilioFiscalReceptor="06600" RegimenFiscalReceptor="601" UsoCFDI="CP01"/>
  <cfdi:Conceptos><cfdi:Concepto ClaveProdServ="84111506" Cantidad="1" ClaveUnidad="ACT" Descripcion="Pago" ValorUnitario="0" Importe="0" ObjetoImp="01"/></cfdi:Conceptos>
  <cfdi:Complemento>
    <pago20:Pagos Version="2.0"><pago20:Totales MontoTotalPagos="1160.00"/>
      <pago20:Pago FechaPago="2026-09-19T12:00:00" FormaDePagoP="03" MonedaP="MXN" TipoCambioP="1" Monto="1160.00">
        <pago20:DoctoRelacionado IdDocumento="6f2c3d48-1234-4a5b-9c8d-abcdef012345" Serie="A" Folio="1234" MonedaDR="MXN" EquivalenciaDR="1" NumParcialidad="1" ImpSaldoAnt="1160.00" ImpPagado="1160.00" ImpSaldoInsoluto="0.00" ObjetoImpDR="01"/>
      </pago20:Pago>
    </pago20:Pagos>
    <tfd:TimbreFiscalDigital Version="1.1" UUID="55555555-2222-4333-8444-555555555555" FechaTimbrado="2026-09-20T08:01:00" RfcProvCertif="SAT970701NN3" SelloCFD="x" NoCertificadoSAT="00001000000400000000" SelloSAT="x"/>
  </cfdi:Complemento>
</cfdi:Comprobante>"""

USD_40 = INGRESO_40.replace(b'Moneda="MXN"', b'Moneda="USD" TipoCambio="18.5000"').replace(
    b'UUID="6f2c3d48-1234-4a5b-9c8d-abcdef012345"', b'UUID="77777777-3333-4444-8555-666666666666"').replace(b'Folio="1234"', b'Folio="1300"')

# Receptor distinto de la empresa: debe quedar como "Ajeno".
AJENO_40 = INGRESO_40.replace(b'Rfc="GES200101ABC"', b'Rfc="XAXX010101000"').replace(
    b'UUID="6f2c3d48-1234-4a5b-9c8d-abcdef012345"', b'UUID="88888888-3333-4444-8555-666666666666"')

SIN_TIMBRE = INGRESO_40.split(b"<cfdi:Complemento>")[0] + b"</cfdi:Comprobante>"
