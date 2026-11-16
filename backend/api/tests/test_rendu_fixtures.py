"""Données de test du secteur R (rendu) : un parc, deux bâtiments, des calculs aux formes I3, des potentiels I0 et
des réponses I3/I4/I5/I7 construites **à la forme du contrat** (les producteurs P et A sont simulés : un test du
rendu ne dépend jamais d'un autre secteur). Pas de test ici."""

import io
from unittest import mock

from django.utils import timezone

from .. import facts
from ..models import Bouquet, Building, CalcRun, Job, Portfolio, PortfolioMember, Potentiel, Scenario
from ..testing import client_for, patch_jwks

HOSTILE = '<img src=x onerror=alert(1)>"\'&</script>'
MOIS12 = list(range(1, 13))


def indicateurs(k=1.0, surface=120.0):
    return {'level': 'N1', 'hours': 8760, 'surface_m2': surface, 'heating_kwh': round(100 * k * surface, 1),
            'heating_kwh_m2': round(100 * k, 1), 'cooling_kwh': 0.0, 'cooling_kwh_m2': 0.0,
            'heating_kw_max': round(8 * k, 2), 'dh_re2020': round(300 * k, 1), 'dh_26': round(200 * k, 1),
            'degree_hours_over_26': round(200 * k, 1), 'heures_inconfort': round(150 * k), 'hours_over_26': 300,
            'hours_over_28': round(40 * k), 't_conf_methode': 'RE2020 adaptatif borné [26, 28] °C',
            'final_kwh_m2': round(130 * k, 1), 'primary_kwh_m2': round(150 * k, 1), 'co2_kg_m2': round(20 * k, 1),
            'cost_eur': round(1500 * k), 'final_kwh': round(130 * k * surface), 'primary_kwh': round(150 * k * surface),
            'co2_kg': round(20 * k * surface), 'dpe_label_model': 'D' if k > 0.8 else 'B',
            'confidence': 0.6, 'completeness': 0.7}


def resultats(k=1.0, surface=120.0):
    hiver = [3, 2.6, 2, 1.2, 0.5, 0, 0, 0, 0.3, 1, 2, 2.8]
    tot = sum(hiver)
    heat = [round(100 * k * surface * h / tot, 1) for h in hiver]
    t_op = []
    for m, n in enumerate([744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]):
        for i in range(n):
            t_op.append(round(19 + (6 if 5 <= m <= 7 else 0) * k + 3 * ((i % 24) / 23), 2))
    return {'heating_kwh': 100 * k * surface, 'balance': {
        'transmission_kwh': round(15000 * k, 1), 'ventilation_kwh': round(5000 * k, 1),
        'solar_windows_kwh': 4000.0, 'solar_opaque_kwh': 800.0, 'internal_kwh': 3000.0},
        'mensuel': {'heating_kwh': heat, 'cooling_kwh': [0.0] * 12,
                    'dh_re2020': [0, 0, 0, 0, 10, 60, 120, 90, 20, 0, 0, 0],
                    't_op_max_c': [20, 20, 21, 23, 25, 28, 30, 29, 26, 23, 21, 20],
                    't_ext_moy_c': [4, 5, 8, 11, 15, 18, 21, 20, 17, 12, 7, 5]},
        'energy': {'by_usage_kwh': {'chauffage': 14000 * k, 'ecs': 2500, 'refroidissement': 0, 'eclairage': 600,
                                    'auxiliaires': 300}},
        'solar_windows_by_orientation_kwh': {'S': 2500.0, 'E': 700.0, 'O': 600.0, 'N': 200.0},
        't_op': t_op}


def run(b, level='N1', scenario=None, k=1.0):
    return CalcRun.objects.create(building=b, scenario=scenario, level=level, status=Job.DONE,
                                  indicators=indicateurs(k), results=resultats(k), duration_s=1.2)


ACTIONS = [{'cle': 'ite', 'libelle': 'Isolation des murs par l’extérieur', 'appliquee': True, 'raison': None,
            'parametres': {'epaisseur_cm': 14}, 'effets': ['mur.u : 1,6 → 0,22 W/m²·K'], 'avertissements': []},
           {'cle': 'store_sud', 'libelle': 'Stores extérieurs au sud', 'appliquee': True, 'raison': None,
            'parametres': {}, 'effets': ['protection mobile'], 'avertissements': []},
           {'cle': 'pac', 'libelle': 'Pompe à chaleur', 'appliquee': False, 'raison': 'Déjà équipé.',
            'parametres': {}, 'effets': [], 'avertissements': []}]
