"""Parc de bâtiments : import, traitement automatique, tableau, export (Lot 11, refonte — § 4).

- **Import** CSV (séparateur `;` ou `,`, en-têtes en français ou en anglais) : une ligne = un
  bâtiment (adresse, ou identifiant RNB, ou lat/lon), plus des colonnes facultatives qui entrent
  dans la fiche comme valeurs **déclarées** par la personne qui importe.
- **Traitement** : file Celery `parc`, un bâtiment à la fois (D13) : chaîne A1→A6 puis N0 et N1
  avec fourchette. Une erreur n'arrête pas le parc. Reprise : un bâtiment resté « en cours » plus
  de 30 min est repris.
- **Tableau** : indicateurs du dernier N1 à jour, confiance, complétude, priorité.
"""

import csv
import io
import unicodedata
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import facts
from .environment_service import unique_name
from .models import Building, CalcRun, Job

MAX_ROWS = 2000
MAX_BYTES = 2 * 1024 * 1024
STALE = timedelta(minutes=30)

COLUMNS = {
    'name': ('nom', 'name', 'libelle', 'batiment'),
    'address': ('adresse', 'address', 'adresse_postale'),
    'rnb_id': ('rnb', 'rnb_id', 'identifiant_rnb'),
    'lat': ('lat', 'latitude'),
    'lon': ('lon', 'lng', 'long', 'longitude'),
    'usage': ('usage', 'type'),
    'surface': ('surface', 'surface_m2', 'shab', 'surface_utile'),
    'annee': ('annee', 'annee_construction', 'year'),
    'energie': ('energie', 'energie_chauffage', 'chauffage'),
    'conso_kwh': ('conso', 'conso_kwh', 'consommation', 'consommation_annuelle_kwh'),
}
USAGE_WORDS = {'maison': 'maison', 'individuel': 'maison', 'collectif': 'logement_collectif', 'logement': 'logement_collectif',
               'bureau': 'bureau', 'tertiaire': 'bureau', 'ecole': 'enseignement', 'enseignement': 'enseignement',
               'college': 'enseignement', 'lycee': 'enseignement', 'commerce': 'commerce', 'sante': 'sante'}
ENERGY_WORDS = {'gaz': 'chaudiere_gaz', 'fioul': 'chaudiere_fioul', 'electri': 'joule', 'pac': 'pac_air_eau',
                'pompe': 'pac_air_eau', 'reseau': 'reseau_chaleur', 'bois': 'bois'}


class ImportError_(ValueError):
    pass


def _norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().strip().lower()
    return s.replace(' ', '_').replace('-', '_')


def _num(s):
    try:
        return float(str(s).replace(',', '.').replace(' ', ''))
    except (TypeError, ValueError):
        return None


def parse_csv(text):
    """[{name, address, rnb_id, lat, lon, usage, surface, annee, energie, conso_kwh}], erreurs par ligne."""
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ImportError_('Fichier trop volumineux (2 Mo au plus).')
    text = text.lstrip('﻿')
    first = text.split('\n', 1)[0]
    delim = ';' if first.count(';') >= first.count(',') else ','
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = [_norm(h) for h in next(reader)]
    except StopIteration:
        raise ImportError_('Fichier vide.') from None
    idx = {}
    for field, names in COLUMNS.items():
        for i, h in enumerate(header):
            if h in names and field not in idx:
                idx[field] = i
    if not ({'address', 'rnb_id'} & idx.keys() or {'lat', 'lon'} <= idx.keys()):
        raise ImportError_('Colonne « adresse », « rnb » ou « lat » + « lon » attendue.')
    rows, errors = [], []
    for n, raw in enumerate(reader, start=2):
        if not any(c.strip() for c in raw):
            continue
        if len(rows) >= MAX_ROWS:
            errors.append({'line': n, 'error': f'Au-delà de {MAX_ROWS} lignes : ignorée.'})
            continue
        get = lambda f: raw[idx[f]].strip() if f in idx and idx[f] < len(raw) else ''
        row = {f: get(f) for f in COLUMNS}
        row['lat'], row['lon'] = _num(row['lat']), _num(row['lon'])
        rnb = row['rnb_id'].upper().replace('-', '')
        row['rnb_id'] = rnb if len(rnb) == 12 and rnb.isalnum() else ''
        if not (row['address'] or row['rnb_id'] or (row['lat'] is not None and row['lon'] is not None)):
            errors.append({'line': n, 'error': 'Ni adresse, ni RNB, ni position.'})
            continue
        if row['lat'] is not None and not (41 <= row['lat'] <= 51.5 and -5.5 <= (row['lon'] or 0) <= 10):
            errors.append({'line': n, 'error': 'Position hors de France métropolitaine.'})
            continue
        row['line'] = n
        rows.append(row)
    return rows, errors


