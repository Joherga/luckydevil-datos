#!/usr/bin/env python3
"""Crea o actualiza datos.json para LuckyDevil (fusiona con el existente, sin duplicar fechas).

  python actualizar_datos.py --auto                 # lo que ejecuta GitHub Actions
  python actualizar_datos.py --primitiva p.csv --euromillones e.csv --eurodreams d.csv
  python actualizar_datos.py --api TU_CLAVE         # último sorteo de cada juego con loteriasapi.com
En modo --auto solo consulta un juego si ya debería haber un sorteo nuevo, para no gastar peticiones.
La clave de la API (opcional, respaldo) se lee de --api o de la variable LOTERIAS_API_KEY.
"""
import argparse, datetime as dt, json, os, re, urllib.request
from zoneinfo import ZoneInfo
CFG = {'primitiva': (6, 49), 'euromillones': (5, 50), 'eurodreams': (6, 40)}
DRAW = {'primitiva': ([0, 3, 5], 22 * 60 + 15), 'euromillones': ([1, 4], 22 * 60 + 30), 'eurodreams': ([0, 3], 21 * 60 + 15)}  # lun=0
DATE = re.compile(r'(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})')
UA = 'Mozilla/5.0 (compatible; LuckyDevilBot/1.0)'

def http(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode('utf-8', 'ignore')

def valid(g, e):
    n, m = CFG[g]
    if not (isinstance(e, list) and len(e) >= 3 and re.match(r'\d{4}-\d{2}-\d{2}$', str(e[0]))): return False
    a = e[1]
    if not (isinstance(a, list) and len(a) == n and all(isinstance(x, int) and 1 <= x <= m for x in a) and len(set(a)) == n): return False
    if g == 'euromillones': return isinstance(e[2], list) and len(e[2]) == 2 and all(1 <= x <= 12 for x in e[2]) and e[2][0] != e[2][1]
    if g == 'eurodreams': return isinstance(e[2], int) and 1 <= e[2] <= 5
    return True

def parse_csv(game, text):
    n, m = CFG[game]; out = {}
    for line in text.lstrip('\ufeff').splitlines():
        mt = DATE.search(line)
        if not mt: continue
        d = mt.group(1)
        if not re.match(r'\d{4}', d):
            a, b, y = re.split(r'[/-]', d); d = f'{y}-{int(b):02d}-{int(a):02d}'
        v = [int(x) for x in re.findall(r'\d+', line[mt.end():])]
        nums, e = v[:n], v[n:]
        if game == 'primitiva': ent = [d, sorted(nums), e[0] if e and 1 <= e[0] <= 49 else None, e[1] if len(e) > 1 and 0 <= e[1] <= 9 else None]
        elif game == 'euromillones': ent = [d, sorted(nums), sorted(e[:2])]
        else: ent = [d, sorted(nums), e[0] if e else 0]
        if valid(game, ent): out[d] = ent
    return list(out.values())

# --- fuentes gratuitas (se prueban primero) ---
def src_eurodreams(desde=None):
    d = json.loads(http('https://juhuj.com/api/derniers-tirages.json'))['games']['eurodreams']
    return [[d['date'], sorted(int(x) for x in d['balls']), int(d['extras'][0])]]
def src_euromillones(desde=None):
    return parse_csv('euromillones', http('https://raw.githubusercontent.com/daowa89/lottery-archive/main/eu/euromillions/results.csv'))
def src_primitiva(desde=None):
    hoy = dt.date.today(); ini = desde or hoy - dt.timedelta(days=21); out = []
    while ini <= hoy:  # ventanas de 60 días para no pedir rangos enormes de una vez
        fin = min(ini + dt.timedelta(days=60), hoy)
        url = f'https://www.loteriasyapuestas.es/servicios/buscadorSorteos?game_id=LAPR&celebrados=true&fechaInicioInicial={ini:%Y%m%d}&fechaFinInicial={fin:%Y%m%d}'
        for it in json.loads(http(url)):
            f = str(it.get('fecha_sorteo', ''))[:10]; v = [int(x) for x in re.findall(r'\d+', str(it.get('combinacion', '')))]
            if len(v) >= 6: out.append([f, sorted(v[:6]), v[6] if len(v) > 6 and 1 <= v[6] <= 49 else None, v[7] if len(v) > 7 and 0 <= v[7] <= 9 else None])
        ini = fin + dt.timedelta(days=1)
    return out
SRC = {'primitiva': src_primitiva, 'euromillones': src_euromillones, 'eurodreams': src_eurodreams}

# --- API de pago/prueba (respaldo) ---
def api_latest(game, key):
    d = (json.loads(http(f'https://api.loteriasapi.com/api/v1/results/{game}/latest', {'X-API-Key': key})).get('data')) or {}
    if d.get('status') not in (None, 'COMPLETED'): return []
    nums, rd = sorted(int(x) for x in d['combination']), d.get('resultData') or {}
    def pick(rx):
        for k, v in rd.items():
            if re.search(rx, k, re.I) and not isinstance(v, (dict, list)) and v not in (None, ''): return int(v)
    date = d['drawDate']
    if game == 'primitiva': return [[date, nums, pick('compl'), pick('reint|refund')]]
    if game == 'euromillones': return [[date, nums, sorted(int(x) for x in rd.get('estrellas', []))]]
    return [[date, nums, pick('dream|sue') or 0]]

def expected(game, now):
    days, mins = DRAW[game]; d = now
    if not (d.weekday() in days and d.hour * 60 + d.minute >= mins): d -= dt.timedelta(days=1)
    while d.weekday() not in days: d -= dt.timedelta(days=1)
    return d.date().isoformat()

def add(cur, nuevos, g):
    k = 0
    for e in nuevos:
        if valid(g, e) and e[0] not in cur: cur[e[0]] = e; k += 1
    return k

def auto(feed, key, now=None):
    now = now or dt.datetime.now(ZoneInfo('Europe/Madrid'))
    for g in CFG:
        cur = {e[0]: e for e in feed.get(g, [])}; exp = expected(g, now); last = max(cur) if cur else ''
        if last >= exp: print(f'{g}: al día ({last})'); continue
        print(f'{g}: falta el sorteo del {exp} (último guardado: {last or "ninguno"})')
        fuentes = [('fuente gratuita', SRC[g])] + ([('API', lambda g=g: api_latest(g, key))] if key else [])
        for nombre, fn in fuentes:
            try:
                n = add(cur, fn(), g); print(f'  {nombre}: OK ({n} nuevos)')
                if cur and max(cur) >= exp: break
            except Exception as ex: print(f'  {nombre}: falló ({type(ex).__name__}: {ex})')
        feed[g] = sorted(cur.values(), key=lambda e: e[0])

def main():
    ap = argparse.ArgumentParser()
    for g in CFG: ap.add_argument('--' + g, help=f'CSV de {g}')
    ap.add_argument('--api'); ap.add_argument('--auto', action='store_true'); ap.add_argument('--salida', default='datos.json')
    ap.add_argument('--rellenar', metavar='AAAA-MM-DD', help='rellena huecos de Primitiva y Euromillones desde esa fecha')
    a = ap.parse_args(); key = a.api or os.environ.get('LOTERIAS_API_KEY', '')
    feed = json.load(open(a.salida, encoding='utf-8')) if os.path.exists(a.salida) else {'v': 1}
    if a.auto: auto(feed, key)
    if a.rellenar:
        try: desde = dt.date.fromisoformat(a.rellenar)
        except ValueError: raise SystemExit('Fecha no válida: usa AAAA-MM-DD, por ejemplo 2026-06-23')
        for g in ('primitiva', 'euromillones'):
            cur = {e[0]: e for e in feed.get(g, [])}
            try: n = add(cur, SRC[g](desde), g); print(f'{g}: relleno desde {desde} OK ({n} nuevos)')
            except Exception as ex: print(f'{g}: relleno falló ({type(ex).__name__}: {ex})')
            feed[g] = sorted(cur.values(), key=lambda e: e[0])
    else:
        for g in CFG:
            cur = {e[0]: e for e in feed.get(g, [])}; new = []
            if getattr(a, g): new += parse_csv(g, open(getattr(a, g), encoding='utf-8', errors='ignore').read())
            if a.api:
                try: new += api_latest(g, a.api)
                except Exception as ex: print(f'{g}: API no disponible ({ex})')
            n = add(cur, new, g); feed[g] = sorted(cur.values(), key=lambda e: e[0]); print(f'{g}: {len(feed[g])} sorteos ({n} nuevos)')
    old = json.load(open(a.salida, encoding='utf-8')) if os.path.exists(a.salida) else {}
    if any(feed.get(g) != old.get(g) for g in CFG): feed['actualizado'] = dt.date.today().isoformat()
    json.dump(feed, open(a.salida, 'w', encoding='utf-8'), separators=(',', ':')); print('Guardado', a.salida)

if __name__ == '__main__': main()
