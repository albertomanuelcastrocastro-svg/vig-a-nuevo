/*** ─────────────────────────────────────────────────────────────
     REGISTRO DE SEÑALES — recibe los avisos del Vigía de Zonas
     y los escribe en la pestaña SEÑALES de PANORAMA · DAVID.
 
     El bot manda un JSON por POST. Aquí se guarda dos veces:
       · desmenuzado en columnas, para poder filtrar y sacar medias
       · entero en la última columna, por si algún día hace falta algo
         que hoy no se nos ha ocurrido
 
     Si llega una señal con un id que ya existe (porque el bot manda
     el seguimiento de +1h, +4h y +24h), NO duplica: actualiza la fila.
 
     CÓMO PUBLICARLO (una sola vez):
       1. Extensiones → Apps Script, pegar esto en un archivo nuevo.
       2. Implementar → Nueva implementación → tipo "Aplicación web".
       3. Ejecutar como: Yo.  Quién tiene acceso: Cualquier usuario.
       4. Copiar la URL que da (termina en /exec) y meterla en Railway
          como variable REGISTRO_URL del servicio vig-a-nuevo.
     ───────────────────────────────────────────────────────────── ***/
 
const HOJA_SENALES = 'SEÑALES';
 
const CABECERA = [
  'fecha_hora', 'id', 'activo', 'tipo', 'sentido', 'papel_previo',
  'precio_senal', 'zona_centro', 'zona_ancho', 'fuerza', 'umbral',
  'toques', 'toques_techo', 'toques_suelo', 'dominio_compra', 'atr',
  'vela_color', 'vela_cuerpo_pct', 'vela_vol_relativo', 'vela_dom_comprador',
  'tramo_verdes', 'tramo_rojas',
  'mas_1h', 'mas_1h_pct', 'mas_4h', 'mas_4h_pct', 'mas_24h', 'mas_24h_pct',
  'mensaje', 'json'
];
 
function doPost(e) {
  try {
    const reg = JSON.parse(e.postData.contents);
    guardarSenal_(reg);
    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, id: reg.id }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
 
function guardarSenal_(reg) {
  const sh = getSenales_();
  const fila = filaDe_(reg);
 
  // ¿ya existe esa señal? (columna B = id)
  const ids = sh.getRange(2, 2, Math.max(sh.getLastRow() - 1, 1), 1).getValues();
  let destino = -1;
  for (let i = 0; i < ids.length; i++) {
    if (String(ids[i][0]) === String(reg.id)) { destino = i + 2; break; }
  }
 
  if (destino > 0) {
    sh.getRange(destino, 1, 1, fila.length).setValues([fila]);
  } else {
    sh.appendRow(fila);
  }
}
 
function filaDe_(reg) {
  const z = reg.zona || {};
  const v = reg.vela_senal || reg['vela_señal'] || {};
  const tr = reg.tramo_espera || {};
  const s = reg.seguimiento || {};
 
  function seg(clave, campo) {
    return (s[clave] && s[clave][campo] !== undefined) ? s[clave][campo] : '';
  }
 
  return [
    reg.hora_utc || '',
    reg.id || '',
    reg.activo || '',
    reg.tipo || '',
    reg.sentido || '',
    reg.papel_previo || '',
    num_(reg.precio_senal !== undefined ? reg.precio_senal : reg['precio_señal']),
    num_(z.centro), num_(z.ancho), num_(z.fuerza), num_(z.umbral_aplicado),
    num_(z.toques), num_(z.toques_techo), num_(z.toques_suelo),
    num_(z.dominio_compra), num_(z.atr),
    v.color || '',
    num_(v.cuerpo_pct_rango), num_(v.volumen_relativo), num_(v.dominio_comprador),
    num_(tr.verdes), num_(tr.rojas),
    num_(seg('mas_1h', 'precio')), num_(seg('mas_1h', 'variacion_pct')),
    num_(seg('mas_4h', 'precio')), num_(seg('mas_4h', 'variacion_pct')),
    num_(seg('mas_24h', 'precio')), num_(seg('mas_24h', 'variacion_pct')),
    reg.mensaje || '',
    JSON.stringify(reg)
  ];
}
 
function getSenales_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let s = ss.getSheetByName(HOJA_SENALES);
  if (!s) {
    s = ss.insertSheet(HOJA_SENALES);
    s.appendRow(CABECERA);
    s.setFrozenRows(1);
  }
  return s;
}
 
function num_(v) {
  if (v === null || v === undefined || v === '') return '';
  const n = Number(v);
  return isNaN(n) ? '' : n;
}
 
/*** Prueba de mesa: ejecutar a mano una vez tras publicar,
     para comprobar que crea la pestaña y escribe bien. ***/
function probarRegistro() {
  guardarSenal_({
    id: 'PRUEBA-1', hora_utc: '2026-09-18T12:00:00Z', activo: 'XRP',
    tipo: 'rebote', sentido: 'alcista', papel_previo: 'soporte',
    'precio_señal': 1.3195,
    zona: { centro: 1.3150, ancho: 0.004, fuerza: 24.5, umbral_aplicado: 8,
            toques: 13, toques_techo: 5, toques_suelo: 8,
            dominio_compra: 0.57, atr: 0.016 },
    'vela_señal': { color: 'verde', cuerpo_pct_rango: 0.62,
                    volumen_relativo: 2.1, dominio_comprador: 0.61 },
    tramo_espera: { verdes: 2, rojas: 1 },
    mensaje: 'PRUEBA — borrar esta fila',
    seguimiento: {}
  });
  Logger.log('Escrita la fila de prueba en ' + HOJA_SENALES);
}