def declared_facts(row):
    out = []
    u = _norm(row.get('usage'))
    for w, key in USAGE_WORDS.items():
        if w in u:
            out.append(('identite.usage', key))
            break
    s = _num(row.get('surface'))
    if s and 5 <= s <= 200000:
        out.append(('identite.surface_m2', s))
    y = _num(row.get('annee'))
    if y and 1500 <= y <= 2030:
        out.append(('identite.annee_construction', int(y)))
    e = _norm(row.get('energie'))
    for w, key in ENERGY_WORDS.items():
        if w in e:
            out.append(('systeme.generateur', key))
            break
    return out


@transaction.atomic
def import_rows(portfolio, rows, author_sub, author_name):
    created = []
    for row in rows:
        name = row['name'] or row['address'] or row['rnb_id'] or f"{row['lat']:.5f}, {row['lon']:.5f}"
        b = Building.objects.create(
            portfolio=portfolio, name=unique_name(Building, name[:140]), address=row['address'][:300],
            rnb_id=row['rnb_id'], georef_lat=row['lat'], georef_lon=row['lon'], pipeline_status='en_attente',
            pipeline={'import': {'line': row['line'], 'conso_kwh': _num(row.get('conso_kwh'))}})
        for key, value in declared_facts(row):
            try:
                facts.record(b, key, value, 'declare', source=f'import du parc (ligne {row["line"]})',
                             author_sub=author_sub, author_name=author_name, refresh=False)
            except facts.FactError:
                pass
        created.append(b)
    return created


def counts(portfolio):
    qs = Building.objects.filter(portfolio=portfolio)
    out = {'total': qs.count()}
    for st in ('en_attente', 'en_cours', 'complet', 'incomplet', ''):
        out[st or 'jamais'] = qs.filter(pipeline_status=st).count()
    return out


def next_building(portfolio_id):
    """Prochain bâtiment à traiter (verrouillé), ou None."""
    from django.db.models import Q
    limit = timezone.now() - STALE
    with transaction.atomic():
        b = (Building.objects.select_for_update(skip_locked=True)
             .filter(portfolio_id=portfolio_id)
             .filter(Q(pipeline_status='en_attente') | Q(pipeline_status='en_cours', updated_at__lt=limit))
             .order_by('pk').first())
        if b is None:
            return None
        Building.objects.filter(pk=b.pk).update(pipeline_status='en_cours', updated_at=timezone.now())
    return Building.objects.get(pk=b.pk)


