"""
BACKTEST DEL VIGIA DE ZONAS
===========================
Reproduce EXACTAMENTE la logica del bot sobre el historico y mide, senal
a senal, que hizo el precio despues. Sirve para contestar una sola
pregunta: merece la pena hacerle caso a este bot, o no.

Dos cuidados para que el resultado no sea un cuento:

  1. SIN MIRAR EL FUTURO. Las zonas de cada tramo se calculan solo con las
     velas ANTERIORES, igual que hace el bot en vivo al recalcular cada 12h.
  2. CON GRUPO DE CONTROL. Por cada senal se mide tambien una entrada
     tomada al azar en el mismo periodo y el mismo activo. Si el bot no bate
     claramente al azar, el bot no sirve, por bonitos que sean sus numeros.

Se ejecuta dentro del contenedor de Railway (alli si hay acceso a Binance):
    python backtest.py
"""

import json
import time
import random
import statistics
import urllib.request
from datetime import datetime, timezone

# ── mismos ajustes que el bot en vivo ──
SIMBOLOS        = ["XRPUSDT", "SOLUSDT"]
TF              = "1h"
VELAS_HISTORIA  = 2000     # ventana con la que el bot construye zonas
VELAS_TEST      = 6000     # cuanto pasado se examina (6000 h ~ 8 meses)
ANCHO_ZONA_ATR  = 0.25
GIRO_MINIMO_ATR = 1.0
UMBRAL_FUERZA   = 20.0
VIDA_MEDIA_DIAS = 30
ESPERA_VELAS    = 1        # 15 min en vivo; en 1h la vela siguiente
SILENCIO_VELAS  = 4
RECALCULO_VELAS = 12
VENTANAS        = [5, 10, 15]
SL_ATR, TP_ATR  = 1.0, 2.0

BINANCE = "https://api.binance.com"
random.seed(7)             # mismo control en cada pasada


def pedir(url, params):
    import urllib.parse
    url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "backtest/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def traer(simbolo, total):
    velas, fin = [], None
    while len(velas) < total:
        p = {"symbol": simbolo, "interval": TF, "limit": min(1000, total - len(velas))}
        if fin:
            p["endTime"] = fin
        lote = pedir(f"{BINANCE}/api/v3/klines", p)
        if not lote:
            break
        velas = lote + velas
        fin = lote[0][0] - 1
        if len(lote) < p["limit"]:
            break
        time.sleep(0.3)
    return [{"t": int(v[0]), "abre": float(v[1]), "max": float(v[2]),
             "min": float(v[3]), "cierra": float(v[4]), "vol": float(v[5]),
             "vol_comprador": float(v[9])} for v in velas]


# ───────────────────────── la logica del bot, tal cual ─────────────────────

def atr(velas, periodo=14):
    r = []
    for i in range(1, len(velas)):
        a, b = velas[i-1], velas[i]
        r.append(max(b["max"]-b["min"], abs(b["max"]-a["cierra"]), abs(b["min"]-a["cierra"])))
    return sum(r[-periodo:]) / min(periodo, len(r)) if r else 0.0


def buscar_giros(velas, umbral):
    giros, direccion = [], 1
    ext_i, ext_p = 0, velas[0]["max"]
    for i, v in enumerate(velas):
        if direccion == 1:
            if v["max"] > ext_p:
                ext_i, ext_p = i, v["max"]
            elif ext_p - v["min"] > umbral:
                giros.append((ext_i, ext_p, "techo")); direccion, ext_i, ext_p = -1, i, v["min"]
        else:
            if v["min"] < ext_p:
                ext_i, ext_p = i, v["min"]
            elif v["max"] - ext_p > umbral:
                giros.append((ext_i, ext_p, "suelo")); direccion, ext_i, ext_p = 1, i, v["max"]
    return giros


def construir_zonas(velas):
    if len(velas) < 50:
        return []
    a = atr(velas)
    if a <= 0:
        return []
    medio = a * ANCHO_ZONA_ATR
    giros = buscar_giros(velas, a * GIRO_MINIMO_ATR)
    if not giros:
        return []
    vol_tipico = statistics.median(v["vol"] for v in velas) or 1.0
    ahora = velas[-1]["t"]

    zonas = []
    for idx, precio, tipo in sorted(giros, key=lambda g: g[1]):
        v = velas[idx]
        peso = min(v["vol"]/vol_tipico, 5.0) * (0.5 ** (((ahora-v["t"])/86_400_000)/VIDA_MEDIA_DIAS))
        compra = v["vol_comprador"]/v["vol"] if v["vol"] else 0.5
        if zonas and abs(precio - zonas[-1]["centro"]) <= medio*2:
            z = zonas[-1]
            z["ps"].append(precio); z["pesos"].append(peso); z["compra"].append(compra)
            z["centro"] = sum(z["ps"])/len(z["ps"])
        else:
            zonas.append({"centro": precio, "ps": [precio], "pesos": [peso], "compra": [compra]})

    for z in zonas:
        z["ancho"] = medio
        z["atr"] = a
        z["fuerza"] = sum(z["pesos"])
        z["toques"] = len(z["ps"])
        z["dominio"] = sum(z["compra"])/len(z["compra"])
        z["estado"], z["ultimo_lado"], z["entrada"], z["silencio"] = "fuera", None, None, -999
    return [z for z in zonas if z["fuerza"] >= UMBRAL_FUERZA]


