#!/usr/bin/env python3
"""LuckyDevil · actualiza datos.json (versión 3: cuatro juegos, CSV subidos al repositorio y diagnóstico de la API).

  python actualizar_datos.py --auto     # lo que ejecuta GitHub Actions
Importa también los CSV que subas a la raíz del repositorio con el nombre del juego al principio (por ejemplo «Primitiva - 2013 a 202X.csv»).
Solo consulta un juego si ya debería haber un sorteo nuevo, para gastar pocas peticiones de la API (LOTERIAS_API_KEY).
"""
import argparse, datetime as dt, json, os, re, urllib.request
from zoneinfo import ZoneInfo
CFG = {'primitiva': (6, 49), 'bonoloto': (6, 49), 'euromillones': (5, 50), 'eurodreams': (6, 40)}
PRIM = ('primitiva', 'bonoloto')  # mismo formato: [fecha, números, complementario, reintegro]
DRAW = {'primitiva': ([0, 3, 5], 22 * 60 + 15), 'bonoloto': ([0, 1, 2, 3, 4, 5, 6], 22 * 60 + 15),  # lunes=0 ... domingo=6
        'euromillones': ([1, 4], 22 * 60 + 30), 'eurodreams': ([0, 3], 21 * 60 + 15)}
DATE = re.compile(r'(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})')

def http(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; LuckyDevilBot/1.0)', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode('utf-8', 'ignore')

def valid(g, e):
    n, m = CFG[g]
    if not (isinstance(e, list) and len(e) >= 3 and re.match(r'\d{4}-\d{2}-\d{2}$', str(e[0]))): return False
    a = e[1]
    if not (isinstance(a, list) and len(a) == n and all(isinstance(x, int) and 1 <= x <= m for x in a) and len(set(a)) == n): return False
    if g == 'euromillones': return isinstance(e[2], list) and len(e[2]) == 2 and all(isinstance(x, int) and 1 <= x <= 12 for x in e[2]) and e[2][0] != e[2][1]
    if g == 'eurodreams': return isinstance(e[2], int) and 1 <= e[2] <= 5
    return True

def parse_csv(g, text):
    n, m = CFG[g]; out = {}
    for line in text.lstrip('\ufeff').splitlines():
        mt = DATE.search(line)
        if not mt: continue
        d = mt.group(1)
        if not re.match(r'\d{4}', d):
            a, b, y = re.split(r'[/-]', d); d = f'{y}-{int(b):02d}-{int(a):02d}'
        v = [int(x) for x in re.findall(r'\d+', line[mt.end():])]; nums, e = v[:n], v[n:]
        if g in PRIM: ent = [d, sorted(nums), e[0] if e and 1 <= e[0] <= 49 else None, e[1] if len(e) > 1 and 0 <= e[1] <= 9 else None]
        elif g == 'euromillones': ent = [d, sorted(nums), sorted(e[:2])]
        else: ent = [d, sorted(nums), e[0] if e else 0]
        if valid(g, ent): out[d] = ent
    return list(out.values())

# ---------- fuentes gratuitas (solo donde funcionan) ----------
def src_eurodreams():
    d = json.loads(http('https://juhuj.com/api/derniers-tirages.json'))['games']['eurodreams']
    return [[d['date'], sorted(int(x) for x in d['balls']), int(d['extras'][0])]]
def src_euromillones():
    return parse_csv('euromillones', http('https://raw.githubusercontent.com/daowa89/lottery-archive/main/eu/euromillions/results.csv'))
SRC = {'euromillones': src_euromillones, 'eurodreams': src_eurodreams}

# ---------- API (respaldo) con búsqueda flexible de campos ----------
def find(obj, rx, kind):
    """Busca en cualquier nivel una clave que case con rx. kind='num' devuelve un entero; kind='list' una lista de enteros."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if re.search(rx, str(k), re.I):
                if kind == 'num' and not isinstance(v, (dict, list)) and str(v).strip().isdigit(): return int(v)
                if kind == 'list' and isinstance(v, list) and v and all(str(x).strip().isdigit() for x in v): return [int(x) for x in v]
        for v in obj.values():
            r = find(v, rx, kind)
            if r is not None: return r
    elif isinstance(obj, list):
        for v in obj:
            r = find(v, rx, kind)
            if r is not None: return r

def api_latest(g, key):
    d = (json.loads(http(f'https://api.loteriasapi.com/api/v1/results/{g}/latest', {'X-API-Key': key})).get('data')) or {}
    if d.get('status') not in (None, 'COMPLETED'): print('  API: el último sorteo aún no está cerrado'); return []
    nums = sorted(int(x) for x in d.get('combination', [])); rd = d.get('resultData') or {}; date = str(d.get('drawDate', ''))[:10]
    if g in PRIM: ent = [date, nums, find(rd, 'compl', 'num'), find(rd, 'reint|refund', 'num')]
    elif g == 'euromillones': ent = [date, nums, sorted(find(d, 'estrella|star', 'list') or [])]
    else: ent = [date, nums, find(d, 'dream|sue', 'num') or 0]
    if not valid(g, ent): print('  API devolvió datos que no encajan:', json.dumps({k: v for k, v in d.items() if k not in ('prizes', 'prizeBreakdown')}, ensure_ascii=False)[:700])
    return [ent]

def csv_de(g):
    import glob
    return sorted(f for f in glob.glob('*.csv') + glob.glob('csv/*.csv') if os.path.basename(f).lower().startswith(g))

def expected(g, now):
    days, mins = DRAW[g]; d = now
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
        cur = {e[0]: e for e in feed.get(g, [])}
        for ruta in csv_de(g):  # cualquier CSV cuyo nombre empiece por el juego, p. ej. «Primitiva - 1985 a 2012.csv»
            try: print(f'{g}: CSV «{ruta}»: {add(cur, parse_csv(g, open(ruta, encoding="utf-8", errors="ignore").read()), g)} nuevos')
            except Exception as ex: print(f'{g}: CSV «{ruta}» falló ({ex})')
        feed[g] = sorted(cur.values(), key=lambda e: e[0])
        exp = expected(g, now); last = max(cur) if cur else ''
        if last >= exp: print(f'{g}: al día ({last}, {len(cur)} sorteos)'); continue
        print(f'{g}: falta el sorteo del {exp} (último guardado: {last or "ninguno"})')
        fuentes = ([('fuente gratuita', SRC[g])] if g in SRC else []) + ([('API', lambda g=g: api_latest(g, key))] if key else [])
        if not fuentes: print('  sin fuente disponible (falta la clave LOTERIAS_API_KEY)')
        for nombre, fn in fuentes:
            try:
                n = add(cur, fn(), g); print(f'  {nombre}: {n} nuevos')
                if cur and max(cur) >= exp: break
            except Exception as ex: print(f'  {nombre}: falló ({type(ex).__name__}: {ex})')
        feed[g] = sorted(cur.values(), key=lambda e: e[0])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--auto', action='store_true'); ap.add_argument('--salida', default='datos.json'); a = ap.parse_args()
    key = os.environ.get('LOTERIAS_API_KEY', '')
    old = json.load(open(a.salida, encoding='utf-8')) if os.path.exists(a.salida) else {'v': 1}
    feed = json.loads(json.dumps(old)); auto(feed, key)
    if any(feed.get(g) != old.get(g) for g in CFG): feed['actualizado'] = dt.date.today().isoformat()
    json.dump(feed, open(a.salida, 'w', encoding='utf-8'), separators=(',', ':'))
    print('Guardado', a.salida, '|', ', '.join(f'{g}={len(feed.get(g, []))}' for g in CFG))

if __name__ == '__main__': main()
