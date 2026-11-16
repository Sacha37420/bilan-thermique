"""Thème d'impression des exports (HTML, PDF, DOCX) : la seule place où le serveur pose des couleurs, et
seulement sur une **copie** de la figure, au moment d'exporter. Règle commune (I6.g) : `palette[i % n]` sur
`data[i].marker.color` et `data[i].line.color` quand ils sont absents ; échelle remplacée quand la palette n'est pas
catégorielle. Palette catégorielle lisible par les daltoniens (d'après Okabe et Ito), contrastée sur papier blanc.
"""

import copy

CATEGORIELLE = ['#0072B2', '#E69F00', '#009E73', '#D55E00', '#56B4E9', '#CC79A7', '#8C6D1F', '#4D4D4D']
SEQUENTIELLE = [[0.0, '#EEF3FB'], [0.25, '#C2D6EE'], [0.5, '#7FA7D9'], [0.75, '#3F6FB4'], [1.0, '#1C3266']]
DIVERGENTE = [[0.0, '#1B7837'], [0.5, '#F7F7F7'], [1.0, '#B2182B']]
HAUSSE, BAISSE, TOTAL = '#B2182B', '#1B7837', '#4D4D4D'
TEXTE, GRILLE, FOND = '#222222', '#D9D9D9', '#FFFFFF'
POLICE = 'Inter, "Segoe UI", Arial, sans-serif'


def appliquer(fig):
    """Copie de `fig` aux couleurs d'impression."""
    fig = copy.deepcopy(fig)
    lay = fig.setdefault('layout', {})
    palette = (lay.get('meta') or {}).get('palette', 'categorielle')
    echelle = DIVERGENTE if palette == 'divergente' else SEQUENTIELLE
    lay.setdefault('paper_bgcolor', FOND)
    lay.setdefault('plot_bgcolor', FOND)
    lay['font'] = {**(lay.get('font') or {}), 'family': POLICE, 'color': TEXTE, 'size': 12}
    lay['colorway'] = CATEGORIELLE
    for k, v in list(lay.items()):
        if (k.startswith('xaxis') or k.startswith('yaxis')) and isinstance(v, dict):
            v.setdefault('gridcolor', GRILLE)
            v.setdefault('linecolor', GRILLE)
            v.setdefault('zerolinecolor', GRILLE)
    if isinstance(lay.get('polar'), dict):
        lay['polar'].setdefault('bgcolor', FOND)
    if palette != 'categorielle' and isinstance(lay.get('coloraxis'), dict):
        lay['coloraxis']['colorscale'] = echelle
    for i, tr in enumerate(fig.get('data') or []):
        c = CATEGORIELLE[i % len(CATEGORIELLE)]
        typ = tr.get('type')
        if typ == 'pie':
            n = len(tr.get('labels') or [])
            tr.setdefault('marker', {}).setdefault('colors', [CATEGORIELLE[j % len(CATEGORIELLE)] for j in range(n)])
            tr['marker'].setdefault('line', {'color': FOND, 'width': 1})
            continue
        if typ == 'waterfall':
            tr.setdefault('increasing', {'marker': {'color': HAUSSE}})
            tr.setdefault('decreasing', {'marker': {'color': BAISSE}})
            tr.setdefault('totals', {'marker': {'color': TOTAL}})
            tr.setdefault('connector', {'line': {'color': GRILLE}})
            continue
        if typ == 'parcoords':
            tr.setdefault('line', {})
            if palette != 'categorielle' and 'coloraxis' not in tr['line']:
                tr['line']['colorscale'] = echelle
            continue
        if typ == 'heatmap':
            if palette != 'categorielle' and 'coloraxis' not in tr:
                tr['colorscale'] = echelle
            continue
        m = tr.setdefault('marker', {})
        if 'color' not in m:
            m['color'] = c
        if typ in ('scatter', 'scatterpolar', 'box', 'violin'):
            tr.setdefault('line', {}).setdefault('color', c)
        if typ == 'scatterpolar':
            tr.setdefault('fillcolor', c + '40')
    return fig
