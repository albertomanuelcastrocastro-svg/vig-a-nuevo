"""
VIGÍA DE ZONAS — Charlie / PANORAMA·DAVID
==========================================
Vigila XRP (y Solana) contra las zonas donde el precio se ha dado la vuelta
de verdad, y avisa por Telegram cuando las toca.

Principios (de Charlie):
  · Son ZONAS, no líneas. Su anchura se calcula con el ATR, así se ajusta sola.
  · Lo que da peso a un toque NO es la distancia recorrida: es el VOLUMEN.
    Un toque con volumen fuerte es donde alguien defendió de verdad.
  · Una zona rota cambia de papel: la resistencia pasa a ser soporte.
  · Cuando toca, NO avisa de inmediato: espera a ver la reacción y entonces
    dice si rebotó o si la cruzó.

No decide nada. Solo avisa para que Charlie abra la gráfica y mire.

REGISTRO (v2, 18-sep-2026)
  Cada señal se guarda como un JSON completo: hora, zona, toques, fuerza,
  la vela que provocó el aviso (cuerpo, mechas, volumen, quién mandaba) y
  el seguimiento del precio a +1h, +4h y +24h. Con eso se puede medir de
  verdad si los avisos aciertan.
  Sale por dos sitios: una línea JSON en el log, y un POST a la hoja si
  REGISTRO_URL está configurada.

Datos: API pública de Binance. Gratis, sin clave, tiempo real.
"""

import os
import time
import json
import statistics
from datetime import datetime, timezone

import urllib.request
import urllib.parse

# ─────────────────────────────────────────────────────────────────────
# AJUSTES — todo esto se puede cambiar desde las variables de Railway
# ─────────────────────────────────────────────────────────────────────

SIMBOLOS = os.getenv("SIMBOLOS", "XRPUSDT,SOLUSDT").split(",")

# Cada cuánto mira el precio (segundos)
INTERVALO_SEGUNDOS = int(os.getenv("INTERVALO_SEGUNDOS", "60"))

# Cuánto espera, tras tocar una zona, antes de juzgar la reacción (minutos)
ESPERA_MINUTOS = int(os.getenv("ESPERA_MINUTOS", "15"))

# Anchura de la zona, en fracción de ATR (0.25 = un cuarto de ATR a cada lado)
ANCHO_ZONA_ATR = float(os.getenv("ANCHO_ZONA_ATR", "0.25"))

# Cuánto recorrido (en ATR) hace falta para considerar que hubo un giro
GIRO_MINIMO_ATR = float(os.getenv("GIRO_MINIMO_ATR", "1.0"))

# Fuerza mínima de una zona para que merezca un aviso.
# La fuerza es la suma de los toques PESADOS POR VOLUMEN.
# Se puede poner distinta por activo:  UMBRAL_XRPUSDT=8  UMBRAL_SOLUSDT=20
UMBRAL_FUERZA = float(os.getenv("UMBRAL_FUERZA", "3.0"))

# Tras avisar de una zona, cuántas horas se calla sobre esa misma zona
SILENCIO_HORAS = float(os.getenv("SILENCIO_HORAS", "4"))

# Cada cuántas horas recalcula las zonas desde cero
RECALCULO_HORAS = float(os.getenv("RECALCULO_HORAS", "12"))