CATALOGUE_I7 = {'familles': [{'cle': c, 'libelle': l} for c, l in (
    ('isolation', 'Isolation'), ('ventilation', 'Ventilation'), ('protections', 'Protections solaires'),
    ('vegetalisation', 'Végétalisation'), ('toiture_albedo', 'Toiture et albédo'), ('systemes', 'Chauffage et systèmes'))],
    'actions': [{'cle': 'ite', 'famille': 'isolation'}, {'cle': 'store_sud', 'famille': 'protections'},
                {'cle': 'pac', 'famille': 'systemes'}]}
I3 = [{'cle': 'heating_kwh_m2', 'libelle': 'Besoin de chauffage', 'unite': 'kWh/m²·an', 'niveaux': ['N0', 'N1', 'N2'],
       'additif': False, 'sens': 'bas_est_mieux', 'famille': 'energie'},
      {'cle': 'dh_re2020', 'libelle': 'DH RE2020', 'unite': '°C·h', 'niveaux': ['N1', 'N2'], 'additif': False,
       'sens': 'bas_est_mieux', 'famille': 'confort'},
      {'cle': 't_conf_methode', 'libelle': 'Méthode', 'unite': '', 'niveaux': ['N1'], 'additif': False,
       'sens': 'bas_est_mieux', 'famille': 'confort'}]


def scene_i4():
    v = [[0, 0, 0], [10, 0, 0], [10, 8, 0], [0, 8, 0], [0, 0, 6], [10, 0, 6], [10, 8, 6], [0, 8, 6]]
    t = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6],
         [3, 0, 4], [3, 4, 7]]
    n = len(t)
    return {'repere': 'batiment', 'vertices': v, 'triangles': t, 'groupes': ['sol'] * 2 + ['toiture'] * 2 + ['mur'] * 8,
            'type': ['paroi'] * n, 'periodes': {'hiver': [10, 11, 12, 1, 2, 3, 4], 'ete': [6, 7, 8]},
            'grandeurs': {g: [round(1 + i * 0.5, 3) for i in range(n)] for g in (
                'pertes_hiver_kwh_m2', 'pertes_hiver_kwh', 'gains_ete_kwh_m2', 'gains_ete_kwh', 'solaire_ete_kwh_m2',
                't_si_hiver_moy_c', 't_si_ete_max_c')},
            'unites': {'pertes_hiver_kwh_m2': 'kWh/m²', 'gains_ete_kwh_m2': 'kWh/m²'}}


def i5_batiment(ctx_bouquets, building, reel_run):
    out = []
    for bq in ctx_bouquets:
        p = Potentiel.objects.filter(building=building, bouquet=bq, niveau='N1').select_related('run').first()
        if p is None:
            out.append({'bouquet': {'id': bq.pk, 'nom': bq.nom, 'objectif': bq.objectif}, 'niveau': 'N1', 'statut': None,
                        'a_jour': False, 'calcule_le': None, 'scenario_id': None, 'indicateurs': None, 'ecarts': {},
                        'ecarts_pct': {}, 'actions': [], 'run_id': None})
            continue
        ind = p.run.indicators
        out.append({'bouquet': {'id': bq.pk, 'nom': bq.nom, 'objectif': bq.objectif}, 'niveau': 'N1', 'statut': 'fait',
                    'a_jour': True, 'calcule_le': p.calcule_le.isoformat(), 'scenario_id': p.scenario_id,
                    'indicateurs': ind,
                    'ecarts': {'heating_kwh_m2': ind['heating_kwh_m2'] - reel_run.indicators['heating_kwh_m2']},
                    'ecarts_pct': {'heating_kwh_m2': round(ind['heating_kwh_m2'] / reel_run.indicators['heating_kwh_m2'] - 1, 3)},
                    'actions': p.actions, 'run_id': p.run_id})
    return {'peut_calculer': True, 'peut_calculer_n2': True,
            'reel': {'niveau': 'N1', 'a_jour': True, 'indicateurs': reel_run.indicators, 'run_id': reel_run.pk,
                     'calcule_le': reel_run.created_at.isoformat()}, 'bouquets': out}


