"""Peuple la bibliothèque de modèles de paroi avec un catalogue de départ :
fenêtres simple/double vitrage usuelles, murs ITE/ITI et toitures pour
chaque génération de réglementation thermique (RT2005, RT2012, RE2020).

Idempotent (update_or_create par nom) — peut être relancé sans risque après un
nouveau déploiement ou pour rafraîchir les valeurs.

Les U indicatifs donnés dans les descriptions sont calculés avec les
résistances superficielles conventionnelles Rsi=0,13 et Rse=0,04 m²·K/W —
très proches des valeurs par défaut de l'app (h_i=8 -> 0,125 ; h_e=25 -> 0,04),
donc représentatifs de ce que renverra un calcul avec les réglages par défaut.
"""

from django.core.management.base import BaseCommand

from api.models import ParoiModel

# ── Couches réutilisables (matériaux courants) ──────────────────────────────

ENDUIT_ITE = {'e': 0.01, 'lam': 0.5, 'rho': 1200, 'c': 1000, 'tau': 0, 'r': 0.5, 'alpha': 0.5}
ENDUIT_EXT = {'e': 0.015, 'lam': 0.87, 'rho': 1800, 'c': 1000, 'tau': 0, 'r': 0.5, 'alpha': 0.5}
BLOC_BETON = {'e': 0.20, 'lam': 1.05, 'rho': 1400, 'c': 1000, 'tau': 0, 'r': 0.9, 'alpha': 0.1}
PLACO = {'e': 0.013, 'lam': 0.25, 'rho': 850, 'c': 1000, 'tau': 0, 'r': 0.9, 'alpha': 0.1}


def isolant(epaisseur):
    """Laine minérale, épaisseur en m — jamais atteinte par le soleil dans les
    murs ci-dessous (toujours derrière la première couche opaque), donc
    tau/r/alpha n'a pas d'incidence physique ici : valeurs neutres."""
    return {'e': epaisseur, 'lam': 0.035, 'rho': 25, 'c': 1030, 'tau': 0, 'r': 0.9, 'alpha': 0.1}


def mur_ite(epaisseur_isolant):
    return [dict(ENDUIT_ITE), isolant(epaisseur_isolant), dict(BLOC_BETON), dict(PLACO)]


def mur_iti(epaisseur_isolant):
    return [dict(ENDUIT_EXT), dict(BLOC_BETON), isolant(epaisseur_isolant), dict(PLACO)]


# Toiture (rampants/combles) : couverture (tuile, exposée au soleil) + support
# bois + isolant + plâtre intérieur. Les toitures sont conventionnellement
# bien plus isolées que les murs à réglementation égale — d'où des épaisseurs
# nettement supérieures (200/300/400 mm contre 100/140/180 mm pour les murs).
COUVERTURE_TUILE = {'e': 0.02, 'lam': 1.0, 'rho': 2000, 'c': 800, 'tau': 0, 'r': 0.3, 'alpha': 0.7}
SUPPORT_TOITURE = {'e': 0.018, 'lam': 0.15, 'rho': 500, 'c': 1600, 'tau': 0, 'r': 0.9, 'alpha': 0.1}


def toiture(epaisseur_isolant):
    return [dict(COUVERTURE_TUILE), dict(SUPPORT_TOITURE), isolant(epaisseur_isolant), dict(PLACO)]


# ── Plancher bas sur terre-plein (Lot AB3) ──────────────────────────────────
# Manquait au catalogue : le mode simplifié impose de choisir un « modèle
# opaque » pour le groupe `sol`, et la liste n'offrait que des murs et des
# toitures. Ordre des couches : de l'EXTÉRIEUR (nœud 0 = côté terre) vers
# l'intérieur, comme partout ailleurs — donc dalle, puis isolant, puis chape.
# tau/r/alpha sans incidence physique ici (aucun rayonnement solaire n'atteint
# un plancher au contact du sol) : valeurs neutres.
DALLE_BETON = {'e': 0.15, 'lam': 1.75, 'rho': 2300, 'c': 1000, 'tau': 0, 'r': 0.9, 'alpha': 0.1}
CHAPE = {'e': 0.05, 'lam': 1.15, 'rho': 2000, 'c': 1000, 'tau': 0, 'r': 0.9, 'alpha': 0.1}


