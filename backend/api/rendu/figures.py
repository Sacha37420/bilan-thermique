"""Figures Plotly construites par le serveur seul (I6.b, formes I6.g).

`{"data": [...], "layout": {..., "meta": {"palette", "resume"}}, "vide": bool}` — **aucune couleur** : le rendu
(F à l'écran, `theme.py` à l'export) pose `palette[i % n]` sur `data[i].marker.color` / `data[i].line.color` et
l'échelle de couleurs quand `palette` n'est pas `categorielle`.

Toute chaîne venue des données (nom de bâtiment, de bouquet, d'action) est **échappée** avant d'entrer dans la
figure : Plotly interprète un sous-ensemble de HTML dans ses textes (`<a href>`, `<b>`…).
"""

import html
import math
import statistics

from . import catalogue, spec as spec_mod
from .donnees import AUTO, filtrer, niveaux_de

MAX_FACETTES = 12
MAX_TRACES = 60
FORMES_BARRES = ('barres', 'barres_empilees', 'barres_groupees', 'courbes', 'aires')


def t(s):
    """Texte venu des données → texte inerte pour Plotly (entités HTML)."""
    return html.escape(str(s if s is not None else ''), quote=False)   # Plotly ne décode pas &quot; : seuls <, >, & comptent


def _fmt(v, unite=''):
    if v is None:
        return '—'
    a = abs(v)
    d = 0 if a >= 100 else 1 if a >= 10 else 2
    s = f'{v:,.{d}f}'.replace(',', ' ').replace('.', ',')
    if d and ',' in s:
        s = s.rstrip('0').rstrip(',')
    return f'{s} {unite}'.strip()


def agreger(vals, agg):
    vs = [v for v in vals if v is not None]
    if not vs:
        return None
    if agg == 'somme':
        return round(sum(vs), 4)
    if agg == 'mediane':
        return round(statistics.median(vs), 4)
    return round(sum(vs) / len(vs), 4)


def ordre(dim, valeurs, ctx=None):
    vals = list(dict.fromkeys(valeurs))
    if dim in catalogue.ORDRES:
        o = catalogue.ORDRES[dim]
        return sorted(vals, key=lambda v: (o.index(v) if v in o else len(o), str(v)))
    if dim == 'bouquet' and ctx is not None:
        o = ['Réel'] + [b.nom for b in ctx.bouquets]
        return sorted(vals, key=lambda v: (o.index(v) if v in o else len(o), str(v)))
    if dim == 'batiment':
        return vals
    return sorted(vals, key=str)


def _titre_mesure(mes, m):
    d = mes[m]
    return f"{d['libelle']} ({d['unite']})" if d['unite'] else d['libelle']


def vide(raison, titre=''):
    return {'data': [], 'layout': {'title': {'text': t(titre)} if titre else {}, 'separators': ', ',
                                   'meta': {'palette': 'categorielle', 'resume': raison}}, 'vide': True}


def _layout_base(g, mes, palette):
    lay = {'separators': ', ', 'hovermode': 'closest', 'margin': {'t': 60, 'r': 20, 'b': 60, 'l': 70},
           'legend': {'orientation': 'h', 'y': -0.2}, 'meta': {'palette': palette, 'resume': ''}}
    if g.get('titre'):
        lay['title'] = {'text': t(g['titre'])}
    return lay


def _facettes(rows, g, ctx):
    f = g.get('facettes')
    if not f:
        return [(None, rows)]
    vals = ordre(f, [r.get(f) for r in rows if r.get(f) is not None], ctx)[:MAX_FACETTES]
    return [(v, [r for r in rows if r.get(f) == v]) for v in vals]


