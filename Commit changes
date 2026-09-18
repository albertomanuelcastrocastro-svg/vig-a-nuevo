"""
BARRIDO DE PARAMETROS DEL VIGIA
===============================
Busca el mejor ajuste posible del bot, y despues comprueba si ese ajuste
vale para algo de verdad o si solo estaba ajustado al pasado.

EL METODO, que es lo que hace que el resultado signifique algo:

  · El historico se parte en DOS. La busqueda solo ve la PRIMERA mitad.
  · El mejor ajuste encontrado se prueba luego en la SEGUNDA mitad, que
    no ha visto nunca. Si ahi sigue ganando, puede ser real. Si se cae,
    era curva ajustada al pasado y no sirve.
  · Se cuenta cuantas combinaciones se han probado. Cuantas mas pruebas,
    mas facil es que la mejor sea pura suerte, y hay que decirlo.
  · Se prueba tambien la señal INVERTIDA: si el bot se equivoca siempre,
    lo contrario del bot es un sistema.

Se ejecuta en el contenedor de Railway:  python barrido.py
"""

import json
import time
import random
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SIMBOLOS       = ["XRPUSDT", "SOLUSDT"]
TF             = "1h"
VELAS_HISTORIA = 2000
VELAS_TEST     = 6000
RECALCULO      = 12
VIDA_MEDIA     = 30
SILENCIO       = 4

# ── rejilla de busqueda ──
G_ANCHO   = [0.15, 0.25, 0.40]
G_GIRO    = [0.7, 1.0, 1.5]
G_ESPERA  = [1, 2]
G_UMBRAL  = [0, 10, 20, 30, 40]
G_VENT    = [5, 10, 15, 24]
G_SLTP    = [(1.0, 2.0), (1.0, 3.0), (0.7, 1.5), (1.5, 3.0), (2.0, 2.0), (0.5, 1.0)]
G_SENTIDO = [1, -1]          # 1 = como dice el bot · -1 = al reves

BINANCE = "https://api.binance.com"
random.seed(11)