def isolant_sous_chape(epaisseur):
    """Polystyrène extrudé, épaisseur en m."""
    return {'e': epaisseur, 'lam': 0.034, 'rho': 30, 'c': 1450, 'tau': 0, 'r': 0.9, 'alpha': 0.1}


def plancher_terre_plein(epaisseur_isolant):
    return [dict(DALLE_BETON), isolant_sous_chape(epaisseur_isolant), dict(CHAPE)]


# ── Bâti existant (Lot AI) ─────────────────────────────────────────────────
# Parois d'avant les réglementations actuelles, par époque et par matériau —
# ce que la pré-assignation d'après la BD TOPO (matériaux des murs et de la
# toiture, année d'apparition) va chercher, par NOM exact (voir
# observed_env.WALL_BY_ERA/ROOF_BY_ERA/FLOOR_BY_ERA). Même statut que le reste
# du catalogue : ordres de grandeur usuels, pas une référence. Parement
# extérieur à alpha 0,5 : l'absorptance réelle est posée par triangle
# (alpha_ext) quand le matériau ou l'orthophoto la donnent.

ENDUIT_CHAUX = {'e': 0.02, 'lam': 0.87, 'rho': 1800, 'c': 1000, 'tau': 0, 'r': 0.5, 'alpha': 0.5}
PLATRE_ENDUIT = {'e': 0.015, 'lam': 0.35, 'rho': 1000, 'c': 1000, 'tau': 0, 'r': 0.9, 'alpha': 0.1}


def maconnerie(e, lam, rho, c):
    return {'e': e, 'lam': lam, 'rho': rho, 'c': c, 'tau': 0, 'r': 0.9, 'alpha': 0.1}


def isolant_ancien(epaisseur):
    """Isolant des années 1975–2000 (laine ou polystyrène de première génération)."""
    return {'e': epaisseur, 'lam': 0.041, 'rho': 20, 'c': 1030, 'tau': 0, 'r': 0.9, 'alpha': 0.1}


def mur_iti_ancien(epaisseur_isolant):
    return [dict(ENDUIT_EXT), dict(BLOC_BETON), isolant_ancien(epaisseur_isolant), dict(PLACO)]


def toiture_ancienne(epaisseur_isolant):
    return [dict(COUVERTURE_TUILE), dict(SUPPORT_TOITURE), isolant_ancien(epaisseur_isolant), dict(PLACO)]


LAME_AIR_TOITURE = {'e': 0.05, 'lam': 0.28, 'rho': 1.2, 'c': 1000, 'tau': 0, 'r': 0.9, 'alpha': 0.1}
BARDAGE_BOIS = {'e': 0.02, 'lam': 0.13, 'rho': 500, 'c': 1600, 'tau': 0, 'r': 0.3, 'alpha': 0.7}
OSB = {'e': 0.012, 'lam': 0.13, 'rho': 600, 'c': 1700, 'tau': 0, 'r': 0.9, 'alpha': 0.1}
# Laine entre montants : λ équivalent dégradé par les ponts thermiques des montants.
LAINE_OSSATURE = {'e': 0.10, 'lam': 0.045, 'rho': 40, 'c': 1100, 'tau': 0, 'r': 0.9, 'alpha': 0.1}