def _decoupe(rows, g, ctx, series):
    """Dimensions qui séparent les traces : couleur, puis série si plusieurs et non déjà placée."""
    dims = []
    if g.get('couleur'):
        dims.append(g['couleur'])
    used = {g.get('x'), g.get('couleur'), g.get('facettes')}
    if len(series) > 1 and 'bouquet' not in used:
        dims.append('bouquet')
    if not dims:
        return [((), rows)]
    combos = {}
    for r in rows:
        combos.setdefault(tuple(r.get(d) for d in dims), []).append(r)
    keys = list(combos)
    for i in reversed(range(len(dims))):
        o = ordre(dims[i], [k[i] for k in keys], ctx)
        keys.sort(key=lambda k, i=i, o=o: o.index(k[i]) if k[i] in o else len(o))
    return [(k, combos[k]) for k in keys]


def _grille(n):
    cols = min(n, 3)
    return math.ceil(n / cols), cols


def _axe(i):
    return ('x', 'y') if i == 0 else (f'x{i + 1}', f'y{i + 1}')


def _poser_facettes(lay, facettes, g, ctx, xtitle, ytitle, log):
    n = len(facettes)
    if n > 1:
        rows, cols = _grille(n)
        lay['grid'] = {'rows': rows, 'columns': cols, 'pattern': 'independent', 'roworder': 'top to bottom'}
        lay['annotations'] = []
    for i, (fv, _r) in enumerate(facettes):
        xa, ya = _axe(i)
        xk = 'xaxis' if i == 0 else f'xaxis{i + 1}'
        yk = 'yaxis' if i == 0 else f'yaxis{i + 1}'
        lay[xk] = {'title': {'text': xtitle}, 'automargin': True}
        lay[yk] = {'title': {'text': ytitle}, 'automargin': True, 'type': 'log' if log else 'linear'}
        if n > 1:
            lay['annotations'].append({'text': t(fv), 'showarrow': False, 'xref': f'{xa} domain',
                                       'yref': f'{ya} domain', 'x': 0.5, 'y': 1.12, 'xanchor': 'center'})


def _nom(key, m, multi_m, mes):
    parts = [str(k) for k in key if k is not None]
    if multi_m:
        parts.append(mes[m]['libelle'])
    return t(' · '.join(parts)) if parts else t(mes[m]['libelle'])


def _resume(g, mes, ctx, data_points, n_bat, series, extra=''):
    y = g.get('y') or []
    quoi = ', '.join(_titre_mesure(mes, m) for m in y[:3]) or 'Répartition'
    if g.get('_x_numerique'):
        selon = _titre_mesure(mes, g['x'])
    else:
        selon = catalogue.DIM_LIBELLE.get(g.get('x'), '')
    serie = ('réel' if series == ['reel'] else
             (f"réel et {len(series) - 1} bouquet{'s' if len(series) > 2 else ''}" if 'reel' in series else
              f"{len(series)} bouquet{'s' if len(series) > 1 else ''}"))
    agg = {'somme': 'somme', 'moyenne': 'moyenne', 'mediane': 'médiane', 'aucune': 'valeurs individuelles'}[g['agregation']]
    s = f"{quoi} selon « {selon} » — {serie}, {n_bat} bâtiment{'s' if n_bat > 1 else ''}, {agg}."
    if data_points:
        hi = max(data_points, key=lambda p: p[0])
        s += f" Valeur la plus haute : {hi[1]}, {_fmt(hi[0], mes[y[0]]['unite'] if y else '')}."
    return (s + (' ' + extra if extra else '')).strip()


def _hover(unite):
    u = t(unite)
    return '%{x}<br>%{y:,.3~r} ' + u + '<extra>%{fullData.name}</extra>'