# Vida media de un toque, en días: uno de hace 30 días pesa la mitad
VIDA_MEDIA_DIAS = float(os.getenv("VIDA_MEDIA_DIAS", "30"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── registro de señales ──
# URL de la app web de Apps Script que escribe en la pestaña SEÑALES.
# Si se deja vacía, el registro solo sale por el log.
REGISTRO_URL = os.getenv("REGISTRO_URL", "")

# Vela fina que se usa para radiografiar el momento de la señal
VELA_REGISTRO = os.getenv("VELA_REGISTRO", "5m")

# El seguimiento se cuenta en VELAS, no en horas: se mira qué hace el precio
# en las N velas siguientes a la señal, en la temporalidad con la que se
# calculan las zonas. Así la ventana se ajusta sola a cada activo.
TF_SENAL = os.getenv("TF_SENAL", "1h")
VENTANAS_VELAS = [int(x) for x in os.getenv("VENTANAS_VELAS", "5,10,15").split(",")]

_MIN_TF = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}

# Vela fina con la que se reconstruye el recorrido posterior a la señal.
# Se usan sus máximos y mínimos, así que el peor y el mejor momento salen exactos.
VELA_RECORRIDO = os.getenv("VELA_RECORRIDO", "5m")

# Simulación: stop y objetivo, medidos en ATR (la vara de la casa).
# Con esto se sabe si la señal era OPERABLE, no solo si acertaba.
SL_ATR = float(os.getenv("SL_ATR", "1.0"))
TP_ATR = float(os.getenv("TP_ATR", "2.0"))

BINANCE = "https://api.binance.com"


def umbral_de(simbolo):
    """Umbral de fuerza propio de cada activo, si se ha puesto uno."""
    return float(os.getenv(f"UMBRAL_{simbolo}", UMBRAL_FUERZA))


# ─────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def iso(ts):
    """Segundos epoch -> texto ISO en UTC."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pedir(url, params=None, datos=None, timeout=25):
    """Petición HTTP con lo que trae Python de serie. Sin librerías extra."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    cuerpo = json.dumps(datos).encode() if datos is not None else None
    req = urllib.request.Request(
        url, data=cuerpo,
        headers={"Content-Type": "application/json", "User-Agent": "vigia-zonas/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def telegram(texto):
    """Manda el aviso al móvil de Charlie."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log(f"(sin Telegram configurado) {texto}")
        return
    try:
        pedir(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
              datos={"chat_id": TELEGRAM_CHAT_ID, "text": texto}, timeout=15)
    except Exception as e:
        log(f"Error mandando Telegram: {e}")


def fmt(x, simbolo):
    """Formatea el precio con los decimales que tocan y coma decimal."""
    dec = 4 if x < 10 else 2
    return f"{x:.{dec}f}".replace(".", ",")


def r6(x):
    return None if x is None else round(float(x), 6)


# ─────────────────────────────────────────────────────────────────────
# DATOS — velas de Binance (gratis, con volumen)
# ─────────────────────────────────────────────────────────────────────

def traer_velas(simbolo, intervalo="1h", total=2000):
    """
    Descarga velas históricas. Binance da 1000 por llamada, así que
    encadena varias hacia atrás hasta juntar las que pidamos.
    Cada vela: apertura, máximo, mínimo, cierre, volumen, nº operaciones,
    y volumen del comprador agresivo (para saber quién mandaba).
    """
    velas = []
    fin = None
    while len(velas) < total:
        params = {"symbol": simbolo, "interval": intervalo,
                  "limit": min(1000, total - len(velas))}
        if fin:
            params["endTime"] = fin
        lote = pedir(f"{BINANCE}/api/v3/klines", params=params)
        if not lote:
            break
        velas = lote + velas
        fin = lote[0][0] - 1
        if len(lote) < params["limit"]:
            break
        time.sleep(0.25)   # no atosigar la API

    return [{
        "t":      int(v[0]),
        "abre":   float(v[1]),
        "max":    float(v[2]),
        "min":    float(v[3]),
        "cierra": float(v[4]),
        "vol":    float(v[5]),
        "ops":    int(v[8]),
        "vol_comprador": float(v[9]),
    } for v in velas]


def precio_actual(simbolo):
    d = pedir(f"{BINANCE}/api/v3/ticker/price", params={"symbol": simbolo}, timeout=15)
    return float(d["price"])


def traer_rango(simbolo, intervalo, inicio_ms, fin_ms):
    """Velas entre dos instantes, en orden. Para reconstruir el recorrido."""
    velas = []
    cursor = inicio_ms
    while cursor < fin_ms and len(velas) < 2000:
        lote = pedir(f"{BINANCE}/api/v3/klines", params={
            "symbol": simbolo, "interval": intervalo,
            "startTime": int(cursor), "endTime": int(fin_ms), "limit": 1000,
        })
        if not lote:
            break
        velas += lote
        cursor = lote[-1][0] + 1
        if len(lote) < 1000:
            break
        time.sleep(0.25)

    return [{
        "t": int(v[0]), "abre": float(v[1]), "max": float(v[2]),
        "min": float(v[3]), "cierra": float(v[4]), "vol": float(v[5]),
    } for v in velas]


# ─────────────────────────────────────────────────────────────────────
# CÁLCULO DE ZONAS
# ─────────────────────────────────────────────────────────────────────

def atr(velas, periodo=14):
    """Rango medio verdadero: cuánto se mueve este activo de normal."""
    rangos = []
    for i in range(1, len(velas)):
        a, b = velas[i - 1], velas[i]
        rangos.append(max(
            b["max"] - b["min"],
            abs(b["max"] - a["cierra"]),
            abs(b["min"] - a["cierra"]),
        ))
    if not rangos:
        return 0.0
    return sum(rangos[-periodo:]) / min(periodo, len(rangos))


def buscar_giros(velas, umbral):
    """
    Zigzag sobre máximos y mínimos REALES de cada vela.
    Un giro se confirma cuando el precio retrocede más de `umbral` desde
    el extremo. Devuelve (índice, precio, 'techo'|'suelo').
    """
    giros = []
    if len(velas) < 3:
        return giros

    direccion = 1          # 1 = buscando techo, -1 = buscando suelo
    ext_i, ext_p = 0, velas[0]["max"]

    for i, v in enumerate(velas):
        if direccion == 1:
            if v["max"] > ext_p:
                ext_i, ext_p = i, v["max"]
            elif ext_p - v["min"] > umbral:
                giros.append((ext_i, ext_p, "techo"))
                direccion, ext_i, ext_p = -1, i, v["min"]
        else:
            if v["min"] < ext_p:
                ext_i, ext_p = i, v["min"]
            elif v["max"] - ext_p > umbral:
                giros.append((ext_i, ext_p, "suelo"))
                direccion, ext_i, ext_p = 1, i, v["max"]

    return giros


def construir_zonas(velas, simbolo):
    """
    De los giros saca las zonas, y le pone a cada una su FUERZA,
    que es la suma de sus toques pesados por volumen y envejecidos.
    """
    if len(velas) < 50:
        return []

    a = atr(velas)
    if a <= 0:
        return []

    medio_ancho = a * ANCHO_ZONA_ATR
    giros = buscar_giros(velas, a * GIRO_MINIMO_ATR)
    if not giros:
        return []

    vol_tipico = statistics.median(v["vol"] for v in velas) or 1.0
    ahora_ms = velas[-1]["t"]

    zonas = []
    for idx, precio, tipo in sorted(giros, key=lambda g: g[1]):
        v = velas[idx]

        # PESO DEL TOQUE: manda el volumen (lo que pidió Charlie)
        peso_vol = min(v["vol"] / vol_tipico, 5.0)      # tope para que un pico no lo domine todo

        # y envejece: un toque viejo pesa menos
        dias = (ahora_ms - v["t"]) / 86_400_000
        peso_edad = 0.5 ** (dias / VIDA_MEDIA_DIAS)

        peso = peso_vol * peso_edad

        # ¿quién mandaba en ese toque? (comprador agresivo vs resto)
        prop_compra = v["vol_comprador"] / v["vol"] if v["vol"] else 0.5

        if zonas and abs(precio - zonas[-1]["centro"]) <= medio_ancho * 2:
            z = zonas[-1]
            z["precios"].append(precio)
            z["pesos"].append(peso)
            z["tipos"].append(tipo)
            z["compra"].append(prop_compra)
            z["fechas"].append(v["t"])
            z["centro"] = sum(z["precios"]) / len(z["precios"])
        else:
            zonas.append({
                "centro": precio, "precios": [precio], "pesos": [peso],
                "tipos": [tipo], "compra": [prop_compra], "fechas": [v["t"]],
            })

    for z in zonas:
        z["simbolo"] = simbolo
        z["ancho"] = medio_ancho
        z["atr"] = a
        z["fuerza"] = sum(z["pesos"])
        z["toques"] = len(z["precios"])
        z["toques_techo"] = z["tipos"].count("techo")
        z["toques_suelo"] = z["tipos"].count("suelo")
        z["dominio_compra"] = sum(z["compra"]) / len(z["compra"])
        z["primer_toque"] = min(z["fechas"])
        z["ultimo_toque"] = max(z["fechas"])
        z["id"] = f"{simbolo}:{z['centro']:.6f}"
        z["ultimo_aviso"] = 0.0
        z["estado"] = "fuera"
        z["entrada"] = None
        z["ultimo_lado"] = None

    umbral = umbral_de(simbolo)
    fuertes = [z for z in zonas if z["fuerza"] >= umbral]
    fuertes.sort(key=lambda z: -z["fuerza"])
    return fuertes


def papel(zona, precio):
    """Si el precio está por debajo, la zona le hace de techo. Y al revés."""
    return "resistencia" if precio < zona["centro"] else "soporte"


# ─────────────────────────────────────────────────────────────────────
# REGISTRO DE SEÑALES  (lo nuevo)
# ─────────────────────────────────────────────────────────────────────

def radiografia_vela(v, vol_tipico):
    """Todo lo que se puede decir de una vela: cuerpo, mechas, quién mandaba."""
    rango = v["max"] - v["min"]
    cuerpo = v["cierra"] - v["abre"]
    return {
        "hora_utc": iso(v["t"] / 1000),
        "apertura": r6(v["abre"]),
        "maximo": r6(v["max"]),
        "minimo": r6(v["min"]),
        "cierre": r6(v["cierra"]),
        "color": "verde" if cuerpo > 0 else ("roja" if cuerpo < 0 else "doji"),
        "rango": r6(rango),
        "cuerpo": r6(cuerpo),
        "cuerpo_pct_rango": r6(abs(cuerpo) / rango) if rango else None,
        "mecha_superior": r6(v["max"] - max(v["abre"], v["cierra"])),
        "mecha_inferior": r6(min(v["abre"], v["cierra"]) - v["min"]),
        "volumen": r6(v["vol"]),
        "volumen_comprador": r6(v["vol_comprador"]),
        "dominio_comprador": r6(v["vol_comprador"] / v["vol"]) if v["vol"] else None,
        "volumen_relativo": r6(v["vol"] / vol_tipico) if vol_tipico else None,
        "operaciones": v["ops"],
    }


def velas_del_momento(simbolo, t_entrada, t_juicio):
    """
    Trae velas finas alrededor de la señal y devuelve:
      · la vela en la que el precio ENTRÓ en la zona
      · la vela en la que se JUZGÓ la reacción
      · el resumen del tramo entre las dos
    """
    try:
        velas = traer_velas(simbolo, VELA_REGISTRO, 60)
    except Exception as e:
        log(f"No pude traer velas de registro de {simbolo}: {e}")
        return None, None, None

    if not velas:
        return None, None, None

    vol_tipico = statistics.median(v["vol"] for v in velas) or 1.0

    def mas_cercana(ts):
        objetivo = ts * 1000
        return min(velas, key=lambda v: abs(v["t"] - objetivo))

    v_ent = mas_cercana(t_entrada)
    v_jui = mas_cercana(t_juicio)

    tramo = [v for v in velas if v_ent["t"] <= v["t"] <= v_jui["t"]] or [v_jui]
    resumen = {
        "velas": len(tramo),
        "maximo": r6(max(v["max"] for v in tramo)),
        "minimo": r6(min(v["min"] for v in tramo)),
        "volumen_total": r6(sum(v["vol"] for v in tramo)),
        "dominio_comprador": r6(
            sum(v["vol_comprador"] for v in tramo) / sum(v["vol"] for v in tramo)
        ) if sum(v["vol"] for v in tramo) else None,
        "verdes": sum(1 for v in tramo if v["cierra"] > v["abre"]),
        "rojas": sum(1 for v in tramo if v["cierra"] < v["abre"]),
    }

    return (radiografia_vela(v_ent, vol_tipico),
            radiografia_vela(v_jui, vol_tipico),
            resumen)


def direccion_de(sentido):
    """+1 si la señal apunta hacia arriba, -1 si apunta hacia abajo."""
    return 1 if sentido in ("alcista", "arriba") else -1


def analizar_recorrido(simbolo, t_senal, precio0, direccion, atr, t_hasta):
    """
    Reconstruye qué hizo el precio DESPUÉS de la señal, hasta t_hasta.

    Lo que de verdad hace falta para saber si una señal es operable:
      · a_favor   — lo máximo que llegó a moverse hacia donde avisaba
      · en_contra — lo máximo que se fue al lado contrario por el camino
      · y la simulación de stop/objetivo: cuál de los dos habría saltado primero

    Sin el "en contra", una señal que acaba acertando parece buena aunque te
    hubiera sacado por el stop antes de llegar. Acertar y ser operable no es
    lo mismo, y esto separa las dos cosas.
    """
    velas = traer_rango(simbolo, VELA_RECORRIDO,
                        int(t_senal * 1000), int(t_hasta * 1000))
    if not velas or not precio0:
        return None

    mejor = peor = 0.0            # extremos a favor y en contra
    t_mejor = t_peor = None       # cuándo ocurrió cada uno
    favor_antes_del_peor = 0.0    # ¿llegó a ir a favor y lo devolvió?
    contra_antes_del_mejor = 0.0  # ¿cuánto dolor hubo que aguantar antes del premio?
    corre_favor = corre_contra = 0.0

    nivel_tp = precio0 + direccion * TP_ATR * atr if atr else None
    nivel_sl = precio0 - direccion * SL_ATR * atr if atr else None
    desenlace, t_desenlace = "abierta", None

    for v in velas:
        if direccion > 0:
            favor_v, contra_v = v["max"] - precio0, precio0 - v["min"]
            toca_tp = nivel_tp is not None and v["max"] >= nivel_tp
            toca_sl = nivel_sl is not None and v["min"] <= nivel_sl
        else:
            favor_v, contra_v = precio0 - v["min"], v["max"] - precio0
            toca_tp = nivel_tp is not None and v["min"] <= nivel_tp
            toca_sl = nivel_sl is not None and v["max"] >= nivel_sl

        if favor_v > mejor:
            mejor, t_mejor = favor_v, v["t"] / 1000
            contra_antes_del_mejor = corre_contra
        if contra_v > peor:
            peor, t_peor = contra_v, v["t"] / 1000
            favor_antes_del_peor = corre_favor

        corre_favor = max(corre_favor, favor_v)
        corre_contra = max(corre_contra, contra_v)

        if desenlace == "abierta" and (toca_tp or toca_sl):
            # Si en la MISMA vela caben los dos, se cuenta el stop. No se puede
            # saber el orden dentro de una vela, así que se tira por lo prudente.
            desenlace = "SL" if toca_sl else "TP"
            t_desenlace = v["t"] / 1000

    cierre = velas[-1]["cierra"]
    neto = (cierre - precio0) * direccion

    def en_atr(x):
        return r6(x / atr) if atr else None

    def min_desde(t):
        return r6((t - t_senal) / 60) if t else None

    # ¿qué pasó primero, el susto o el premio?
    if t_mejor and t_peor:
        primero = "a_favor" if t_mejor < t_peor else "en_contra"
    else:
        primero = "a_favor" if t_mejor else ("en_contra" if t_peor else None)

    return {
        "velas": len(velas),
        "a_favor":   {"precio": r6(mejor), "pct": r6(mejor / precio0 * 100),
                      "atr": en_atr(mejor), "minutos": min_desde(t_mejor)},
        "en_contra": {"precio": r6(peor),  "pct": r6(peor / precio0 * 100),
                      "atr": en_atr(peor),  "minutos": min_desde(t_peor)},
        "cierre":    {"precio": r6(cierre), "pct": r6(neto / precio0 * 100),
                      "atr": en_atr(neto)},
        "forma": {
            "primero": primero,
            # lo que hubo que aguantar en contra ANTES de llegar al mejor momento.
            # Es el número que dice si la señal se podía sostener o no.
            "contra_antes_del_mejor_atr": en_atr(contra_antes_del_mejor),
            # si esto es alto, la señal fue a favor, se dio la vuelta y te la devolvió
            "favor_antes_del_peor_atr": en_atr(favor_antes_del_peor),
        },
        "simulacion": {
            "sl_atr": SL_ATR, "tp_atr": TP_ATR,
            "nivel_sl": r6(nivel_sl), "nivel_tp": r6(nivel_tp),
            "desenlace": desenlace,
            "minutos_hasta": min_desde(t_desenlace),
        },
    }


def construir_registro(simbolo, zona, resultado, precio, ahora, texto):
    """Arma el JSON completo de una señal."""
    tipo = resultado[0]
    entrada = zona.get("entrada") or {}

    reg = {
        "id": f"{simbolo}-{int(ahora)}",
        "hora_utc": iso(ahora),
        "simbolo": simbolo,
        "activo": simbolo.replace("USDT", ""),
        "tipo": tipo,                                  # rebote | cruce
        "sentido": resultado[1],                       # alcista/bajista | arriba/abajo
        "direccion": direccion_de(resultado[1]),       # +1 arriba · -1 abajo
        "papel_previo": entrada.get("papel"),          # qué hacía la zona antes
        "lado_entrada": entrada.get("lado"),
        "precio_señal": r6(precio),
        "hora_entrada_zona_utc": iso(entrada["t"]) if entrada.get("t") else None,
        "minutos_espera": ESPERA_MINUTOS,
        "zona": {
            "id": zona["id"],
            "centro": r6(zona["centro"]),
            "ancho": r6(zona["ancho"]),
            "borde_inferior": r6(zona["centro"] - zona["ancho"]),
            "borde_superior": r6(zona["centro"] + zona["ancho"]),
            "fuerza": r6(zona["fuerza"]),
            "umbral_aplicado": r6(umbral_de(simbolo)),
            "toques": zona["toques"],
            "toques_techo": zona.get("toques_techo"),
            "toques_suelo": zona.get("toques_suelo"),
            "dominio_compra": r6(zona["dominio_compra"]),
            "atr": r6(zona.get("atr")),
            "primer_toque_utc": iso(zona["primer_toque"] / 1000) if zona.get("primer_toque") else None,
            "ultimo_toque_utc": iso(zona["ultimo_toque"] / 1000) if zona.get("ultimo_toque") else None,
        },
        "mensaje": texto.replace("\n", " | "),
        "seguimiento": {},
    }

    v_ent, v_jui, tramo = velas_del_momento(
        simbolo, entrada.get("t", ahora), ahora)
    reg["vela_entrada"] = v_ent
    reg["vela_señal"] = v_jui
    reg["tramo_espera"] = tramo

    return reg


def guardar_registro(reg):
    """Lo deja en el log siempre, y en la hoja si hay URL configurada."""
    print("SEÑAL_JSON " + json.dumps(reg, ensure_ascii=False), flush=True)
    if not REGISTRO_URL:
        return
    try:
        pedir(REGISTRO_URL, datos=reg, timeout=20)
    except Exception as e:
        log(f"No pude escribir el registro en la hoja: {e}")


# ─────────────────────────────────────────────────────────────────────
# VIGILANCIA
# ─────────────────────────────────────────────────────────────────────

def describir(zona):
    fuerza = zona["fuerza"]
    umbral = umbral_de(zona["simbolo"])
    etiqueta = "MUY FUERTE" if fuerza >= umbral * 2 else "fuerte"
    mandaban = "compradores" if zona["dominio_compra"] > 0.55 else \
               "vendedores" if zona["dominio_compra"] < 0.45 else "reparto"
    return f"zona {etiqueta}: {zona['toques']} toques, {mandaban} mandando"


def revisar(zona, precio, ahora):
    """
    Máquina de estados de una zona:
      fuera  → el precio entra en la zona → esperando
      esperando → pasados ESPERA_MINUTOS se juzga: rebote o cruce
    """
    dentro = abs(precio - zona["centro"]) <= zona["ancho"]

    # Mientras está claramente fuera, vamos apuntando de qué lado está.
    # Ese es el lado del que VIENE cuando entre — dentro de la zona ya no
    # se puede saber, y era el fallo de la primera versión.
    if not dentro:
        zona["ultimo_lado"] = "abajo" if precio < zona["centro"] else "arriba"

    if zona["estado"] == "fuera":
        if dentro:
            # si aún no sabemos de dónde venía (recién arrancado el bot),
            # no juzgamos este toque: esperamos al siguiente
            if not zona.get("ultimo_lado"):
                return None
            zona["estado"] = "esperando"
            zona["entrada"] = {
                "t": ahora,
                "lado": zona["ultimo_lado"],
                "papel": "resistencia" if zona["ultimo_lado"] == "abajo" else "soporte",
            }
        return None

    if zona["estado"] == "esperando":
        if ahora - zona["entrada"]["t"] < ESPERA_MINUTOS * 60:
            return None                      # todavía esperando reacción

        lado_antes = zona["entrada"]["lado"]
        lado_ahora = "abajo" if precio < zona["centro"] - zona["ancho"] else \
                     "arriba" if precio > zona["centro"] + zona["ancho"] else "dentro"
        zona["estado"] = "fuera"

        if lado_ahora == "dentro":
            return None                      # ni una cosa ni la otra, no molestamos

        if lado_ahora == lado_antes:
            sentido = "bajista" if lado_antes == "abajo" else "alcista"
            return ("rebote", sentido, zona["entrada"]["papel"])

        return ("cruce", lado_ahora, zona["entrada"]["papel"])

    return None


def revisar_seguimientos(pendientes, ahora):
    """
    A la hora, a las 4 y a las 24 reconstruye el recorrido de cada señal:
    cuánto se fue a favor, cuánto en contra, y si habría saltado antes el
    stop o el objetivo. Esto es lo que convierte el registro en estadística.
    """
    quedan = []
    for p in pendientes:
        objetivos = [o for o in p["objetivos"] if o["cuando"] > ahora]
        vencidos = [o for o in p["objetivos"] if o["cuando"] <= ahora]

        for o in vencidos:
            reg = p["reg"]
            try:
                rec = analizar_recorrido(
                    p["simbolo"], p["t_senal"], reg["precio_señal"],
                    reg["direccion"], (reg["zona"] or {}).get("atr"), o["cuando"])
            except Exception as e:
                log(f"Seguimiento {reg['id']}: no pude reconstruir el recorrido ({e})")
                objetivos.append(o)          # se reintenta en la vuelta siguiente
                continue

            if not rec:
                objetivos.append(o)
                continue

            rec["hora_utc"] = iso(ahora)
            reg["seguimiento"][o["clave"]] = rec

            log(f"Seguimiento {o['clave']} de {reg['id']}: "
                f"a favor {rec['a_favor']['atr']} ATR · "
                f"en contra {rec['en_contra']['atr']} ATR · "
                f"primero {rec['forma']['primero']} · "
                f"aguantar {rec['forma']['contra_antes_del_mejor_atr']} ATR · "
                f"{rec['simulacion']['desenlace']}")
            guardar_registro(reg)

        p["objetivos"] = objetivos
        if objetivos:
            quedan.append(p)

    return quedan


def main():
    log("Vigía de zonas arrancando.")
    log(f"Activos: {', '.join(SIMBOLOS)} | espera {ESPERA_MINUTOS} min")
    for s in SIMBOLOS:
        log(f"   umbral de {s}: {umbral_de(s)}")
    log(f"Registro en hoja: {'SÍ' if REGISTRO_URL else 'no (solo log)'}")
    log(f"Seguimiento: {VENTANAS_VELAS} velas de {TF_SENAL} tras cada senal")
    log(f"Simulacion: stop {SL_ATR} ATR · objetivo {TP_ATR} ATR · "
        f"recorrido medido con velas de {VELA_RECORRIDO}")

    zonas = {}
    pendientes = []
    ultimo_calculo = 0.0

    while True:
        ahora = time.time()

        # Recalcular zonas de vez en cuando (y al arrancar)
        if ahora - ultimo_calculo > RECALCULO_HORAS * 3600:
            for s in SIMBOLOS:
                try:
                    velas = traer_velas(s, "1h", 2000)
                    zonas[s] = construir_zonas(velas, s)
                    log(f"{s}: {len(velas)} velas, {len(zonas[s])} zonas fuertes "
                        f"(umbral {umbral_de(s)})")
                    for z in zonas[s][:6]:
                        log(f"    {fmt(z['centro'], s)}  fuerza {z['fuerza']:.1f}  "
                            f"{z['toques']} toques")
                except Exception as e:
                    log(f"Error calculando zonas de {s}: {e}")
            ultimo_calculo = ahora

        # Mirar el precio contra las zonas
        for s in SIMBOLOS:
            try:
                p = precio_actual(s)
            except Exception as e:
                log(f"No pude leer el precio de {s}: {e}")
                continue

            for z in zonas.get(s, []):
                r = revisar(z, p, ahora)
                if not r:
                    continue
                if ahora - z["ultimo_aviso"] < SILENCIO_HORAS * 3600:
                    continue

                tipo = r[0]
                activo = s.replace("USDT", "")
                nivel = fmt(z["centro"], s)

                if tipo == "rebote":
                    _, sentido, pap = r
                    texto = (f"{activo} — rebote {sentido} en {nivel}\n"
                             f"{describir(z)}\n"
                             f"Vigila gráfica.")
                else:
                    _, hacia, pap = r
                    que = "resistencia" if pap == "resistencia" else "soporte"
                    texto = (f"{activo} — cruza {que} de {nivel}\n"
                             f"{describir(z)}\n"
                             f"Ahora esa zona pasa a hacer de "
                             f"{'soporte' if que == 'resistencia' else 'resistencia'}. "
                             f"Vigila gráfica.")

                log("AVISO -> " + texto.replace("\n", " | "))
                telegram(texto)
                z["ultimo_aviso"] = ahora

                # ── registro completo de la señal ──
                try:
                    reg = construir_registro(s, z, r, p, ahora, texto)
                    guardar_registro(reg)
                    pendientes.append({
                        "simbolo": s,
                        "reg": reg,
                        "t_senal": ahora,
                        "objetivos": [
                            {"clave": f"{n}velas",
                             "cuando": ahora + n * _MIN_TF.get(TF_SENAL, 60) * 60}
                            for n in VENTANAS_VELAS
                        ],
                    })
                except Exception as e:
                    log(f"Error construyendo el registro: {e}")

        # Rellenar los seguimientos que toquen
        try:
            pendientes = revisar_seguimientos(pendientes, ahora)
        except Exception as e:
            log(f"Error en seguimientos: {e}")

        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
