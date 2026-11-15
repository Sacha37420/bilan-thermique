"""Systèmes et consommations (Lot 16, refonte — § B5). Aide à la décision, pas un DPE (D6).

Du besoin (N1 horaire ou N0 mensuel) à l'énergie finale, primaire, au CO₂ et au coût :
- chauffage : rendement global = génération × émission × distribution × régulation (repères de la
  méthode 3CL) ; pompe à chaleur : COP horaire selon la température extérieure (fraction de Carnot
  0,40 entre la source et la température d'eau des émetteurs, borné à [1,5 ; 5,0] — SCOP de l'ordre de
  4,5 sur plancher chauffant et de 3 sur radiateurs haute température en climat tempéré) ;
- ECS : besoin par occupant (logement) ou par m² (tertiaire), rendement du système ;
- refroidissement (EER 3), éclairage et auxiliaires de ventilation, pour les cinq usages du DPE ;
- facteurs d'énergie primaire et de CO₂ de l'arrêté DPE 2021 ; prix indicatifs 2025 ;
- étiquette « modèle » par le double seuil énergie primaire / GES du DPE logement 2021.
"""

import numpy as np

ENERGY_OF = {'chaudiere_gaz': 'gaz', 'chaudiere_gaz_cond': 'gaz', 'chaudiere_fioul': 'fioul', 'pac_air_eau': 'electricite',
             'pac_air_air': 'electricite', 'joule': 'electricite', 'reseau_chaleur': 'reseau', 'bois': 'bois'}
PRIMARY_FACTOR = {'electricite': 2.3, 'gaz': 1.0, 'fioul': 1.0, 'bois': 1.0, 'reseau': 1.0}
CO2_KG_KWH = {'electricite_chauffage': 0.079, 'electricite': 0.065, 'gaz': 0.227, 'fioul': 0.324, 'bois': 0.030, 'reseau': 0.130}
PRICE_EUR_KWH = {'electricite': 0.25, 'gaz': 0.11, 'fioul': 0.12, 'bois': 0.07, 'reseau': 0.10}
EMISSION = {'radiateurs_ht': 0.95, 'radiateurs_bt': 0.95, 'plancher': 0.94, 'convecteurs': 0.97, 'soufflage': 0.93}
REGULATION = {'aucune': 0.85, 'centrale': 0.90, 'pieces': 0.95, 'programmable': 0.97}
SINK_T = {'radiateurs_ht': 55.0, 'radiateurs_bt': 40.0, 'plancher': 32.0, 'convecteurs': 55.0, 'soufflage': 35.0}
ECS_EFF = {'ballon_elec': 0.85, 'thermodynamique': 2.5, 'chaudiere': 0.75, 'solaire': 1.6, 'instantane_gaz': 0.80}
ECS_ENERGY = {'ballon_elec': 'electricite', 'thermodynamique': 'electricite', 'solaire': 'electricite', 'instantane_gaz': 'gaz'}
ECS_KWH_PER_OCCUPANT = 750.0     # utile, ≈ 40 L/jour à 45 K
ECS_KWH_M2_TERTIARY = 2.0
LIGHTING_KWH_M2 = {'logement': 3.0, 'tertiaire': 12.0}
FAN_WH_M3 = {'naturelle': 0.0, 'vmc_sf_auto': 0.25, 'vmc_sf_hygro': 0.20, 'vmc_df': 0.45}
DPE_THRESHOLDS = [('A', 70, 6), ('B', 110, 11), ('C', 180, 30), ('D', 250, 50), ('E', 330, 70), ('F', 420, 100)]
RESIDENTIAL = ('maison', 'logement_collectif')


def dpe_label(ep_m2, co2_m2):
    for label, ep, ges in DPE_THRESHOLDS:
        if ep_m2 <= ep and co2_m2 <= ges:
            return label
    return 'G'


def heat_pump_cop(t_ext, sink_c, air_air=False):
    sink = (sink_c if not air_air else 35.0) + 273.15
    src = np.asarray(t_ext, dtype=float) + 273.15
    cop = 0.40 * sink / np.maximum(sink - src, 5.0)
    return np.clip(cop, 1.5, 5.0)