def construire(ctx, g):
    """Figure d'un GRAPHE déjà validé (`spec.valider_graphe`)."""
    mes = ctx.mesures()
    forme = g['forme']
    if forme == 'cascade':
        return _cascade(ctx, g, mes)
    besoins_faits = any(mes.get(k, {}).get('source') == 'fait' for k in [*g['y'], g.get('taille'), g['x']] if k)
    series = ctx.series(g['series'])
    if not series:
        return vide('Aucune donnée : aucun des bouquets demandés n’est visible ici.', g.get('titre'))
    rows = filtrer(ctx.lignes(g['_grain'], g['series'], niveaux_de(g), besoins_faits), g['filtre'])
    if not rows:
        return vide('Aucune donnée : aucun calcul terminé ne correspond à ce graphe (bâtiments, séries, filtres).',
                    g.get('titre'))
    n_bat = len({r['_b'] for r in rows})
    fn = {'camembert': _camembert, 'radar': _radar, 'carte_chaleur': _carte_chaleur, 'parallele': _parallele,
          'histogramme': _histogramme, 'boite': _boite, 'violon': _boite, 'nuage': _nuage, 'bulles': _nuage}.get(forme, _barres)
    fig, points, extra = fn(ctx, g, mes, rows, series)
    if not fig['data']:
        return vide('Aucune donnée : les calculs trouvés ne portent pas ces mesures.', g.get('titre'))
    fig['data'] = fig['data'][:MAX_TRACES]
    fig['layout']['meta']['resume'] = _resume(g, mes, ctx, points, n_bat, series,
                                              ' '.join(x for x in (extra, ctx.resume_niveaux()) if x))
    fig['vide'] = False
    return fig


# ── Familles de formes ───────────────────────────────────────────────────────

def _barres(ctx, g, mes, rows, series):
    forme = g['forme']
    lay = _layout_base(g, mes, 'categorielle')
    facettes = _facettes(rows, g, ctx)
    multi_m = len(g['y']) > 1
    data, points = [], []
    xdim = g['x']
    for fi, (fv, frows) in enumerate(facettes):
        xa, ya = _axe(fi)
        for key, srows in _decoupe(frows, g, ctx, series):
            for m in g['y']:
                xs_o = ordre(xdim, [r.get(xdim) for r in srows if r.get(m) is not None], ctx)
                xs, ys = [], []
                if g['agregation'] == 'aucune':
                    for xv in xs_o:
                        for r in srows:
                            if r.get(xdim) == xv and r.get(m) is not None:
                                xs.append(t(xv))
                                ys.append(r[m])
                else:
                    for xv in xs_o:
                        v = agreger([r.get(m) for r in srows if r.get(xdim) == xv], g['agregation'])
                        if v is not None:
                            xs.append(t(xv))
                            ys.append(v)
                if not ys:
                    continue
                name = _nom(key, m, multi_m, mes)
                tr = {'x': xs, 'y': ys, 'name': name, 'legendgroup': name, 'showlegend': fi == 0,
                      'xaxis': xa, 'yaxis': ya, 'hovertemplate': _hover(mes[m]['unite'])}
                if forme.startswith('barres'):
                    tr['type'] = 'bar'
                elif forme == 'courbes':
                    tr.update(type='scatter', mode='lines+markers')
                else:
                    tr.update(type='scatter', mode='lines', stackgroup=f'pile{fi}')
                data.append(tr)
                points += [(v, f'{x} ({name})' if (key or multi_m) else x) for x, v in zip(xs, ys)]
    if forme.startswith('barres'):
        lay['barmode'] = 'stack' if forme == 'barres_empilees' else 'group'
    ytitle = _titre_mesure(mes, g['y'][0]) if not multi_m else ', '.join(mes[m]['libelle'] for m in g['y'])
    _poser_facettes(lay, facettes, g, ctx, catalogue.DIM_LIBELLE[xdim], t(ytitle), g['axe_y_log'])
    return {'data': data, 'layout': lay}, [(v, html.unescape(lbl)) for v, lbl in points], ''


