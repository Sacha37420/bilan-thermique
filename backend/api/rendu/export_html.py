"""Export HTML **autonome** d'un document résolu : plotly.js (et three.js s'il y a une vue 3D) embarqués,
interactif hors ligne, sommaire cliquable, CSS d'impression. Le même HTML, sans bibliothèques (le service les
injecte), est celui que le service de rendu imprime en PDF.

Échappement : gabarit Django (échappement automatique) pour tout texte ; données des figures et des scènes dans
`json_script` (`<`, `>`, `&` encodés) ; seul le HTML du bloc « texte », assaini par `texte.vers_html`, est inséré
tel quel.
"""

import json
from datetime import datetime
from pathlib import Path

from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from . import figures, theme

VENDOR = Path(__file__).resolve().parents[2] / 'vendor'
PLOTLY = VENDOR / 'plotly-2.35.2.min.js'
THREE = VENDOR / 'three-0.180.0.global.js'
CLIENT = Path(__file__).resolve().parent / 'client.js'
LARGEURS_MM = {('A4', 'portrait'): 210, ('A4', 'paysage'): 297, ('A3', 'portrait'): 297, ('A3', 'paysage'): 420}
MARGES_MM = 14
_CACHE = {}


def _lire(p):
    if p not in _CACHE:
        _CACHE[p] = p.read_text(encoding='utf-8').replace('</script', '<\\/script')
    return _CACHE[p]


def _cellule(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return {'t': figures._fmt(float(v)), 'n': True}
    return {'t': '—' if v in (None, '') else str(v), 'n': False}


def vue(doc):
    """Modèle de vue du gabarit : numérotation des figures, cellules formatées, figures au thème d'impression."""
    sections, n_fig, n_json = [], 0, 0
    for s in doc['sections']:
        blocs = []
        for b in s['blocs']:
            b = dict(b)
            if b.get('indisponible'):
                blocs.append(b)
                continue
            t = b['type']
            if t in ('graphe', 'carte', 'vue3d'):
                n_fig += 1
                n_json += 1
                b['numero'] = n_fig
                b['ancre'] = f'fig{n_fig}'
                b['json_id'] = f'donnees-{n_json}'
            if t in ('graphe', 'carte'):
                b['figure'] = theme.appliquer(b['figure'])
                titre = ((b['figure'].get('layout') or {}).get('title') or {}).get('text')
                import html as _h
                b['legende'] = _h.unescape(titre) if titre else ('Carte' if t == 'carte' else 'Graphe')
            elif t == 'vue3d':
                b['donnees3d'] = {'scene': b['scene'], 'grandeur': b['grandeur'], 'camera': b['camera']}
            elif t == 'texte':
                b['html_sur'] = mark_safe(b['html'])      # assaini par texte.vers_html (échappé puis balisé)
            elif t == 'indicateurs':
                b['lignes_vue'] = [{'libelle': (l['libelle'] + (f" ({l['unite']})" if l['unite'] else '')),
                                    'cellules': [_val_ecart(v, e, l['unite']) for v, e in zip(l['valeurs'], l['ecarts'])]}
                                   for l in b['lignes']]
            elif t == 'tableau':
                b['lignes_vue'] = [[_cellule(v) for v in row] for row in b['lignes']]
                b['tronque'] = b['total_lignes'] > len(b['lignes'])
            blocs.append(b)
        sections.append({**s, 'blocs': blocs})
    return sections


def _val_ecart(v, e, unite):
    if v is None:
        return '—'
    s = figures._fmt(v)
    if e is not None:
        s += f" ({'+' if e > 0 else '−' if e < 0 else '±'}{figures._fmt(abs(e))})"
    return s


def a_des_vues3d(doc):
    return any(b.get('type') == 'vue3d' and not b.get('indisponible') for s in doc['sections'] for b in s['blocs'])


def rendre(doc, sous_titre='', embarquer=True):
    mp = doc['mise_en_page']
    largeur = LARGEURS_MM[(mp['format'], mp['orientation'])] - 2 * MARGES_MM
    libs = []
    if embarquer:
        libs.append(mark_safe(_lire(PLOTLY)))
        if a_des_vues3d(doc):
            libs.append(mark_safe(_lire(THREE)))
    script = _lire(CLIENT).replace('__ECHELLE__', json.dumps(theme.SEQUENTIELLE))
    degrade = ', '.join(f'{c} {int(p * 100)}%' for p, c in theme.SEQUENTIELLE)
    return render_to_string('rendu/document.html', {
        'doc': doc, 'sections': vue(doc), 'sous_titre': sous_titre, 'bibliotheques': libs,
        'script': mark_safe(script), 'degrade': degrade,
        'page': {'format': mp['format'], 'orientation': 'landscape' if mp['orientation'] == 'paysage' else 'portrait',
                 'largeur_mm': largeur, 'marges_mm': MARGES_MM},
        'date': datetime.now().strftime('%d/%m/%Y à %H:%M'),
    })