EXISTANT = [
    {'name': 'Mur pierre 50 cm (avant 1948)',
     'description': "Moellons de pierre calcaire 50 cm, enduit chaux extérieur, enduit plâtre intérieur, "
                    "sans isolant. U indicatif ≈ 1,9 W/m²·K. Aussi retenu pour la meulière.",
     'layers': [dict(ENDUIT_CHAUX), maconnerie(0.50, 1.7, 2200, 900), dict(PLATRE_ENDUIT)]},
    {'name': 'Mur brique pleine 34 cm (avant 1948)',
     'description': "Brique pleine 34 cm, enduit extérieur, enduit plâtre intérieur, sans isolant. "
                    "U indicatif ≈ 1,6 W/m²·K.",
     'layers': [dict(ENDUIT_CHAUX), maconnerie(0.34, 0.84, 1800, 840), dict(PLATRE_ENDUIT)]},
    {'name': 'Mur pan de bois / torchis (avant 1948)',
     'description': "Colombage hourdé de torchis 15 cm, enduit, sans isolant. U indicatif ≈ 2,0 W/m²·K.",
     'layers': [dict(ENDUIT_CHAUX), maconnerie(0.15, 0.55, 1400, 1000), dict(PLATRE_ENDUIT)]},
    {'name': 'Mur béton banché 16 cm non isolé (1948–1974)',
     'description': "Béton banché 16 cm, enduit, sans isolant — reconstruction et grands ensembles. "
                    "U indicatif ≈ 3,2 W/m²·K.",
     'layers': [dict(ENDUIT_CHAUX), maconnerie(0.16, 2.0, 2300, 1000), dict(PLATRE_ENDUIT)]},
    {'name': 'Mur parpaing 20 cm non isolé (1948–1974)',
     'description': "Bloc béton creux 20 cm enduit, plâtre intérieur, sans isolant. U indicatif ≈ 2,3 W/m²·K.",
     'layers': [dict(ENDUIT_CHAUX), dict(BLOC_BETON), dict(PLATRE_ENDUIT)]},
    {'name': 'Mur maçonné ITI — 1975–1981 (isolant 40 mm)',
     'description': "Bloc béton 20 cm + 40 mm d'isolant (1ʳᵉ réglementation thermique, 1974). "
                    "U indicatif ≈ 0,71 W/m²·K.",
     'layers': mur_iti_ancien(0.04)},
    {'name': 'Mur maçonné ITI — 1982–1988 (isolant 60 mm)',
     'description': "Bloc béton 20 cm + 60 mm d'isolant. U indicatif ≈ 0,53 W/m²·K.",
     'layers': mur_iti_ancien(0.06)},
    {'name': 'Mur maçonné ITI — 1989–2000 (isolant 80 mm)',
     'description': "Bloc béton 20 cm + 80 mm d'isolant (RT 1988). U indicatif ≈ 0,42 W/m²·K.",
     'layers': mur_iti_ancien(0.08)},
    {'name': 'Mur ossature bois (isolant 100 mm)',
     'description': "Bardage bois + OSB + 100 mm de laine entre montants + plaque de plâtre. "
                    "U indicatif ≈ 0,37 W/m²·K.",
     'layers': [dict(BARDAGE_BOIS), dict(OSB), dict(LAINE_OSSATURE), dict(PLACO)]},
    {'name': 'Toiture non isolée (avant 1975)',
     'description': "Couverture + support bois + lame d'air + plafond, sans isolant. U indicatif ≈ 2,0 W/m²·K.",
     'layers': [dict(COUVERTURE_TUILE), dict(SUPPORT_TOITURE), dict(LAME_AIR_TOITURE), dict(PLACO)]},
    {'name': 'Toiture isolée — 1975–1981 (isolant 60 mm)',
     'description': "Couverture + support + 60 mm d'isolant + plafond. U indicatif ≈ 0,56 W/m²·K.",
     'layers': toiture_ancienne(0.06)},
    {'name': 'Toiture isolée — 1982–1988 (isolant 100 mm)',
     'description': "Couverture + support + 100 mm d'isolant + plafond. U indicatif ≈ 0,36 W/m²·K.",
     'layers': toiture_ancienne(0.10)},
    {'name': 'Toiture isolée — 1989–2000 (isolant 150 mm)',
     'description': "Couverture + support + 150 mm d'isolant + plafond. U indicatif ≈ 0,25 W/m²·K.",
     'layers': toiture_ancienne(0.15)},
    {'name': 'Plancher bas non isolé (avant 1975)',
     'description': "Dallage béton 15 cm + chape 5 cm sur terre-plein, sans isolant. U indicatif ≈ 1,25 W/m²·K "
                    "(Rsi = 0,17 et r_ground = 0,5 compris).",
     'layers': [dict(DALLE_BETON), dict(CHAPE)]},
]

# ── Vitrages ─────────────────────────────────────────────────────────────