def process_one(building, owner_sub=''):
    """Chaîne automatique + N0 + N1 (fourchette) d'un bâtiment de parc. Ne lève jamais."""
    from . import calc_service, net, pipeline
    try:
        pipeline.initialize(building)
    except Exception as exc:  # noqa: BLE001
        building.pipeline_status = 'incomplet'
        building.pipeline = {**(building.pipeline or {}), 'error': net.reason(exc)}
        building.save(update_fields=['pipeline_status', 'pipeline'])
        return
    building.refresh_from_db()
    final_status = building.pipeline_status
    # « en cours » jusqu'à la fin des calculs : le tableau ne montre pas un bâtiment fini sans résultat.
    Building.objects.filter(pk=building.pk).update(pipeline_status='en_cours')
    # Réglages du parc (orchestration) : environnement observé (A6, avant N0/N1 qui en tirent
    # l'horizon), météo par défaut (A7) et calculs automatiques (A8).
    a6 = pipeline.apply_environment(building)
    a7 = pipeline.apply_default_weather(building, owner_sub)
    levels = pipeline.auto_levels(building)
    try:
        for lvl in levels:
            calc_service.run_level(building, lvl, owner_sub=owner_sub)
        ok = True
        err = None
    except calc_service.CalcError as exc:
        ok, err = False, str(exc)[:300]
    pipe = dict(building.pipeline or {})
    if a6 is not None:
        pipe.setdefault('steps', {})['A6'] = a6
    if a7 is not None:
        pipe.setdefault('steps', {})['A7'] = a7
    pipe.setdefault('steps', {})['A8'] = {'ok': ok, **({'error': err} if err else {}),
                                          **({} if levels else {'skipped': True, 'message': pipeline.SKIPPED})}
    building.pipeline = pipe
    building.pipeline_status = final_status if ok else 'incomplet'
    building.save(update_fields=['pipeline', 'pipeline_status'])


LEVEL_LABELS = {'N0': 'Mensuel', 'N1': 'Horaire simplifié', 'N2': 'Détaillé par paroi'}
DEPTH_LABELS = {'automatique': 'Automatique', 'complete': 'Complété', 'documente': 'Documenté', 'mesure': 'Mesuré',
                'expert': 'Expert'}
CALAGE_SOURCE = 'calage sur factures'


def _plural(n, one, many):
    return f'{n} {one if n == 1 else many}'


def approfondissement(building, eff=None):
    """Catégorie d'approfondissement de la fiche (D38 : une catégorie, pas un score) — la plus haute
    atteinte : expert > mesure > documente > complete > automatique."""
    from .engines import n2_inputs
    eff = facts.effective(building) if eff is None else eff
    methods = [f.method for f in eff.values()]
    n_decl, n_doc, n_mes = methods.count('declare'), methods.count('document'), methods.count('mesure')
    calage = any((f.source or '').startswith(CALAGE_SOURCE) for f in eff.values())
    expert = n2_inputs._expert_envelope(building)
    niveau = ('expert' if expert else 'mesure' if (n_mes or calage) else 'documente' if n_doc
              else 'complete' if n_decl else 'automatique')
    parts = []
    if n_decl:
        parts.append(_plural(n_decl, 'valeur déclarée', 'valeurs déclarées'))
    if n_doc:
        parts.append(_plural(n_doc, 'valeur tirée d’un document', 'valeurs tirées d’un document'))
    if n_mes:
        parts.append(_plural(n_mes, 'mesure', 'mesures'))
    if calage:
        parts.append('calage sur factures appliqué')
    if expert:
        parts.append('enveloppe experte assignée')
    return {'niveau': niveau, 'libelle': DEPTH_LABELS[niveau],
            'detail': ', '.join(parts) if parts else 'valeurs automatiques seulement'}


def latest_models(building, values=None):
    """{niveau: (calcul, à_jour)} — dernier calcul terminé de l'état actuel par niveau. « À jour » : même
    empreinte d'entrées que la fiche actuelle (même logique que l'historique) ; None pour un N2 lancé
    hors fiche (page Calcul 3D)."""
    from . import calc_service
    out = {}
    for lvl in ('N0', 'N1', 'N2'):
        run = calc_service.latest(building, lvl)
        if run is None:
            continue
        a_jour = None
        if calc_service.from_fiche(run):
            a_jour = run.inputs_hash == calc_service.inputs_hash_for(building, lvl, None, (run.weather or {}).get('hash'),
                                                                     values)
        out[lvl] = (run, a_jour)
    return out