# ───────────────────────── medicion del recorrido ──────────────────────────

def medir(velas, i0, precio0, direccion, a, n):
    """Que hizo el precio en las n velas siguientes a la senal."""
    tramo = velas[i0+1:i0+1+n]
    if not tramo or not a:
        return None
    mejor = peor = 0.0
    i_mejor = i_peor = None
    antes_contra = antes_favor = 0.0
    corre_f = corre_c = 0.0
    tp = precio0 + direccion*TP_ATR*a
    sl = precio0 - direccion*SL_ATR*a
    fin = "abierta"

    for k, v in enumerate(tramo):
        if direccion > 0:
            f, c = v["max"]-precio0, precio0-v["min"]
            toca_tp, toca_sl = v["max"] >= tp, v["min"] <= sl
        else:
            f, c = precio0-v["min"], v["max"]-precio0
            toca_tp, toca_sl = v["min"] <= tp, v["max"] >= sl
        if f > mejor:
            mejor, i_mejor, antes_contra = f, k, corre_c
        if c > peor:
            peor, i_peor, antes_favor = c, k, corre_f
        corre_f, corre_c = max(corre_f, f), max(corre_c, c)
        if fin == "abierta" and (toca_tp or toca_sl):
            fin = "SL" if toca_sl else "TP"     # empate en la misma vela: manda el stop

    neto = (tramo[-1]["cierra"] - precio0) * direccion
    return {
        "favor": mejor/a, "contra": peor/a, "cierre": neto/a,
        "aguantar": antes_contra/a, "devuelto": antes_favor/a,
        "primero": ("favor" if (i_mejor is not None and (i_peor is None or i_mejor < i_peor))
                    else "contra" if i_peor is not None else None),
        "fin": fin,
    }


# ───────────────────────── recorrido del historico ─────────────────────────

def barrer(simbolo):
    total = VELAS_HISTORIA + VELAS_TEST + max(VENTANAS) + 5
    velas = traer(simbolo, total)
    print(f"{simbolo}: {len(velas)} velas "
          f"({datetime.fromtimestamp(velas[0]['t']/1000, timezone.utc):%Y-%m-%d} "
          f"-> {datetime.fromtimestamp(velas[-1]['t']/1000, timezone.utc):%Y-%m-%d})",
          flush=True)

    senales, zonas = [], []
    inicio = VELAS_HISTORIA
    fin = len(velas) - max(VENTANAS) - 2

    for i in range(inicio, fin):
        # zonas recalculadas cada 12 velas, SOLO con el pasado
        if (i - inicio) % RECALCULO_VELAS == 0:
            zonas = construir_zonas(velas[i-VELAS_HISTORIA:i])

        p = velas[i]["cierra"]
        for z in zonas:
            dentro = abs(p - z["centro"]) <= z["ancho"]
            if not dentro:
                z["ultimo_lado"] = "abajo" if p < z["centro"] else "arriba"

            if z["estado"] == "fuera":
                if dentro and z["ultimo_lado"]:
                    z["estado"], z["entrada"] = "esperando", i
                continue

            if i - z["entrada"] < ESPERA_VELAS:
                continue

            lado0 = z["ultimo_lado"]
            lado1 = ("abajo" if p < z["centro"]-z["ancho"]
                     else "arriba" if p > z["centro"]+z["ancho"] else "dentro")
            z["estado"] = "fuera"
            if lado1 == "dentro":
                continue
            if i - z["silencio"] < SILENCIO_VELAS:
                continue
            z["silencio"] = i

            tipo = "rebote" if lado1 == lado0 else "cruce"
            direccion = 1 if lado1 == "arriba" else -1

            s = {"i": i, "t": velas[i]["t"], "tipo": tipo, "direccion": direccion,
                 "toques": z["toques"], "fuerza": z["fuerza"], "dominio": z["dominio"],
                 "precio": p, "atr": z["atr"], "vent": {}}
            for n in VENTANAS:
                s["vent"][n] = medir(velas, i, p, direccion, z["atr"], n)
            if s["vent"][VENTANAS[-1]]:
                senales.append(s)

    # ── grupo de control: mismas veces, momentos al azar ──
    control = []
    a_global = atr(velas[-VELAS_HISTORIA:])
    for _ in range(len(senales)):
        i = random.randint(inicio, fin-1)
        d = random.choice([1, -1])
        m = {"vent": {}}
        for n in VENTANAS:
            m["vent"][n] = medir(velas, i, velas[i]["cierra"], d, a_global, n)
        if m["vent"][VENTANAS[-1]]:
            control.append(m)

    return senales, control


