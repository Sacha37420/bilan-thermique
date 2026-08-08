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
]


class Command(BaseCommand):
    help = "Peuple la bibliothèque de modèles de paroi avec un catalogue de départ (vitrages, murs ITE/ITI, toitures RT2005/RT2012/RE2020)."

    def handle(self, *args, **options):
        for entry in CATALOGUE:
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

        self.stdout.write(self.style.SUCCESS(f"Terminé — {len(CATALOGUE)} modèle(s) dans le catalogue."))