def _nuage(ctx, g, mes, rows, series):
    lay = _layout_base(g, mes, 'categorielle')
    facettes = _facettes(rows, g, ctx)
    data, points = [], []
    m = g['y'][0]
    xk = g['x']
    taille = g.get('taille')
    tmax = max([r.get(taille) or 0 for r in rows] or [0]) if taille else 0
    for fi, (fv, frows) in enumerate(facettes):
        xa, ya = _axe(fi)
        for key, srows in _decoupe(frows, g, ctx, series):
            pts = [r for r in srows if r.get(m) is not None and r.get(xk) is not None
                   and (not taille or r.get(taille) is not None)]
            if not g['_x_numerique'] and g['agregation'] != 'aucune':
                xs_o = ordre(xk, [r.get(xk) for r in pts], ctx)
                pts = [{xk: xv, m: agreger([r.get(m) for r in pts if r.get(xk) == xv], g['agregation']),
                        'batiment': '', 'bouquet': '',
                        **({taille: agreger([r.get(taille) for r in pts if r.get(xk) == xv], g['agregation'])}
                           if taille else {})} for xv in xs_o]
            if not pts:
                continue
            name = _nom(key, m, False, mes)
            ux = mes[xk]['unite'] if g['_x_numerique'] else ''
            tr = {'type': 'scatter', 'mode': 'markers', 'name': name, 'legendgroup': name, 'showlegend': fi == 0,
                  'xaxis': xa, 'yaxis': ya,
                  'x': [r[xk] if g['_x_numerique'] else t(r[xk]) for r in pts], 'y': [r[m] for r in pts],
                  'text': [t(f"{r.get('batiment', '')} — {r.get('bouquet', '')}".strip(' —')) for r in pts],
                  'hovertemplate': '%{text}<br>%{x:,.3~r} ' + t(ux) + ' · %{y:,.3~r} ' + t(mes[m]['unite'])
                                   + '<extra>%{fullData.name}</extra>' if g['_x_numerique'] else
                                   '%{text}<br>%{x}<br>%{y:,.3~r} ' + t(mes[m]['unite']) + '<extra>%{fullData.name}</extra>'}
            if taille:
                tr['marker'] = {'size': [r[taille] for r in pts], 'sizemode': 'area',
                                'sizeref': (2.0 * tmax / (40.0 ** 2)) if tmax else 1, 'sizemin': 4}
            data.append(tr)
            points += [(r[m], html.unescape(tr['text'][i]) or str(r[xk])) for i, r in enumerate(pts)]
    xt = _titre_mesure(mes, xk) if g['_x_numerique'] else catalogue.DIM_LIBELLE[xk]
    _poser_facettes(lay, facettes, g, ctx, t(xt), t(_titre_mesure(mes, m)), g['axe_y_log'])
    extra = ''
    if g['_x_numerique'] and len(points) >= 3:
        xs = [x for tr in data for x in tr['x']]
        ys = [y for tr in data for y in tr['y']]
        r = _correlation(xs, ys)
        if r is not None:
            extra = f'Corrélation linéaire : {_fmt(r)}.'
    return {'data': data, 'layout': lay}, points, extra


def _correlation(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy), 2)


def _boite(ctx, g, mes, rows, series):
    lay = _layout_base(g, mes, 'categorielle')
    facettes = _facettes(rows, g, ctx)
    data, points = [], []
    m = g['y'][0]
    xdim = g['x']
    typ = 'box' if g['forme'] == 'boite' else 'violin'
    for fi, (fv, frows) in enumerate(facettes):
        xa, ya = _axe(fi)
        for key, srows in _decoupe(frows, g, ctx, series):
            xs_o = ordre(xdim, [r.get(xdim) for r in srows if r.get(m) is not None], ctx)
            pts = [r for xv in xs_o for r in srows if r.get(xdim) == xv and r.get(m) is not None]
            if not pts:
                continue
            name = _nom(key, m, False, mes)
            tr = {'type': typ, 'name': name, 'legendgroup': name, 'showlegend': fi == 0, 'xaxis': xa, 'yaxis': ya,
                  'x': [t(r[xdim]) for r in pts], 'y': [r[m] for r in pts]}
            if typ == 'box':
                tr['boxpoints'] = 'outliers'
            else:
                tr.update(box={'visible': True}, meanline={'visible': True}, points=False)
            data.append(tr)
            points += [(r[m], f"{r.get('batiment', '')}") for r in pts]
    lay['boxmode' if typ == 'box' else 'violinmode'] = 'group'
    _poser_facettes(lay, facettes, g, ctx, catalogue.DIM_LIBELLE[xdim], t(_titre_mesure(mes, m)), g['axe_y_log'])
    return {'data': data, 'layout': lay}, points, ''


