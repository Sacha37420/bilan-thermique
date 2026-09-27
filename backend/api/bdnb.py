"""Vitrages déclarés dans les DPE, via la BDNB (Lot AL).

Source : Base de Données Nationale des Bâtiments (CSTB), table
`batiment_groupe_dpe_representatif_logement` — licence ouverte Etalab 2.0, API
PostgREST sans clé (api.bdnb.io ; 120 requêtes/min). Jointure depuis une
position par le Référentiel National des Bâtiments (rnb-api.beta.gouv.fr,
`closest`), qui donne l'identifiant RNB, lui-même présent dans la BDNB.
Vérifié le 2026-09-27 sur un collectif de Tours (26 logements, 2005) : 23 % de
baies, double vitrage PVC, Uw 2,43, g 0,40.

Limite à connaître : pour un immeuble, le DPE « représentatif » décrit UN
logement — ses surfaces vitrées par orientation ne sont pas celles du
bâtiment. On n'en garde alors que la PROPORTION de baies (appliquée à chaque
façade). Pour une maison, les surfaces par orientation sont celles de la
maison entière.
"""

import requests

RNB_CLOSEST_URL = 'https://rnb-api.beta.gouv.fr/api/alpha/buildings/closest/'
BDNB_URL = 'https://api.bdnb.io/v1/bdnb/donnees'
USER_AGENT = 'bilan-thermique-lab/1.0 (usage interne)'
DPE_FIELDS = ('batiment_groupe_id,identifiant_dpe,date_etablissement_dpe,type_batiment_dpe,'
              'surface_vitree_nord,surface_vitree_sud,surface_vitree_est,surface_vitree_ouest,'
              'pourcentage_surface_baie_vitree_exterieur,type_vitrage,type_materiaux_menuiserie,'
              'uw,facteur_solaire_baie_vitree,type_fermeture,surface_habitable_logement')


class BdnbError(ValueError):
    pass


def _get(url, params):
    try:
        resp = requests.get(url, params=params, headers={'User-Agent': USER_AGENT}, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise BdnbError(f"Service injoignable ({exc}).") from exc


def rnb_ids_at(points_latlon, radius_m=8):
    """Identifiants RNB des bâtiments CONTENANT chacun des points (distance 0)."""
    ids = []
    for lat, lon in points_latlon:
        data = _get(RNB_CLOSEST_URL, {'point': f'{lat:.7f},{lon:.7f}', 'radius': radius_m})
        for r in data.get('results') or []:
            if (r.get('distance') or 0) <= 0.5 and r.get('rnb_id') and r['rnb_id'] not in ids:
                ids.append(r['rnb_id'])
    return ids


def dpe_for_rnb(rnb_ids):
    """DPE représentatifs (un par groupe de bâtiments BDNB) et fiches des groupes."""
    if not rnb_ids:
        return [], []
    ids = ','.join(rnb_ids)
    cons = _get(f'{BDNB_URL}/batiment_construction',
                {'rnb_id': f'in.({ids})', 'select': 'batiment_groupe_id'})
    groups = sorted({c['batiment_groupe_id'] for c in cons if c.get('batiment_groupe_id')})
    if not groups:
        return [], []
    gl = ','.join(groups)
    dpes = _get(f'{BDNB_URL}/batiment_groupe_dpe_representatif_logement',
                {'batiment_groupe_id': f'in.({gl})', 'select': DPE_FIELDS})
    fiches = _get(f'{BDNB_URL}/batiment_groupe_complet',
                  {'batiment_groupe_id': f'in.({gl})',
                   'select': 'batiment_groupe_id,nb_log,annee_construction,nb_niveau'})
    return dpes, fiches


CARDINAL = {'N': 'surface_vitree_nord', 'E': 'surface_vitree_est',
            'S': 'surface_vitree_sud', 'O': 'surface_vitree_ouest'}


def cardinal_of(azimuth_deg):
    """Orientation (N/E/S/O) d'une façade d'azimut donné (0 = nord, sens horaire)."""
    return 'NESO'[int(((azimuth_deg % 360) + 45) // 90) % 4]


def glazing_ratios(dpes, fiches, facade_area_by_cardinal):
    """Proportion de baies par orientation déduite des DPE, ou None.

    Maison (un seul logement) : surfaces vitrées N/E/S/O du DPE rapportées aux
    surfaces de façade de même orientation de l'enveloppe. Immeuble : la
    proportion globale du logement représentatif, appliquée partout."""
    if not dpes:
        return None
    nb_log = sum((f.get('nb_log') or 0) for f in fiches) or None
    d = max(dpes, key=lambda x: x.get('date_etablissement_dpe') or '')
    kind = (d.get('type_batiment_dpe') or '').lower()
    info = {
        'source': 'BDNB — DPE ' + (d.get('identifiant_dpe') or ''),
        'dpe_date': (d.get('date_etablissement_dpe') or '')[:10],
        'type': kind or None, 'nb_log': nb_log,
        'annee': next((f.get('annee_construction') for f in fiches if f.get('annee_construction')), None),
        'vitrage': d.get('type_vitrage'), 'menuiserie': d.get('type_materiaux_menuiserie'),
        'uw': round(d['uw'], 2) if d.get('uw') else None,
        'g': d.get('facteur_solaire_baie_vitree'), 'fermeture': d.get('type_fermeture'),
        'n_dpe': len(dpes), 'license': 'Licence Ouverte Etalab 2.0 (CSTB, ADEME)',
    }
    house = kind == 'maison' and (nb_log or 1) <= 2 and len(dpes) == 1
    if house and any(d.get(k) is not None for k in CARDINAL.values()):
        ratios = {}
        for c, key in CARDINAL.items():
            area = facade_area_by_cardinal.get(c, 0.0)
            if area > 1.0:
                ratios[c] = round(min(0.6, (d.get(key) or 0.0) / area), 3)
        info.update(mode='orientation', ratios=ratios,
                    surfaces={c: d.get(k) for c, k in CARDINAL.items()})
        return info
    pcts = [x['pourcentage_surface_baie_vitree_exterieur'] for x in dpes
            if x.get('pourcentage_surface_baie_vitree_exterieur')]
    if not pcts:
        return None
    r = round(min(0.6, sum(pcts) / len(pcts)), 3)
    info.update(mode='global', ratios={c: r for c in 'NESO'})
    return info