def modeles(building, values=None):
    return [{'niveau': lvl, 'libelle': LEVEL_LABELS[lvl], 'computed_at': run.created_at, 'a_jour': a_jour}
            for lvl, (run, a_jour) in latest_models(building, values).items()]


def latest_n1(building):
    return (CalcRun.objects.filter(building=building, level='N1', scenario__isnull=True, status=Job.DONE)
            .order_by('-created_at').first())


def table(portfolio, buildings):
    rows = []
    for b in buildings:
        run = latest_n1(b)
        ind = run.indicators if run else {}
        eff = facts.effective(b)
        vals = {k: f.value for k, f in eff.items()}
        heat = ind.get('heating_kwh')
        rows.append({
            'id': b.pk, 'name': b.name, 'address': b.address, 'rnb_id': b.rnb_id, 'status': b.pipeline_status,
            'lat': b.georef_lat, 'lon': b.georef_lon,
            'usage': vals.get('identite.usage'), 'surface_m2': ind.get('surface_m2') or vals.get('identite.surface_m2'),
            'annee': vals.get('identite.annee_construction'),
            'heating_kwh_m2': ind.get('heating_kwh_m2'), 'p10': ind.get('heating_kwh_m2_p10'),
            'p90': ind.get('heating_kwh_m2_p90'), 'heating_kwh': heat, 'heating_kw_max': ind.get('heating_kw_max'),
            'hours_over_28': ind.get('hours_over_28'), 'dpe': vals.get('dpe.etiquette'),
            'dpe_ep_kwh_m2': vals.get('dpe.conso_ep_kwh_m2'), 'final_kwh_m2': ind.get('final_kwh_m2'),
            'primary_kwh_m2': ind.get('primary_kwh_m2'), 'model_label': ind.get('dpe_label_model'),
            'confidence': b.confidence, 'completeness': b.completeness,
            'level': 'N1' if run else None, 'computed_at': run.created_at if run else None,
            'priority': round(heat * (0.5 + 0.5 * b.confidence), 0) if heat else None,
            'errors': {k: v.get('error') for k, v in ((b.pipeline or {}).get('steps') or {}).items() if not v.get('ok')},
            'approfondissement': approfondissement(b, eff),
            'modeles': modeles(b, vals),
        })
    return rows


EXPORT_COLUMNS = [('id', 'id'), ('nom', 'name'), ('adresse', 'address'), ('rnb', 'rnb_id'), ('statut', 'status'),
                  ('usage', 'usage'), ('surface_m2', 'surface_m2'), ('annee', 'annee'),
                  ('besoin_kwh_m2', 'heating_kwh_m2'), ('besoin_p10', 'p10'), ('besoin_p90', 'p90'),
                  ('besoin_kwh', 'heating_kwh'), ('puissance_kw', 'heating_kw_max'), ('heures_sup_28', 'hours_over_28'),
                  ('energie_finale_kwh_m2', 'final_kwh_m2'), ('energie_primaire_kwh_m2', 'primary_kwh_m2'),
                  ('etiquette_modele', 'model_label'), ('dpe', 'dpe'), ('dpe_ep_kwh_m2', 'dpe_ep_kwh_m2'),
                  ('confiance', 'confidence'), ('completude', 'completeness'), ('priorite', 'priority')]


def to_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow([c for c, _ in EXPORT_COLUMNS])
    for r in rows:
        line = []
        for _c, k in EXPORT_COLUMNS:
            v = r.get(k)
            if isinstance(v, str) and v[:1] in ('=', '+', '-', '@', '\t', '\r'):
                v = "'" + v  # pas de formule exécutée à l'ouverture dans un tableur
            if isinstance(v, float):
                v = f'{v:.3f}'.rstrip('0').rstrip('.').replace('.', ',')
            line.append('' if v is None else str(v))
        w.writerow(line)
    return '﻿' + buf.getvalue()