VITRAGE_SIMPLE = [
    {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.87, 'r': 0.07, 'alpha': 0.06},
]

VITRAGE_DOUBLE = [
    {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.88, 'r': 0.06, 'alpha': 0.06},
    # Lame d'air 16 mm, ordinaire (sans argon ni couche basse émissivité) :
    # lambda "équivalent" incluant convection + rayonnement, pas la seule
    # conduction de l'air (0,025) — R_gap ~ 0,17 m²K/W, valeur usuelle DTU.
    {'e': 0.016, 'lam': 0.094, 'rho': 1.2, 'c': 1000, 'tau': 0.97, 'r': 0.01, 'alpha': 0.02},
    {'e': 0.004, 'lam': 1.0, 'rho': 2500, 'c': 750, 'tau': 0.88, 'r': 0.06, 'alpha': 0.06},
]

CATALOGUE = [
    {
        'name': 'Fenêtre simple vitrage (usuelle)',
        'description': (
            "Vitrage simple 4 mm, sans lame d'air. Ug indicatif ≈ 5,7 W/m²·K. "
            "Facteur solaire g ≈ 0,87. Cadre PVC usuel modélisé séparément "
            "(frame_u ≈ 2,0 W/m²·K, frame_fraction ≈ 0,25 — ajustables par modèle)."
        ),
        'layers': VITRAGE_SIMPLE,
        'frame_u': 2.0,
        'frame_fraction': 0.25,
        'is_glazing': True,
    },
    {
        'name': 'Fenêtre double vitrage (usuelle, 4/16/4)',
        'description': (
            "Double vitrage standard 4/16/4 (verre + lame d'air 16 mm + verre), "
            "sans traitement basse émissivité. Ug indicatif ≈ 2,9 W/m²·K, "
            "facteur solaire g ≈ 0,75. Cadre PVC usuel modélisé séparément "
            "(frame_u ≈ 1,8 W/m²·K, frame_fraction ≈ 0,25 — ajustables par modèle)."
        ),
        'layers': VITRAGE_DOUBLE,
        'frame_u': 1.8,
        'frame_fraction': 0.25,
        'is_glazing': True,
    },
    {
        'name': 'Mur ITE — RT2005 (isolant 100 mm)',
        'description': (
            "Isolation par l'extérieur : enduit mince + 100 mm laine minérale + "
            "bloc béton 20 cm + plâtre intérieur. U indicatif ≈ 0,30 W/m²·K, "
            "représentatif d'une construction RT2005."
        ),
        'layers': mur_ite(0.10),
    },
    {
        'name': 'Mur ITI — RT2005 (isolant 100 mm)',
        'description': (
            "Isolation par l'intérieur : enduit extérieur + bloc béton 20 cm + "
            "100 mm laine minérale + plaque de plâtre BA13. U indicatif ≈ "
            "0,30 W/m²·K, représentatif d'une construction RT2005."
        ),
        'layers': mur_iti(0.10),
    },
    {
        'name': 'Mur ITE — RT2012 (isolant 140 mm)',
        'description': (
            "Isolation par l'extérieur : enduit mince + 140 mm laine minérale + "
            "bloc béton 20 cm + plâtre intérieur. U indicatif ≈ 0,23 W/m²·K, "
            "représentatif d'une construction RT2012."
        ),
        'layers': mur_ite(0.14),
    },
    {
        'name': 'Mur ITI — RT2012 (isolant 140 mm)',
        'description': (
            "Isolation par l'intérieur : enduit extérieur + bloc béton 20 cm + "
            "140 mm laine minérale + plaque de plâtre BA13. U indicatif ≈ "
            "0,23 W/m²·K, représentatif d'une construction RT2012."
        ),
        'layers': mur_iti(0.14),
    },
    {
        'name': 'Mur ITE — RE2020 (isolant 180 mm)',
        'description': (
            "Isolation par l'extérieur : enduit mince + 180 mm laine minérale + "
            "bloc béton 20 cm + plâtre intérieur. U indicatif ≈ 0,18 W/m²·K, "
            "représentatif d'une construction RE2020."
        ),
        'layers': mur_ite(0.18),
    },
    {
        'name': 'Mur ITI — RE2020 (isolant 180 mm)',
        'description': (
            "Isolation par l'intérieur : enduit extérieur + bloc béton 20 cm + "
            "180 mm laine minérale + plaque de plâtre BA13. U indicatif ≈ "
            "0,18 W/m²·K, représentatif d'une construction RE2020."
        ),
        'layers': mur_iti(0.18),
    },
    {
        'name': 'Toiture — RT2005 (isolant 200 mm)',
        'description': (
            "Rampant/comble : couverture tuile + support bois + 200 mm laine "
            "minérale + plâtre intérieur. U indicatif ≈ 0,16 W/m²·K, "
            "représentatif d'une toiture RT2005 (les toitures sont "
            "conventionnellement plus isolées que les murs à génération égale)."
        ),
        'layers': toiture(0.20),
    },
    {
        'name': 'Toiture — RT2012 (isolant 300 mm)',
        'description': (
            "Rampant/comble : couverture tuile + support bois + 300 mm laine "
            "minérale + plâtre intérieur. U indicatif ≈ 0,11 W/m²·K, "
            "représentatif d'une toiture RT2012."
        ),
        'layers': toiture(0.30),
    },
    {
        'name': 'Toiture — RE2020 (isolant 400 mm)',
        'description': (
            "Rampant/comble : couverture tuile + support bois + 400 mm laine "
            "minérale + plâtre intérieur. U indicatif ≈ 0,08 W/m²·K, "
            "représentatif d'une toiture RE2020."
        ),
        'layers': toiture(0.40),
    },
    {
        'name': 'Plancher bas sur terre-plein — RT2005 (isolant 40 mm)',
        'description': (
            "Dallage sur terre-plein : dalle béton 15 cm + 40 mm polystyrène "
            "extrudé + chape 5 cm. À utiliser sur les triangles marqués « Sol » "
            "(page Bâtiment). U indicatif ≈ 0,51 W/m²·K, résistances superficielle "
            "intérieure (Rsi = 0,17, flux descendant) et de sol (r_ground = 0,5, "
            "réglable page Calcul 3D) comprises."
        ),
        'layers': plancher_terre_plein(0.04),
    },
    {
        'name': 'Plancher bas sur terre-plein — RT2012 (isolant 80 mm)',
        'description': (
            "Dallage sur terre-plein : dalle béton 15 cm + 80 mm polystyrène "
            "extrudé + chape 5 cm. À utiliser sur les triangles marqués « Sol » "
            "(page Bâtiment). U indicatif ≈ 0,32 W/m²·K, mêmes conventions que "
            "ci-dessus."
        ),
        'layers': plancher_terre_plein(0.08),
    },
    {
        'name': 'Plancher bas sur terre-plein — RE2020 (isolant 120 mm)',
        'description': (
            "Dallage sur terre-plein : dalle béton 15 cm + 120 mm polystyrène "
            "extrudé + chape 5 cm. À utiliser sur les triangles marqués « Sol » "
            "(page Bâtiment). U indicatif ≈ 0,23 W/m²·K, mêmes conventions que "
            "ci-dessus."
        ),
        'layers': plancher_terre_plein(0.12),
    },
]


class Command(BaseCommand):
    help = "Peuple la bibliothèque de modèles de paroi avec un catalogue de départ (vitrages, murs ITE/ITI, toitures RT2005/RT2012/RE2020)."

    def handle(self, *args, **options):
        for entry in CATALOGUE + EXISTANT:
            obj, created = ParoiModel.objects.update_or_create(
                name=entry['name'],
                defaults={
                    'description': entry['description'], 'layers': entry['layers'],
                    'frame_u': entry.get('frame_u'), 'frame_fraction': entry.get('frame_fraction'),
                    'is_glazing': entry.get('is_glazing', False),
                },
            )
            verb = 'créé' if created else 'mis à jour'
            self.stdout.write(self.style.SUCCESS(f"  {verb} : {obj.name}"))

        self.stdout.write(self.style.SUCCESS(f"Terminé — {len(CATALOGUE) + len(EXISTANT)} modèle(s) dans le catalogue."))