# ───────────────────────── cuentas y resultado ─────────────────────────────

def media(xs):
    return sum(xs)/len(xs) if xs else 0.0


def resumen(nombre, muestras, n):
    v = [s["vent"][n] for s in muestras if s["vent"].get(n)]
    if not v:
        return None
    tp = sum(1 for x in v if x["fin"] == "TP")
    sl = sum(1 for x in v if x["fin"] == "SL")
    return {
        "nombre": nombre, "n": len(v),
        "favor": media([x["favor"] for x in v]),
        "contra": media([x["contra"] for x in v]),
        "cierre": media([x["cierre"] for x in v]),
        "aguantar": media([x["aguantar"] for x in v]),
        "acierto": 100.0*sum(1 for x in v if x["cierre"] > 0)/len(v),
        "tp": 100.0*tp/len(v), "sl": 100.0*sl/len(v),
        "esperanza": (tp*TP_ATR - sl*SL_ATR)/len(v),
        "primero_contra": 100.0*sum(1 for x in v if x["primero"] == "contra")/len(v),
    }


def linea(r):
    return (f"  {r['nombre']:<22} n={r['n']:<5} "
            f"favor {r['favor']:+.2f}  contra {r['contra']:+.2f}  "
            f"cierre {r['cierre']:+.2f}  aguantar {r['aguantar']:.2f}  "
            f"acierto {r['acierto']:.0f}%  TP {r['tp']:.0f}% / SL {r['sl']:.0f}%  "
            f"esperanza {r['esperanza']:+.2f} ATR")


def main():
    print("=" * 110)
    print("BACKTEST VIGIA DE ZONAS  ·  todo en ATR  ·  sin mirar el futuro")
    print(f"umbral fuerza {UMBRAL_FUERZA} · stop {SL_ATR} ATR · objetivo {TP_ATR} ATR")
    print("=" * 110)

    todo, todo_ctrl = [], []
    for s in SIMBOLOS:
        try:
            senales, control = barrer(s)
        except Exception as e:
            print(f"{s}: ERROR {e}", flush=True)
            continue
        print(f"{s}: {len(senales)} senales detectadas\n", flush=True)
        for x in senales:
            x["simbolo"] = s
        todo += senales
        todo_ctrl += control

    if not todo:
        print("Sin senales. Nada que medir.")
        return

    for n in VENTANAS:
        print(f"\n─── VENTANA DE {n} VELAS ({n} horas) " + "─"*60)
        filas = [resumen("TODAS las senales", todo, n),
                 resumen("· al azar (control)", todo_ctrl, n),
                 resumen("· solo rebotes", [s for s in todo if s["tipo"] == "rebote"], n),
                 resumen("· solo cruces", [s for s in todo if s["tipo"] == "cruce"], n),
                 resumen("· zona >= 15 toques", [s for s in todo if s["toques"] >= 15], n),
                 resumen("· zona < 15 toques", [s for s in todo if s["toques"] < 15], n),
                 resumen("· fuerza >= 30", [s for s in todo if s["fuerza"] >= 30], n),
                 resumen("· alcistas", [s for s in todo if s["direccion"] > 0], n),
                 resumen("· bajistas", [s for s in todo if s["direccion"] < 0], n)]
        for r in filas:
            if r:
                print(linea(r))

    # veredicto sobre la ventana mediana
    n = VENTANAS[len(VENTANAS)//2]
    bot, ctrl = resumen("bot", todo, n), resumen("azar", todo_ctrl, n)
    print("\n" + "=" * 110)
    print(f"VEREDICTO  (ventana de {n} velas)")
    print(f"  el bot  : acierto {bot['acierto']:.0f}%  esperanza {bot['esperanza']:+.2f} ATR")
    print(f"  el azar : acierto {ctrl['acierto']:.0f}%  esperanza {ctrl['esperanza']:+.2f} ATR")
    d = bot["esperanza"] - ctrl["esperanza"]
    print(f"  ventaja : {d:+.2f} ATR por senal")
    if d <= 0.05:
        print("  -> El bot NO bate al azar. Con estos ajustes no vale para operar.")
    elif d < 0.20:
        print("  -> Ventaja pequena. Puede ser ruido; hacen falta mas senales.")
    else:
        print("  -> Hay ventaja real. Merece la pena seguir midiendo en vivo.")
    print("=" * 110)

    with open("/tmp/backtest.json", "w") as f:
        json.dump({"senales": len(todo), "ventanas": VENTANAS,
                   "resumen": {str(n): [resumen("todas", todo, n),
                                        resumen("azar", todo_ctrl, n)] for n in VENTANAS}},
                  f, indent=1)
    print("detalle en /tmp/backtest.json")


if __name__ == "__main__":
    main()