class RenduMixin:
    """Parc `p` (gestionnaire g, lecteur l), étranger o ; bâtiments b1 (bureau, 1972) et b2 (maison, 1995, nom hostile),
    bâtiment b3 sans calcul ; deux bouquets du lab ; potentiels N1 de b1 et b2 pour le premier bouquet, de b1 pour
    le second. Sources des autres secteurs simulées à la forme du contrat."""

    def setUp(self):
        jwks = patch_jwks()
        jwks.start()
        self.addCleanup(jwks.stop)
        self.p = Portfolio.objects.create(name='Parc test', owner_sub='g')
        PortfolioMember.objects.create(portfolio=self.p, sub='g', role=PortfolioMember.GESTIONNAIRE_PARC)
        PortfolioMember.objects.create(portfolio=self.p, sub='l', role=PortfolioMember.LECTEUR)
        self.g, self.l, self.o = client_for(sub='g'), client_for(sub='l'), client_for(sub='o')
        self.b1 = Building.objects.create(name='Mairie', portfolio=self.p, georef_lat=47.39, georef_lon=0.69,
                                          address='1 place de la Mairie')
        self.b2 = Building.objects.create(name='Maison ' + HOSTILE, portfolio=self.p, georef_lat=47.40, georef_lon=0.70)
        self.b3 = Building.objects.create(name='Hangar', portfolio=self.p)
        for b, usage, annee, dpe in ((self.b1, 'bureau', 1972, 'E'), (self.b2, 'maison', 1995, 'C')):
            facts.record(b, 'identite.usage', usage, 'declare', source='essai')
            facts.record(b, 'identite.annee_construction', annee, 'declare', source='essai')
            facts.record(b, 'identite.surface_m2', 120.0, 'declare', source='essai')
            facts.record(b, 'dpe.etiquette', dpe, 'declare', source=HOSTILE)
        self.r1, self.r2 = run(self.b1, k=1.0), run(self.b2, k=0.8)
        run(self.b1, 'N0', k=1.1)
        self.bq1 = Bouquet.objects.create(nom='Économiser l’énergie', objectif='energie', par_defaut=True, ordre=1,
                                          elements=[{'action': 'ite', 'parametres': {}}])
        self.bq2 = Bouquet.objects.create(nom='Minimiser les DH ' + HOSTILE, objectif='confort', par_defaut=True, ordre=2,
                                          elements=[{'action': 'store_sud', 'parametres': {}}])
        self.bouquets = [self.bq1, self.bq2]
        for b, bq, k in ((self.b1, self.bq1, 0.5), (self.b2, self.bq1, 0.45), (self.b1, self.bq2, 0.9)):
            s = Scenario.objects.create(building=b, name=f'potentiel {bq.nom}', bouquet=bq, genere=True)
            r = run(b, scenario=s, k=k)
            Potentiel.objects.create(building=b, bouquet=bq, scenario=s, niveau='N1', statut='fait', run=r,
                                     actions=ACTIONS, calcule_le=timezone.now())
        self.sources = self.simuler_sources()

    def simuler_sources(self, triangles=True):
        t = self

        def i5b(request, pk):
            b = Building.objects.get(pk=pk)
            r = CalcRun.objects.filter(building=b, level='N1', scenario__isnull=True).first()
            return i5_batiment(t.bouquets, b, r) if r else None

        patches = [mock.patch('api.rendu.sources.indicateurs', return_value=I3),
                   mock.patch('api.rendu.sources.actions', return_value=CATALOGUE_I7),
                   mock.patch('api.rendu.sources.potentiels_batiment', side_effect=i5b),
                   mock.patch('api.rendu.sources.triangles', return_value=scene_i4() if triangles else None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return patches

    def figure(self, client, graphe, portee='parc', cible=None):
        cible = cible or ({'parc': self.p.pk} if portee == 'parc' else {'batiment': self.b1.pk})
        return client.post('/api/rendu/figure/', {'portee': portee, 'cible': cible, 'graphe': graphe}, format='json')


def png(w=40, h=30):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (w, h), (30, 60, 120)).save(buf, 'PNG')
    return buf.getvalue()


__all__ = ['RenduMixin', 'HOSTILE', 'png', 'scene_i4', 'run', 'client_for', 'MOIS12']