def _histogramme(ctx, g, mes, rows, series):
    lay = _layout_base(g, mes, 'categorielle')
    facettes = _facettes(rows, g, ctx)
    xk = g['x']
    data = []
    n = 0
    for fi, (fv, frows) in enumerate(facettes):
        xa, ya = _axe(fi)
        for key, srows in _decoupe(frows, g, ctx, series):
            xs = [r[xk] for r in srows if r.get(xk) is not None]
            if not xs:
                continue
            n += len(xs)
            name = t(' · '.join(str(k) for k in key)) if key else t(mes[xk]['libelle'])
            data.append({'type': 'histogram', 'x': xs, 'name': name, 'legendgroup': name, 'showlegend': fi == 0,
                         'xaxis': xa, 'yaxis': ya, 'opacity': 0.75 if len(series) > 1 or g.get('couleur') else 1,
                         'hovertemplate': '%{x}<br>%{y} bâtiment(s)<extra>%{fullData.name}</extra>'})
    lay['barmode'] = 'overlay'
    _poser_facettes(lay, facettes, g, ctx, t(_titre_mesure(mes, xk)), 'Nombre', g['axe_y_log'])
    vals = sorted(x for tr in data for x in tr['x'])
    extra = f"Médiane : {_fmt(statistics.median(vals), mes[xk]['unite'])}." if vals else ''
    return {'data': data, 'layout': lay}, [], extra


