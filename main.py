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

Datos: API pública de Binance. Gratis, sin clave, tiempo real.
"""

import os
import time
import json
import statistics
from datetime import datetime, timezone

import requests

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
UMBRAL_FUERZA = float(os.getenv("UMBRAL_FUERZA", "3.0"))

# Tras avisar de una zona, cuántas horas se calla sobre esa misma zona
SILENCIO_HORAS = float(os.getenv("SILENCIO_HORAS", "4"))

# Cada cuántas horas recalcula las zonas desde cero
RECALCULO_HORAS = float(os.getenv("RECALCULO_HORAS", "12"))

# Vida media de un toque, en días: uno de hace 30 días pesa la mitad
VIDA_MEDIA_DIAS = float(os.getenv("VIDA_MEDIA_DIAS", "30"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BINANCE = "https://api.binance.com"


# ─────────────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def telegram(texto):
    """Manda el aviso al móvil de Charlie."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log(f"(sin Telegram configurado) {texto}")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": texto},
            timeout=15,
        )
        if r.status_code != 200:
            log(f"Telegram respondió {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"Error mandando Telegram: {e}")


def fmt(x, simbolo):
    """Formatea el precio con los decimales que tocan y coma decimal."""
    dec = 4 if x < 10 else 2
    return f"{x:.{dec}f}".replace(".", ",")


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
        r = requests.get(f"{BINANCE}/api/v3/klines", params=params, timeout=25)
        r.raise_for_status()
        lote = r.json()
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
    r = requests.get(f"{BINANCE}/api/v3/ticker/price",
                     params={"symbol": simbolo}, timeout=15)
    r.raise_for_status()
    return float(r.json()["price"])


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
            z["centro"] = sum(z["precios"]) / len(z["precios"])
        else:
            zonas.append({
                "centro": precio, "precios": [precio], "pesos": [peso],
                "tipos": [tipo], "compra": [prop_compra],
            })

    for z in zonas:
        z["simbolo"] = simbolo
        z["ancho"] = medio_ancho
        z["fuerza"] = sum(z["pesos"])
        z["toques"] = len(z["precios"])
        z["dominio_compra"] = sum(z["compra"]) / len(z["compra"])
        z["id"] = f"{simbolo}:{z['centro']:.6f}"
        z["ultimo_aviso"] = 0.0
        z["estado"] = "fuera"
        z["entrada"] = None
        z["ultimo_lado"] = None

    fuertes = [z for z in zonas if z["fuerza"] >= UMBRAL_FUERZA]
    fuertes.sort(key=lambda z: -z["fuerza"])
    return fuertes


def papel(zona, precio):
    """Si el precio está por debajo, la zona le hace de techo. Y al revés."""
    return "resistencia" if precio < zona["centro"] else "soporte"


# ─────────────────────────────────────────────────────────────────────
# VIGILANCIA
# ─────────────────────────────────────────────────────────────────────

def describir(zona):
    fuerza = zona["fuerza"]
    etiqueta = "MUY FUERTE" if fuerza >= UMBRAL_FUERZA * 2 else "fuerte"
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


def main():
    log("Vigía de zonas arrancando.")
    log(f"Activos: {', '.join(SIMBOLOS)} | espera {ESPERA_MINUTOS} min | "
        f"fuerza mínima {UMBRAL_FUERZA}")

    zonas = {}
    ultimo_calculo = 0.0

    while True:
        ahora = time.time()

        # Recalcular zonas de vez en cuando (y al arrancar)
        if ahora - ultimo_calculo > RECALCULO_HORAS * 3600:
            for s in SIMBOLOS:
                try:
                    velas = traer_velas(s, "1h", 2000)
                    zonas[s] = construir_zonas(velas, s)
                    log(f"{s}: {len(velas)} velas, {len(zonas[s])} zonas fuertes")
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

        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