def pedir(url, p):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(p),
                                 headers={"User-Agent": "barrido/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def traer(simbolo, total):
    velas, fin = [], None
    while len(velas) < total:
        p = {"symbol": simbolo, "interval": TF, "limit": min(1000, total-len(velas))}
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
    return [{"t": int(v[0]), "max": float(v[2]), "min": float(v[3]),
             "cierra": float(v[4]), "vol": float(v[5]),
             "vc": float(v[9])} for v in velas]


def atr(velas, periodo=14):
    r = []
    for i in range(1, len(velas)):
        a, b = velas[i-1], velas[i]
        r.append(max(b["max"]-b["min"], abs(b["max"]-a["cierra"]), abs(b["min"]-a["cierra"])))
    return sum(r[-periodo:]) / min(periodo, len(r)) if r else 0.0


def giros(velas, umbral):
    g, d = [], 1
    ei, ep = 0, velas[0]["max"]
    for i, v in enumerate(velas):
        if d == 1:
            if v["max"] > ep:
                ei, ep = i, v["max"]
            elif ep - v["min"] > umbral:
                g.append((ei, ep)); d, ei, ep = -1, i, v["min"]
        else:
            if v["min"] < ep:
                ei, ep = i, v["min"]
            elif v["max"] - ep > umbral:
                g.append((ei, ep)); d, ei, ep = 1, i, v["max"]
    return g


def zonas_de(velas, ancho_atr, giro_atr):
    a = atr(velas)
    if a <= 0 or len(velas) < 50:
        return [], 0.0
    medio = a * ancho_atr
    gs = giros(velas, a * giro_atr)
    if not gs:
        return [], a
    vt = statistics.median(v["vol"] for v in velas) or 1.0
    ahora = velas[-1]["t"]
    zs = []
    for idx, precio in sorted(gs, key=lambda x: x[1]):
        v = velas[idx]
        peso = min(v["vol"]/vt, 5.0) * (0.5 ** (((ahora-v["t"])/86_400_000)/VIDA_MEDIA))
        if zs and abs(precio - zs[-1]["c"]) <= medio*2:
            z = zs[-1]; z["ps"].append(precio); z["f"] += peso
            z["c"] = sum(z["ps"])/len(z["ps"])
        else:
            zs.append({"c": precio, "ps": [precio], "f": peso})
    for z in zs:
        z["ancho"] = medio
        z["atr"] = a
        z["toques"] = len(z["ps"])
        z["estado"], z["lado"], z["ent"], z["sil"] = "fuera", None, None, -999
    return zs, a


def detectar(velas, ancho_atr, giro_atr, espera):
    """Todas las señales SIN filtrar por fuerza: el umbral se aplica despues."""
    senales, zs = [], []
    ini, fin = VELAS_HISTORIA, len(velas) - max(G_VENT) - 2
    for i in range(ini, fin):
        if (i - ini) % RECALCULO == 0:
            zs, _ = zonas_de(velas[i-VELAS_HISTORIA:i], ancho_atr, giro_atr)
        p = velas[i]["cierra"]
        for z in zs:
            dentro = abs(p - z["c"]) <= z["ancho"]
            if not dentro:
                z["lado"] = "abajo" if p < z["c"] else "arriba"
            if z["estado"] == "fuera":
                if dentro and z["lado"]:
                    z["estado"], z["ent"] = "esperando", i
                continue
            if i - z["ent"] < espera:
                continue
            l0 = z["lado"]
            l1 = ("abajo" if p < z["c"]-z["ancho"]
                  else "arriba" if p > z["c"]+z["ancho"] else "dentro")
            z["estado"] = "fuera"
            if l1 == "dentro" or i - z["sil"] < SILENCIO:
                continue
            z["sil"] = i
            senales.append({"i": i, "p": p, "atr": z["atr"], "f": z["f"],
                            "toques": z["toques"],
                            "d": 1 if l1 == "arriba" else -1,
                            "tipo": "rebote" if l1 == l0 else "cruce"})
    return senales


def evaluar(velas, s, direccion, n, sl, tp):
    """Resultado en ATR de una señal con un stop y un objetivo dados."""
    tramo = velas[s["i"]+1:s["i"]+1+n]
    a = s["atr"]
    if not tramo or not a:
        return None
    p0 = s["p"]
    ntp = p0 + direccion*tp*a
    nsl = p0 - direccion*sl*a
    for v in tramo:
        if direccion > 0:
            t_tp, t_sl = v["max"] >= ntp, v["min"] <= nsl
        else:
            t_tp, t_sl = v["min"] <= ntp, v["max"] >= nsl
        if t_sl:
            return -sl            # empate en la misma vela: manda el stop
        if t_tp:
            return tp
    return (tramo[-1]["cierra"] - p0) * direccion / a     # se cierra al final


def main():
    print("="*112)
    print("BARRIDO DE PARAMETROS · optimiza en la 1a mitad · comprueba en la 2a")
    print("="*112, flush=True)

    datos = {}
    for s in SIMBOLOS:
        v = traer(s, VELAS_HISTORIA + VELAS_TEST + max(G_VENT) + 5)
        datos[s] = v
        print(f"{s}: {len(v)} velas "
              f"{datetime.fromtimestamp(v[0]['t']/1000, timezone.utc):%Y-%m-%d} -> "
              f"{datetime.fromtimestamp(v[-1]['t']/1000, timezone.utc):%Y-%m-%d}", flush=True)

    corte = VELAS_HISTORIA + int(VELAS_TEST*0.6)     # frontera 1a / 2a mitad
    print(f"corte en la vela {corte}: busqueda a la izquierda, comprobacion a la derecha\n",
          flush=True)

    # deteccion: una pasada por cada (ancho, giro, espera)
    cache = {}
    for an in G_ANCHO:
        for gi in G_GIRO:
            for es in G_ESPERA:
                todas = []
                for s in SIMBOLOS:
                    for x in detectar(datos[s], an, gi, es):
                        x["s"] = s
                        todas.append(x)
                cache[(an, gi, es)] = todas
                print(f"  ancho {an} · giro {gi} · espera {es} -> {len(todas)} señales",
                      flush=True)

    resultados, probadas = [], 0
    for (an, gi, es), senales in cache.items():
        for um in G_UMBRAL:
            base = [x for x in senales if x["f"] >= um]
            if len(base) < 60:
                continue
            for n in G_VENT:
                for (sl, tp) in G_SLTP:
                    for sent in G_SENTIDO:
                        probadas += 1
                        dentro, fuera = [], []
                        for x in base:
                            r = evaluar(datos[x["s"]], x, x["d"]*sent, n, sl, tp)
                            if r is None:
                                continue
                            (dentro if x["i"] < corte else fuera).append(r)
                        if len(dentro) < 40 or len(fuera) < 25:
                            continue
                        resultados.append({
                            "an": an, "gi": gi, "es": es, "um": um, "n": n,
                            "sl": sl, "tp": tp, "sent": sent,
                            "n_in": len(dentro), "esp_in": sum(dentro)/len(dentro),
                            "n_out": len(fuera), "esp_out": sum(fuera)/len(fuera),
                            "win_in": 100*sum(1 for r in dentro if r > 0)/len(dentro),
                            "win_out": 100*sum(1 for r in fuera if r > 0)/len(fuera),
                        })

    if not resultados:
        print("No hay combinaciones con muestra suficiente.")
        return

    resultados.sort(key=lambda r: -r["esp_in"])
    print(f"\nCombinaciones probadas: {probadas}   ·   con muestra valida: {len(resultados)}")
    print("\n" + "="*112)
    print("LAS 12 MEJORES DE LA PRIMERA MITAD  ·  y que hicieron despues en la segunda")
    print("="*112)
    print(f"{'ancho':>6}{'giro':>6}{'esp':>5}{'umbr':>6}{'vent':>6}{'SL':>5}{'TP':>5}"
          f"{'sent':>6} | {'n':>5}{'esp 1a':>9}{'win':>6} | {'n':>5}{'esp 2a':>9}{'win':>6}")
    print("-"*112)
    for r in resultados[:12]:
        print(f"{r['an']:>6}{r['gi']:>6}{r['es']:>5}{r['um']:>6}{r['n']:>6}"
              f"{r['sl']:>5}{r['tp']:>5}{('bot' if r['sent']>0 else 'INV'):>6} | "
              f"{r['n_in']:>5}{r['esp_in']:>+9.3f}{r['win_in']:>5.0f}% | "
              f"{r['n_out']:>5}{r['esp_out']:>+9.3f}{r['win_out']:>5.0f}%")

    mejor = resultados[0]
    sobreviven = [r for r in resultados[:12] if r["esp_out"] > 0]
    media_out = sum(r["esp_out"] for r in resultados)/len(resultados)

    print("\n" + "="*112)
    print("VEREDICTO")
    print(f"  mejor ajuste en la 1a mitad : {mejor['esp_in']:+.3f} ATR/señal "
          f"({mejor['n_in']} señales)")
    print(f"  ese mismo en la 2a mitad    : {mejor['esp_out']:+.3f} ATR/señal "
          f"({mejor['n_out']} señales)")
    print(f"  de las 12 mejores, positivas fuera de muestra: {len(sobreviven)} de 12")
    print(f"  media de TODAS las combinaciones en la 2a mitad: {media_out:+.3f} ATR")
    print(f"  combinaciones probadas: {probadas}  (cuantas mas, mas facil acertar por suerte)")
    if mejor["esp_out"] <= 0:
        print("  -> El mejor ajuste se cae fuera de muestra. Era curva ajustada al pasado.")
    elif len(sobreviven) < 6:
        print("  -> Aguanta el mejor, pero sus vecinos no. Sospechoso de casualidad.")
    elif media_out > 0:
        print("  -> Aguanta el mejor Y la media general es positiva. Esto merece mirarse en serio.")
    else:
        print("  -> Aguanta el mejor y varios vecinos, pero la media general no. Dudoso.")
    print("="*112)

    # ¿y si el problema es el sentido?
    for etiqueta, sent in (("como dice el bot", 1), ("al reves", -1)):
        sub = [r for r in resultados if r["sent"] == sent]
        if sub:
            print(f"  señal {etiqueta:<18}: media 1a {sum(r['esp_in'] for r in sub)/len(sub):+.3f}"
                  f"   media 2a {sum(r['esp_out'] for r in sub)/len(sub):+.3f}   ({len(sub)} combinaciones)")


if __name__ == "__main__":
    main()