def _camembert(ctx, g, mes, rows, series):
    lay = _layout_base(g, mes, 'categorielle')
    m = g['y'][0]
    agg = 'somme' if g['agregation'] == 'aucune' else g['agregation']
    facettes = _facettes(rows, g, ctx)
    if g.get('facettes') is None and len(series) > 1 and g['x'] != 'bouquet':
        vals = ordre('bouquet', [r['bouquet'] for r in rows], ctx)
        facettes = [(v, [r for r in rows if r['bouquet'] == v]) for v in vals][:MAX_FACETTES]
    n = len(facettes)
    nr, nc = _grille(n)
    if n > 1:
        lay['grid'] = {'rows': nr, 'columns': nc}
    data, points = [], []
    for i, (fv, frows) in enumerate(facettes):
        labels = ordre(g['x'], [r.get(g['x']) for r in frows if r.get(m) is not None], ctx)
        values = [agreger([r.get(m) for r in frows if r.get(g['x']) == lv], agg) for lv in labels]
        pairs = [(lv, v) for lv, v in zip(labels, values) if v is not None and v > 0]
        if not pairs:
            continue
        data.append({'type': 'pie', 'labels': [t(lv) for lv, _ in pairs], 'values': [v for _, v in pairs],
                     'name': t(fv) if fv is not None else t(mes[m]['libelle']), 'sort': False,
                     **({'domain': {'row': i // nc, 'column': i % nc}} if n > 1 else {}),
                     'textinfo': 'percent', 'hole': 0.35,
                     'title': {'text': t(fv)} if fv is not None else {},
                     'hovertemplate': '%{label}<br>%{value:,.3~r} ' + t(mes[m]['unite']) + ' (%{percent})<extra></extra>'})
        points += [(v, str(lv)) for lv, v in pairs]
    return {'data': data, 'layout': lay}, points, ''


def _radar(ctx, g, mes, rows, series):
    lay = _layout_base(g, mes, 'categorielle')
    agg = 'moyenne' if g['agregation'] == 'aucune' else g['agregation']
    cats = ordre(g['x'], [r.get(g['x']) for r in rows], ctx)
    vals = {c: {m: agreger([r.get(m) for r in rows if r.get(g['x']) == c], agg) for m in g['y']} for c in cats}
    maxi = {m: max([abs(vals[c][m]) for c in cats if vals[c][m] is not None] or [0]) for m in g['y']}
    theta = [t(mes[m]['libelle']) for m in g['y']]
    data = []
    for c in cats:
        r = [round(vals[c][m] / maxi[m], 4) if vals[c][m] is not None and maxi[m] else None for m in g['y']]
        if all(v is None for v in r):
            continue
        data.append({'type': 'scatterpolar', 'r': r + r[:1], 'theta': theta + theta[:1], 'fill': 'toself',
                     'name': t(c), 'opacity': 0.8,
                     'customdata': [_fmt(vals[c][m], mes[m]['unite']) for m in g['y']] + [_fmt(vals[c][g['y'][0]], mes[g['y'][0]]['unite'])],
                     'hovertemplate': '%{theta} : %{customdata}<extra>%{fullData.name}</extra>'})
    lay['polar'] = {'radialaxis': {'range': [0, 1.05], 'tickformat': '.0%', 'title': {'text': 'Part du maximum'}}}
    return {'data': data, 'layout': lay}, [], 'Chaque axe est rapporté à sa plus grande valeur (100 %).'


def _carte_chaleur(ctx, g, mes, rows, series):
    lay = _layout_base(g, mes, 'sequentielle')
    lay.pop('legend')   # pas de légende pour cette trace (Plotly.validate échoue sur un conteneur non utilisé)
    m = g['y'][0]
    agg = 'moyenne' if g['agregation'] == 'aucune' else g['agregation']
    facettes = _facettes(rows, g, ctx)
    data, points = [], []
    for fi, (fv, frows) in enumerate(facettes):
        xa, ya = _axe(fi)
        xs = ordre(g['x'], [r.get(g['x']) for r in frows], ctx)
        ys = ordre(g['couleur'], [r.get(g['couleur']) for r in frows], ctx)
        z = [[agreger([r.get(m) for r in frows if r.get(g['x']) == xv and r.get(g['couleur']) == yv], agg) for xv in xs]
             for yv in ys]
        if all(v is None for row in z for v in row):
            continue
        data.append({'type': 'heatmap', 'x': [t(v) for v in xs], 'y': [t(v) for v in ys], 'z': z,
                     'coloraxis': 'coloraxis', 'xaxis': xa, 'yaxis': ya, 'name': t(fv) if fv else t(mes[m]['libelle']),
                     'hovertemplate': '%{y} · %{x}<br>%{z:,.3~r} ' + t(mes[m]['unite']) + '<extra></extra>'})
        points += [(v, f'{yv}, {xv}') for yv, row in zip(ys, z) for xv, v in zip(xs, row) if v is not None]
    lay['coloraxis'] = {'colorbar': {'title': {'text': t(mes[m]['unite'])}}}
    _poser_facettes(lay, facettes, g, ctx, catalogue.DIM_LIBELLE[g['x']], catalogue.DIM_LIBELLE[g['couleur']], False)
    return {'data': data, 'layout': lay}, points, ''


def _parallele(ctx, g, mes, rows, series):
    lay = _layout_base(g, mes, 'sequentielle')
    lay.pop('legend')   # pas de légende pour cette trace (Plotly.validate échoue sur un conteneur non utilisé)
    pts = [r for r in rows if all(r.get(m) is not None for m in g['y'])]
    if not pts:
        return {'data': [], 'layout': lay}, [], ''
    dims = [{'label': t(_titre_mesure(mes, m)), 'values': [r[m] for r in pts]} for m in g['y']]
    data = [{'type': 'parcoords', 'dimensions': dims,
             'line': {'color': [r[g['y'][0]] for r in pts], 'coloraxis': 'coloraxis'}}]
    lay['coloraxis'] = {'colorbar': {'title': {'text': t(mes[g['y'][0]]['unite'])}}}
    return {'data': data, 'layout': lay}, [], f'{len(pts)} lignes (bâtiment × série) ; la couleur suit la première mesure.'


def _cascade(ctx, g, mes, _rows=None, _series=None):
    """Du réel au potentiel d'un bouquet, poste par poste du bilan N1 : écart des déperditions (+), écart des
    apports (− : plus d'apports, moins de chauffage), puis le reste (utilisation des apports, inertie, pilotage)
    qui ferme exactement l'écart des besoins."""
    lay = _layout_base(g, mes, 'divergente')
    lay.pop('legend')   # pas de légende pour cette trace (Plotly.validate échoue sur un conteneur non utilisé)
    bq = [s for s in ctx.series(g['series']) if s != 'reel']
    if not bq:
        return vide('Aucune donnée : la cascade compare le réel à un bouquet visible ici.', g.get('titre'))
    s = bq[0]
    m = g['y'][0]
    par_m2 = m.endswith('_m2')
    deltas = {p[0]: [] for p in catalogue.POSTES}
    reels, pots, n = [], [], 0
    impose = [n for n in ('N0', 'N1', 'N2') if n in set(g['filtre'].get('niveau') or [])][:1]
    rows = filtrer([{**ctx.commun(b)['raw'], '_b': b.pk, '_obj': b} for b in ctx.batiments],
                   {k: v for k, v in g['filtre'].items() if k not in ('mois', 'famille_action', 'application', 'niveau')})
    choix = {}
    for r in rows:
        b = r['_obj']
        niv = impose[0] if impose else _niveau_commun(ctx, b, s)
        r0, r1 = ctx.run(b, 'reel', niv), ctx.run(b, s, niv)
        if r0 is None or r1 is None:
            continue
        b0, b1 = (r0.results or {}).get('balance') or {}, (r1.results or {}).get('balance') or {}
        h0, h1 = r0.indicators.get('heating_kwh'), r1.indicators.get('heating_kwh')
        surf = (r0.indicators or {}).get('surface_m2') or 0
        if h0 is None or h1 is None or not b0 or not b1 or (par_m2 and not surf):
            continue
        k = 1.0 / surf if par_m2 else 1.0
        n += 1
        choix[niv] = choix.get(niv, 0) + 1
        reels.append(h0 * k)
        pots.append(h1 * k)
        for cle, _lib, sens in catalogue.POSTES:
            d = (b1.get(cle, 0.0) - b0.get(cle, 0.0)) * k
            deltas[cle].append(d if sens == 'perte' else -d)
    if not n:
        return vide('Aucune donnée : il faut, pour un même bâtiment, le calcul réel et celui du bouquet au même niveau.',
                    g.get('titre'))
    agg = 'somme' if g['agregation'] == 'somme' and not par_m2 else 'moyenne'
    a = (lambda v: sum(v)) if agg == 'somme' else (lambda v: sum(v) / len(v))
    v_reel, v_pot = a(reels), a(pots)
    steps = [(cle, lib, a(deltas[cle])) for cle, lib, _s in catalogue.POSTES]
    reste = v_pot - v_reel - sum(d for _c, _l, d in steps)
    nom = ctx.libelle_serie(s)
    x = ['Réel'] + [lib for _c, lib, _d in steps] + ['Utilisation des apports, inertie, pilotage', nom]
    y = [v_reel] + [d for _c, _l, d in steps] + [reste, v_pot]
    data = [{'type': 'waterfall', 'x': [t(v) for v in x], 'y': [round(v, 2) for v in y],
             'measure': ['absolute'] + ['relative'] * (len(steps) + 1) + ['total'], 'name': t(nom),
             'hovertemplate': '%{x}<br>%{y:,.3~r} ' + t(mes[m]['unite']) + '<extra></extra>'}]
    lay['xaxis'] = {'title': {'text': 'Poste du bilan'}, 'automargin': True}
    lay['yaxis'] = {'title': {'text': t(_titre_mesure(mes, m))}, 'automargin': True}
    lay['showlegend'] = False
    gain = v_reel - v_pot
    lay['meta']['resume'] = (f"{_titre_mesure(mes, m)} : du réel ({_fmt(v_reel, mes[m]['unite'])}) au bouquet « {nom} » "
                             f"({_fmt(v_pot, mes[m]['unite'])}), soit {_fmt(gain, mes[m]['unite'])} de moins — "
                             f"{n} bâtiment{'s' if n > 1 else ''}, {'somme' if agg == 'somme' else 'moyenne'}."
                             + (' Niveau de calcul : ' + ', '.join(f"{lv} pour {c} bâtiment{'s' if c > 1 else ''}"
                                                                   for lv, c in sorted(choix.items(), reverse=True))
                                + (' (imposé par le filtre).' if impose else
                                   ' (le plus fin où le réel et le bouquet sont tous deux à jour, sinon N1).')))
    return {'data': data, 'layout': lay, 'vide': False}


def _niveau_commun(ctx, b, s):
    """Cascade : le niveau le plus fin où le réel et le bouquet sont calculés et à jour tous les deux, sinon N1."""
    for niv in ('N2', 'N1', 'N0'):
        if (ctx.run(b, 'reel', niv) is not None and ctx.run(b, s, niv) is not None
                and ctx.a_jour(b, 'reel', niv) and ctx.a_jour(b, s, niv)):
            return niv
    return 'N1'


def carte(ctx, indicateur, serie):
    """Bloc « carte » : bâtiments placés en longitude / latitude, **sans fond de carte** (aucune tuile, aucune
    topologie téléchargée : le service de rendu n'a pas de réseau, l'export HTML marche hors ligne)."""
    mes = ctx.mesures()
    if indicateur not in mes or mes[indicateur]['grain'] != 'batiment':
        return vide('Indicateur de carte inconnu.')
    if serie != 'reel' and serie not in ctx._bq:
        return vide('Aucune donnée : ce bouquet n’est pas visible ici.')
    rows = [r for r in ctx.lignes('batiment', [serie] if serie != 'reel' else 'reel', [AUTO],
                                  mes[indicateur]['source'] == 'fait')
            if r.get('lat') is not None and r.get('lon') is not None]
    sans = [r for r in rows if r.get(indicateur) is None]
    rows = [r for r in rows if r.get(indicateur) is not None]
    if not rows:
        return vide('Aucune donnée : aucun bâtiment situé n’a cette valeur.')
    lat0 = sum(r['lat'] for r in rows) / len(rows)
    u = mes[indicateur]['unite']
    data = [{'type': 'scatter', 'mode': 'markers', 'x': [r['lon'] for r in rows], 'y': [r['lat'] for r in rows],
             'text': [t(r['batiment']) for r in rows], 'name': t(mes[indicateur]['libelle']),
             'marker': {'size': 12, 'color': [r[indicateur] for r in rows], 'coloraxis': 'coloraxis'},
             'customdata': [r[indicateur] for r in rows],
             'hovertemplate': '%{text}<br>%{customdata:,.3~r} ' + t(u) + '<extra></extra>'}]
    lay = {'separators': ', ', 'hovermode': 'closest', 'margin': {'t': 40, 'r': 20, 'b': 50, 'l': 60},
           'xaxis': {'title': {'text': 'Longitude (°)'}, 'zeroline': False},
           'yaxis': {'title': {'text': 'Latitude (°)'}, 'zeroline': False, 'scaleanchor': 'x',
                     'scaleratio': round(1.0 / max(math.cos(math.radians(lat0)), 0.1), 4)},
           'coloraxis': {'colorbar': {'title': {'text': t(u)}}}, 'showlegend': False,
           'meta': {'palette': 'sequentielle', 'resume': ''}}
    hi = max(rows, key=lambda r: r[indicateur])
    lay['meta']['resume'] = (f"{_titre_mesure(mes, indicateur)} ({ctx.libelle_serie(serie)}) de {len(rows)} bâtiment"
                             f"{'s' if len(rows) > 1 else ''} situé{'s' if len(rows) > 1 else ''} ; le plus élevé : "
                             f"{hi['batiment']} ({_fmt(hi[indicateur], u)})."
                             + (f" {len(sans)} sans valeur." if sans else '')
                             + (' ' + ctx.resume_niveaux() if ctx.resume_niveaux() else ''))
    return {'data': data, 'layout': lay, 'vide': False}


def figure_de_spec(ctx, graphe):
    """Validation + construction (entrée HTTP)."""
    g = spec_mod.valider_graphe(graphe, ctx.mesures())
    return construire(ctx, g)