def evaluate(values, heating_kwh, cooling_kwh, a_floor, volume, heat_series_w=None, t_ext_series=None):
    """Consommations à partir du besoin. `heat_series_w`/`t_ext_series` (horaires) permettent un COP
    horaire de PAC ; sans eux, COP saisonnier (fiche ou valeur type)."""
    usage = values.get('identite.usage') or 'autre'
    residential = usage in RESIDENTIAL
    gen = values.get('systeme.generateur') or 'chaudiere_gaz'
    energy = ENERGY_OF.get(gen, 'gaz')
    emitter = values.get('systeme.emetteurs') or ('convecteurs' if gen == 'joule' else 'radiateurs_ht')
    reg = values.get('systeme.regulation') or 'centrale'
    eta_e, eta_r = EMISSION.get(emitter, 0.95), REGULATION.get(reg, 0.90)
    eta_d = 0.92 if (values.get('identite.logements') or 1) > 2 else 0.95
    out = {'generator': gen, 'energy': energy, 'emitter': emitter}
    if gen.startswith('pac') and heat_series_w is not None and t_ext_series is not None:
        cop = heat_pump_cop(t_ext_series, SINK_T.get(emitter, 45.0), gen == 'pac_air_air')
        heat = np.clip(np.asarray(heat_series_w, dtype=float), 0, None)
        final_heat = float((heat / cop).sum() / 1000.0) / (eta_e * eta_d * eta_r)
        out['cop_seasonal'] = round(float(heat.sum() / max((heat / cop).sum(), 1e-9)), 2)
    else:
        eta_g = float(values.get('systeme.rendement_generation') or (2.8 if gen.startswith('pac') else 0.85))
        final_heat = heating_kwh / (eta_g * eta_e * eta_d * eta_r)
        if gen.startswith('pac'):
            out['cop_seasonal'] = eta_g
    occupants = values.get('usage.occupants') or max(1.0, a_floor / (35.0 if usage == 'maison' else 30.0))
    ecs_useful = occupants * ECS_KWH_PER_OCCUPANT if residential else a_floor * ECS_KWH_M2_TERTIARY
    ecs_sys = values.get('systeme.ecs') or ('chaudiere' if energy in ('gaz', 'fioul') else 'ballon_elec')
    ecs_energy = energy if ecs_sys == 'chaudiere' else ECS_ENERGY.get(ecs_sys, 'electricite')
    final_ecs = ecs_useful / ECS_EFF.get(ecs_sys, 0.85)
    final_cool = cooling_kwh / 3.0
    final_light = a_floor * LIGHTING_KWH_M2['logement' if residential else 'tertiaire']
    vt = values.get('ventilation.type') or 'vmc_sf_auto'
    debit = float(values.get('ventilation.debit_vol_h') or 0.5) * volume
    final_aux = FAN_WH_M3.get(vt, 0.25) * debit * 8760 / 1000.0
    by_usage = {'chauffage': (final_heat, energy), 'ecs': (final_ecs, ecs_energy), 'refroidissement': (final_cool, 'electricite'),
                'eclairage': (final_light, 'electricite'), 'auxiliaires': (final_aux, 'electricite')}
    by_energy = {}
    primary = co2 = cost = 0.0
    for use, (kwh, en) in by_usage.items():
        by_energy[en] = by_energy.get(en, 0.0) + kwh
        primary += kwh * PRIMARY_FACTOR[en]
        co2 += kwh * (CO2_KG_KWH['electricite_chauffage'] if (en == 'electricite' and use == 'chauffage') else CO2_KG_KWH[en])
        cost += kwh * PRICE_EUR_KWH[en]
    final = sum(k for k, _ in by_usage.values())
    a = max(a_floor, 1.0)
    out.update({
        'final_kwh': round(final, 0), 'primary_kwh': round(primary, 0), 'co2_kg': round(co2, 0), 'cost_eur': round(cost, 0),
        'final_kwh_m2': round(final / a, 1), 'primary_kwh_m2': round(primary / a, 1), 'co2_kg_m2': round(co2 / a, 1),
        'by_usage_kwh': {u: round(k, 0) for u, (k, _) in by_usage.items()},
        'by_usage_energy': {u: en for u, (_k, en) in by_usage.items()},
        'by_energy_kwh': {e: round(k, 0) for e, k in by_energy.items()},
        'efficiency': {'emission': eta_e, 'distribution': eta_d, 'regulation': eta_r},
        'dpe_label_model': dpe_label(primary / a, co2 / a),
    })
    declared_ep = values.get('dpe.conso_ep_kwh_m2')
    if declared_ep:
        out['dpe_ep_declared'] = declared_ep
        out['dpe_gap_pct'] = round((primary / a / declared_ep - 1) * 100, 0)
    if values.get('dpe.etiquette'):
        out['dpe_label_declared'] = values['dpe.etiquette']
    return out
