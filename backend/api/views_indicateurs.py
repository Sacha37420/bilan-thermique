"""Secteur P (I3) : liste canonique des indicateurs d'un calcul (`CalcRun.indicators`).

Chantier bt-potentiels — voir chantiers/bt-potentiels/CONTRAT.md, I3. `GET /api/indicateurs/` (lecture, tout compte
admis par l'app) : [{cle, libelle, unite, niveaux, additif, sens, famille}]. `additif` : la somme sur des bâtiments a
un sens (kWh, kg, €), pas une valeur rapportée au m² ni une température. `sens` : bas_est_mieux | haut_est_mieux.
Séries mensuelles (`CalcRun.results['mensuel']`) : `SERIES_MENSUELLES`.
"""

from rest_framework.response import Response
from rest_framework.views import APIView

ALL, HOURLY = ['N0', 'N1', 'N2'], ['N1', 'N2']


def _i(cle, libelle, unite, niveaux, additif, sens, famille):
    return {'cle': cle, 'libelle': libelle, 'unite': unite, 'niveaux': list(niveaux), 'additif': additif,
            'sens': sens, 'famille': famille}


BAS, HAUT = 'bas_est_mieux', 'haut_est_mieux'
INDICATEURS = [
    _i('heating_kwh', 'Besoin de chauffage', 'kWh/an', ALL, True, BAS, 'energie'),
    _i('heating_kwh_m2', 'Besoin de chauffage par m²', 'kWh/m²·an', ALL, False, BAS, 'energie'),
    _i('heating_kwh_m2_p10', 'Besoin de chauffage par m² (P10)', 'kWh/m²·an', ['N1'], False, BAS, 'energie'),
    _i('heating_kwh_m2_p90', 'Besoin de chauffage par m² (P90)', 'kWh/m²·an', ['N1'], False, BAS, 'energie'),
    _i('heating_kw_max', 'Puissance de chauffage maximale', 'kW', HOURLY, True, BAS, 'energie'),
    _i('cooling_kwh', 'Besoin de refroidissement', 'kWh/an', ALL, True, BAS, 'energie'),
    _i('cooling_kwh_m2', 'Besoin de refroidissement par m²', 'kWh/m²·an', ALL, False, BAS, 'energie'),
    _i('final_kwh', 'Énergie finale', 'kWh/an', ALL, True, BAS, 'energie'),
    _i('final_kwh_m2', 'Énergie finale par m²', 'kWh/m²·an', ALL, False, BAS, 'energie'),
    _i('primary_kwh', 'Énergie primaire', 'kWh/an', ALL, True, BAS, 'energie'),
    _i('primary_kwh_m2', 'Énergie primaire par m²', 'kWh/m²·an', ALL, False, BAS, 'energie'),
    _i('co2_kg', 'Émissions de CO₂', 'kg/an', ALL, True, BAS, 'energie'),
    _i('co2_kg_m2', 'Émissions de CO₂ par m²', 'kg/m²·an', ALL, False, BAS, 'energie'),
    _i('cost_eur', 'Coût de l’énergie (indicatif)', '€/an', ALL, True, BAS, 'energie'),
    _i('dh_re2020', 'Degrés-heures d’inconfort (RE2020)', '°C·h', HOURLY, False, BAS, 'confort'),
    _i('heures_inconfort', 'Heures d’inconfort (RE2020)', 'h', HOURLY, False, BAS, 'confort'),
    _i('dh_26', 'Degrés-heures au-dessus de 26 °C', '°C·h', HOURLY, False, BAS, 'confort'),
    _i('degree_hours_over_26', 'Degrés-heures au-dessus de 26 °C (ancien nom)', '°C·h', HOURLY, False, BAS, 'confort'),
    _i('hours_over_26', 'Heures au-dessus de 26 °C', 'h', HOURLY, False, BAS, 'confort'),
    _i('hours_over_28', 'Heures au-dessus de 28 °C', 'h', HOURLY, False, BAS, 'confort'),
    _i('t_air_mean', 'Température d’air moyenne', '°C', HOURLY, False, BAS, 'confort'),
    _i('t_conf_methode', 'Méthode du seuil de confort', '', HOURLY, False, BAS, 'donnees'),
    _i('surface_m2', 'Surface chauffée', 'm²', ALL, True, HAUT, 'donnees'),
    _i('dpe_label_model', 'Étiquette du modèle (indicative, pas un DPE)', '', ALL, False, BAS, 'donnees'),
    _i('dpe_gap_pct', 'Écart au DPE déclaré', '%', ALL, False, BAS, 'donnees'),
    _i('confidence', 'Confiance de la fiche', '', ALL, False, HAUT, 'donnees'),
    _i('completeness', 'Complétude de la fiche', '', ALL, False, HAUT, 'donnees'),
]
SERIES_MENSUELLES = [
    {'cle': 'heating_kwh', 'libelle': 'Besoin de chauffage mensuel', 'unite': 'kWh', 'niveaux': ALL},
    {'cle': 'cooling_kwh', 'libelle': 'Besoin de refroidissement mensuel', 'unite': 'kWh', 'niveaux': HOURLY},
    {'cle': 'dh_re2020', 'libelle': 'Degrés-heures d’inconfort mensuels (RE2020)', 'unite': '°C·h', 'niveaux': HOURLY},
    {'cle': 't_op_max_c', 'libelle': 'Température opérative maximale du mois', 'unite': '°C', 'niveaux': HOURLY},
    {'cle': 't_ext_moy_c', 'libelle': 'Température extérieure moyenne du mois', 'unite': '°C', 'niveaux': HOURLY},
]


class IndicateursView(APIView):
    def get(self, request):
        return Response(INDICATEURS)
