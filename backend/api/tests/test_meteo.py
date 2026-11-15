"""Tests — meteo.

Découpé de l'ancien `api/tests.py` (Lot 5, refonte) — contenu des classes inchangé.
"""

import math
from unittest import mock
import numpy as np
from django.test import SimpleTestCase, TestCase
from .. import building_solver, elevation, geodata, geometry, serializers, shadow, solver, weather_source
from ..models import Building, Environment
from .helpers import (  # noqa: F401
    DT_SECONDS,
    _flat_wall_layer,
    _no_sun_weather_1d,
    _no_sun_weather_3d,
    _single_triangle_envelope,
    _angle_diff_deg,
    _wall_envelope,
    _no_sun_weather_3d_wind,
    _veg_box,
    SCENE_LAT,
    SCENE_LON,
    SCENE_GROUND_Z,
    SCENE_SLOPE_X,
    BDTOPO_OFFSET,
    HOUSE,
    FLAT,
    SHED,
    GONE,
    TREE,
    _scene_ground,
    _scene_points,
    _scene_bdtopo,
    _closed_and_outward,
    _terrain_env,
    _vertical_wall_envelope,
    _box_building,
)


class SolarEphemerisTest(SimpleTestCase):
    """Lot L — déclinaison solaire et équation du temps (weather_source._declination_deg/
    _equation_of_time_min, formules de Spencer 1971). Oracle : bornes et faits
    astronomiques indépendants de la formule elle-même (inclinaison de l'axe terrestre
    ≈23,44°, bien établie), PAS des valeurs de référence recopiées d'une autre
    calculatrice solaire — pour ne pas simplement retester la même formule sous un
    autre nom."""

    databases = []

    def test_declination_never_exceeds_axial_tilt(self):
        for n in range(1, 366):
            gamma = 2.0 * math.pi * (n - 1) / 365.0
            self.assertLessEqual(abs(weather_source._declination_deg(gamma)), 23.6)

    def test_declination_sign_matches_hemisphere_season(self):
        # ~21 juin (jour 172) : été hémisphère nord, déclinaison nettement positive.
        gamma_june = 2.0 * math.pi * (172 - 1) / 365.0
        self.assertGreater(weather_source._declination_deg(gamma_june), 20.0)
        # ~21 décembre (jour 355) : hiver hémisphère nord, déclinaison nettement négative.
        gamma_dec = 2.0 * math.pi * (355 - 1) / 365.0
        self.assertLess(weather_source._declination_deg(gamma_dec), -20.0)

    def test_equation_of_time_bounded(self):
        # Écart connu entre temps solaire vrai et temps solaire moyen : quelques
        # dizaines de minutes au plus sur toute l'année (borne large et sûre,
        # pas une valeur de référence précise à une date donnée).
        for n in range(1, 366):
            gamma = 2.0 * math.pi * (n - 1) / 365.0
            self.assertLess(abs(weather_source._equation_of_time_min(gamma)), 20.0)


class SolarElevationAzimuthTest(SimpleTestCase):
    """Lot L — weather_source._elevation_azimuth (cœur géométrique, sans notion de
    date/heure). Oracle : identités algébriques dérivées à la main à des angles
    horaires remarquables (voir docstring de chaque test), jamais un second appel à
    la fonction testée."""

    databases = []

    def test_solar_noon_azimuth_and_elevation_hand_derived(self):
        # A l'angle horaire 0 (midi solaire vrai), la composante Est du vecteur
        # soleil est nulle par construction (-cos(decl)*sin(0)=0) : l'azimuth ne
        # peut valoir que 0 ou 180° selon le signe de (declinaison-latitude), et
        # l'elevation vaut exactement 90-|latitude-declinaison| — deux identités
        # dérivées à la main (voir weather_source._elevation_azimuth, pas une
        # resolution numérique).
        cases = [
            (48.8566, 10.0),   # Paris, decl < lat -> soleil au sud
            (10.0, 20.0),      # decl > lat -> soleil au nord
            (-33.8688, -10.0),  # hémisphère sud, decl > lat -> soleil au nord
        ]
        for lat, decl in cases:
            azimuth, elevation = weather_source._elevation_azimuth(lat, decl, 0.0)
            expected_elevation = 90.0 - abs(lat - decl)
            expected_azimuth = 180.0 if decl < lat else 0.0
            self.assertAlmostEqual(elevation, expected_elevation, places=6)
            self.assertAlmostEqual(azimuth, expected_azimuth, places=6)

    def test_equinox_sunrise_sunset_due_east_west(self):
        # A declinaison nulle (equinoxe), le lever (HA=-90) et le coucher (HA=+90)
        # se produisent exactement a l'horizon plein est / plein ouest, a N'IMPORTE
        # QUELLE latitude — fait astronomique elementaire independant de toute
        # formule de position solaire.
        for lat in (-60.0, -23.5, 0.0, 23.5, 60.0, 80.0):
            az_rise, el_rise = weather_source._elevation_azimuth(lat, 0.0, -90.0)
            az_set, el_set = weather_source._elevation_azimuth(lat, 0.0, 90.0)
            self.assertAlmostEqual(el_rise, 0.0, places=6)
            self.assertAlmostEqual(el_set, 0.0, places=6)
            self.assertAlmostEqual(az_rise, 90.0, places=6)
            self.assertAlmostEqual(az_set, 270.0, places=6)

    def test_time_symmetry_around_solar_noon(self):
        # L'elevation ne depend de H qu'au travers de cos(H) (fonction paire) :
        # elevation(H) == elevation(-H) exactement. L'azimuth, lui, doit etre le
        # miroir : azimuth(-H) == 360 - azimuth(H). Ces deux identites decoulent de
        # la structure algebrique de _elevation_azimuth (Est est impaire en H, Nord
        # est paire), independamment des valeurs numeriques choisies ici.
        cases = [(48.8566, 15.0, 37.0), (-10.0, -5.0, 82.0), (60.0, -20.0, 150.0)]
        for lat, decl, ha in cases:
            az_pos, el_pos = weather_source._elevation_azimuth(lat, decl, ha)
            az_neg, el_neg = weather_source._elevation_azimuth(lat, decl, -ha)
            self.assertAlmostEqual(el_pos, el_neg, places=9)
            self.assertAlmostEqual(_angle_diff_deg(az_neg, 360.0 - az_pos), 0.0, places=6)


class LocalAzimuthRotationTest(SimpleTestCase):
    """Lot L, point d'attention critique du to_do.md — weather_source.to_local_azimuth
    doit appliquer EXACTEMENT la même rotation que geodata._rotate_xy (même
    convention Building.georef_north_offset_deg), pour que météo et géométrie restent
    dans le même repère. Oracle : géométrie vectorielle dérivée à la main, pas un
    second appel à _rotate_xy (qui pourrait partager le même bug de signe)."""

    databases = []

    def test_zero_offset_is_identity(self):
        for az in (0.0, 45.0, 90.0, 180.0, 270.0, 359.9):
            self.assertAlmostEqual(
                _angle_diff_deg(weather_source.to_local_azimuth(az, 0.0), az), 0.0, places=6,
            )

    def test_ninety_degree_offset_rotates_south_to_local_east_axis(self):
        # Si l'axe +Y local pointe le Est reel (offset=90), alors l'axe +X local
        # (90° horaire apres +Y, geometry.py) pointe le Sud reel : un soleil reel
        # plein sud (azimuth reel 180°) doit donc avoir un azimuth LOCAL de 90°.
        self.assertAlmostEqual(weather_source.to_local_azimuth(180.0, 90.0), 90.0, places=6)

    def test_ninety_degree_offset_rotates_north_to_local_negative_x(self):
        # Meme configuration : le Nord reel (azimuth reel 0°) devient l'axe -X
        # local, soit un azimuth local de 270°.
        self.assertAlmostEqual(weather_source.to_local_azimuth(0.0, 90.0), 270.0, places=6)


class WeatherSeriesAssemblyTest(SimpleTestCase):
    """Lot L — weather_source._assemble_weather_series, partie pure (pas de réseau :
    prend directement une réponse Open-Meteo Archive synthétique, même format que la
    vraie API vérifiée en réel — voir project_bilan_thermique.md)."""

    databases = []

    def test_assembles_series_with_correct_shape_and_clamping(self):
        data = {
            'hourly': {
                'time': ['2023-06-21T10:00', '2023-06-21T11:00', '2023-06-21T12:00'],
                'temperature_2m': [20.0, 500.0, -500.0],           # hors bornes exprès
                'direct_normal_irradiance': [600.0, 5000.0, -10.0],  # hors bornes exprès
                'diffuse_radiation': [100.0, 5000.0, -10.0],         # hors bornes exprès
            },
        }
        series, n_missing = weather_source._assemble_weather_series(48.8566, 2.3522, data)
        self.assertEqual(len(series), 3)
        self.assertEqual(n_missing, 0)
        self.assertEqual(series[1]['t_ext'], weather_source.T_EXT_MAX)
        self.assertEqual(series[2]['t_ext'], weather_source.T_EXT_MIN)
        self.assertEqual(series[1]['e_dir'], weather_source.E_DIR_MAX)
        self.assertEqual(series[2]['e_dir'], 0.0)
        self.assertEqual(series[1]['e_dif'], weather_source.E_DIF_MAX)
        self.assertEqual(series[2]['e_dif'], 0.0)
        for point in series:
            self.assertTrue(0.0 <= point['sun_azimuth'] < 360.0)
            self.assertTrue(-90.0 <= point['sun_elevation'] <= 90.0)

    def test_skips_hours_with_missing_data(self):
        data = {
            'hourly': {
                'time': ['2023-06-21T10:00', '2023-06-21T11:00'],
                'temperature_2m': [20.0, None],
                'direct_normal_irradiance': [600.0, 500.0],
                'diffuse_radiation': [100.0, 90.0],
            },
        }
        series, n_missing = weather_source._assemble_weather_series(48.8566, 2.3522, data)
        self.assertEqual(len(series), 1)
        self.assertEqual(n_missing, 1)

    def test_no_hourly_data_raises(self):
        with self.assertRaises(weather_source.WeatherSourceError):
            weather_source._assemble_weather_series(48.8566, 2.3522, {'hourly': {'time': []}})

    def test_series_points_validate_against_public_serializer(self):
        # Integration avec le contrat public de l'app : chaque point assemblé doit
        # etre accepté par BuildingWeatherPointSerializer (le meme serializer que
        # celui utilisé au moment du calcul), pas seulement "dans les bornes"
        # vérifiées à la main ci-dessus.
        data = {
            'hourly': {
                'time': [f'2023-06-21T{h:02d}:00' for h in range(24)],
                'temperature_2m': [15.0 + h for h in range(24)],
                'direct_normal_irradiance': [0.0 if h < 6 or h > 20 else 500.0 for h in range(24)],
                'diffuse_radiation': [0.0 if h < 6 or h > 20 else 80.0 for h in range(24)],
            },
        }
        series, _ = weather_source._assemble_weather_series(48.8566, 2.3522, data, north_offset_deg=37.0)
        for point in series:
            s = serializers.BuildingWeatherPointSerializer(data=point)
            self.assertTrue(s.is_valid(), s.errors)


class PvgisTimestampTest(SimpleTestCase):
    """Lot S — weather_source._parse_pvgis_timestamp ('YYYYMMDD:HHMM', format PVGIS,
    différent du format ISO d'Open-Meteo)."""

    databases = []

    def test_parses_midnight(self):
        dt = weather_source._parse_pvgis_timestamp('20050101:0000')
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2005, 1, 1, 0, 0))

    def test_parses_non_midnight_time(self):
        dt = weather_source._parse_pvgis_timestamp('20081215:1430')
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2008, 12, 15, 14, 30))


class TmySeriesAssemblyTest(SimpleTestCase):
    """Lot S — weather_source._assemble_tmy_series, partie pure (pas de réseau :
    prend directement une réponse PVGIS TMY synthétique, même format que la vraie
    API vérifiée en réel — voir project_bilan_thermique.md). Gb(n)/Gd(h) sont les
    mêmes grandeurs physiques que direct_normal_irradiance/diffuse_radiation
    d'Open-Meteo (irradiance directe normale au rayon / diffuse horizontale), donc
    les mêmes tests de clamping/heures manquantes que WeatherSeriesAssemblyTest
    s'appliquent, juste avec le format brut PVGIS en entrée."""

    databases = []

    def test_assembles_tmy_series_with_correct_shape_and_clamping(self):
        data = {
            'outputs': {
                'tmy_hourly': [
                    {'time(UTC)': '20050621:1000', 'T2m': 20.0, 'Gb(n)': 600.0, 'Gd(h)': 100.0},
                    {'time(UTC)': '20060215:1200', 'T2m': 500.0, 'Gb(n)': 5000.0, 'Gd(h)': 5000.0},
                    {'time(UTC)': '20081201:0000', 'T2m': -500.0, 'Gb(n)': -10.0, 'Gd(h)': -10.0},
                ],
            },
        }
        series, n_missing = weather_source._assemble_tmy_series(48.8566, 2.3522, data)
        self.assertEqual(len(series), 3)
        self.assertEqual(n_missing, 0)
        self.assertEqual(series[1]['t_ext'], weather_source.T_EXT_MAX)
        self.assertEqual(series[2]['t_ext'], weather_source.T_EXT_MIN)
        self.assertEqual(series[1]['e_dir'], weather_source.E_DIR_MAX)
        self.assertEqual(series[2]['e_dir'], 0.0)
        for point in series:
            self.assertTrue(0.0 <= point['sun_azimuth'] < 360.0)
            self.assertTrue(-90.0 <= point['sun_elevation'] <= 90.0)

    def test_skips_hours_with_missing_pvgis_data(self):
        data = {
            'outputs': {
                'tmy_hourly': [
                    {'time(UTC)': '20050621:1000', 'T2m': 20.0, 'Gb(n)': 600.0, 'Gd(h)': 100.0},
                    {'time(UTC)': '20050621:1100', 'T2m': None, 'Gb(n)': 600.0, 'Gd(h)': 100.0},
                ],
            },
        }
        series, n_missing = weather_source._assemble_tmy_series(48.8566, 2.3522, data)
        self.assertEqual(len(series), 1)
        self.assertEqual(n_missing, 1)

    def test_no_hourly_data_raises(self):
        with self.assertRaises(weather_source.WeatherSourceError):
            weather_source._assemble_tmy_series(48.8566, 2.3522, {'outputs': {'tmy_hourly': []}})

    def test_series_points_validate_against_public_serializer(self):
        data = {
            'outputs': {
                'tmy_hourly': [
                    {'time(UTC)': f'20050621:{h:02d}00', 'T2m': 15.0 + h,
                     'Gb(n)': 0.0 if h < 6 or h > 20 else 500.0, 'Gd(h)': 0.0 if h < 6 or h > 20 else 80.0}
                    for h in range(24)
                ],
            },
        }
        series, _ = weather_source._assemble_tmy_series(48.8566, 2.3522, data, north_offset_deg=37.0)
        for point in series:
            s = serializers.BuildingWeatherPointSerializer(data=point)
            self.assertTrue(s.is_valid(), s.errors)


class TmyFallbackTest(SimpleTestCase):
    """Lot S — weather_source.build_tmy_or_fallback_series doit replier sur
    Open-Meteo Archive quand PVGIS échoue (zone non couverte ou autre erreur), et
    retourner explicitement quelle source a produit le résultat (to_do.md, étape 3 :
    ne jamais laisser la source ambiguë). build_tmy_series/build_weather_series
    mockées : ce test vérifie le BRANCHEMENT, pas le réseau (déjà couvert par les
    tests d'assemblage ci-dessus + vérification en réel, voir mémoire projet)."""

    databases = []

    def test_uses_pvgis_when_available(self):
        fake_series = [{'t_ext': 10.0}]
        with mock.patch.object(weather_source, 'build_tmy_series', return_value=(fake_series, 0)) as m_tmy, \
                mock.patch.object(weather_source, 'build_weather_series') as m_archive:
            series, n_missing, source, warning = weather_source.build_tmy_or_fallback_series(
                48.8566, 2.3522, '2023-01-01', '2023-01-02',
            )
        self.assertEqual(series, fake_series)
        self.assertEqual(n_missing, 0)
        self.assertEqual(source, 'pvgis-tmy')
        self.assertIsNone(warning)
        m_tmy.assert_called_once()
        m_archive.assert_not_called()

    def test_falls_back_to_archive_when_pvgis_unavailable(self):
        fake_series = [{'t_ext': 5.0}]
        with mock.patch.object(
            weather_source, 'build_tmy_series',
            side_effect=weather_source.WeatherSourceError("PVGIS TMY : Location over the sea."),
        ), mock.patch.object(weather_source, 'build_weather_series', return_value=(fake_series, 0)) as m_archive:
            series, n_missing, source, warning = weather_source.build_tmy_or_fallback_series(
                0.0, -160.0, '2023-01-01', '2023-01-02', north_offset_deg=15.0,
                utc_offset_seconds=-11 * 3600,
            )
        self.assertEqual(series, fake_series)
        self.assertEqual(source, 'open-meteo-archive')
        self.assertIsNotNone(warning)
        self.assertIn('over the sea', warning)
        # Assertion volontairement stricte sur la signature complète : le repli
        # doit transmettre TOUS les paramètres, pas seulement ceux qu'il
        # connaissait au Lot S. C'est ce qui a fait remonter, au Lot AB4, qu'un
        # nouveau paramètre devait bien traverser ce chemin-là aussi.
        m_archive.assert_called_once_with(
            0.0, -160.0, '2023-01-01', '2023-01-02',
            north_offset_deg=15.0, utc_offset_seconds=-11 * 3600,
        )


class WeatherHourIndexTest(SimpleTestCase):
    """Lot AB1 — `hour_index` porté par chaque point météo.

    weather_source SAUTE les heures dont une donnée manque (décision du Lot L :
    ne rien inventer). Tout ce qui indexait le temps par la POSITION dans la
    liste — planning de ventilation/apports/volets (Lots Q et J) côté serveur,
    calendrier d'occupation (Lot V) côté client — dérivait donc définitivement
    d'autant d'heures qu'il en manquait, sans aucun signe (`n_missing`
    n'apparaît que dans le message du Job).
    """

    databases = []

    @staticmethod
    def _archive_payload(times, drop_temp_at=()):
        n = len(times)
        return {'hourly': {
            'time': times,
            'temperature_2m': [None if i in drop_temp_at else 10.0 for i in range(n)],
            'direct_normal_irradiance': [0.0] * n,
            'diffuse_radiation': [0.0] * n,
            'wind_speed_10m': [3.0] * n,
        }}

    def test_hour_index_matches_position_when_nothing_missing(self):
        times = [f'2024-03-05T{h:02d}:00' for h in range(24)]
        series, n_missing = weather_source._assemble_weather_series(48.85, 2.35, self._archive_payload(times))
        self.assertEqual(n_missing, 0)
        self.assertEqual([p['hour_index'] for p in series], list(range(24)))

    def test_hour_index_survives_missing_hours(self):
        """LE cas du bug : 3 heures sautées au milieu. Les points suivants
        doivent garder leur heure RÉELLE, pas leur rang dans la liste."""
        times = [f'2024-03-05T{h:02d}:00' for h in range(24)]
        series, n_missing = weather_source._assemble_weather_series(
            48.85, 2.35, self._archive_payload(times, drop_temp_at={8, 9, 10}),
        )
        self.assertEqual(n_missing, 3)
        self.assertEqual(len(series), 21)
        # Position 8 dans la liste = 11 h réelles (8, 9, 10 sautées).
        self.assertEqual(series[8]['hour_index'], 11)
        self.assertEqual(series[-1]['hour_index'], 23)
        # Le bug d'origine : l'ancien calcul (heure_debut=0 + position) aurait
        # donné 20 pour la dernière heure au lieu de 23 — 3 h de décalage.
        self.assertNotEqual(series[-1]['hour_index'], len(series) - 1)

    def test_hour_index_spans_days(self):
        times = [f'2024-03-{d:02d}T{h:02d}:00' for d in (5, 6, 7) for h in range(24)]
        series, _ = weather_source._assemble_weather_series(48.85, 2.35, self._archive_payload(times))
        self.assertEqual(series[0]['hour_index'], 0)
        self.assertEqual(series[24]['hour_index'], 24)     # jour 1, minuit
        self.assertEqual(series[47]['hour_index'], 47)     # jour 1, 23 h
        self.assertEqual(series[-1]['hour_index'], 71)     # jour 2, 23 h

    def test_hour_index_survives_a_whole_missing_day(self):
        """Une JOURNÉE entière absente : le décompte des jours doit rester juste
        (sinon week-ends et vacances du calendrier d'occupation glissent d'un
        jour) — c'est pourquoi hour_index est dérivé de la DATE et non d'un
        compteur incrémenté ligne à ligne."""
        times = [f'2024-03-{d:02d}T{h:02d}:00' for d in (5, 6, 7) for h in range(24)]
        series, _ = weather_source._assemble_weather_series(
            48.85, 2.35, self._archive_payload(times, drop_temp_at=set(range(24, 48))),
        )
        self.assertEqual(len(series), 48)
        self.assertEqual(series[24]['hour_index'], 48)  # 7 mars 00 h, pas 24
        self.assertEqual(series[24]['hour_index'] // 24, 2)

    def test_tmy_hour_index_ignores_source_years(self):
        """Une TMY assemble des mois issus d'ANNÉES DIFFÉRENTES : compter par
        différence de dates sauterait des années entières d'un mois à l'autre.
        Ici, 31 déc 2011 puis 1er jan 2007 doivent rester deux jours
        CONSÉCUTIFS de l'année type."""
        rows = (
            [{'time(UTC)': f'20111231:{h:02d}00', 'T2m': 5.0, 'Gb(n)': 0.0, 'Gd(h)': 0.0, 'WS10m': 2.0}
             for h in range(24)]
            + [{'time(UTC)': f'20070101:{h:02d}00', 'T2m': 4.0, 'Gb(n)': 0.0, 'Gd(h)': 0.0, 'WS10m': 2.0}
               for h in range(24)]
        )
        series, _ = weather_source._assemble_tmy_series(48.85, 2.35, {'outputs': {'tmy_hourly': rows}})
        self.assertEqual(series[0]['hour_index'], 0)
        self.assertEqual(series[23]['hour_index'], 23)
        self.assertEqual(series[24]['hour_index'], 24)
        self.assertEqual(series[-1]['hour_index'], 47)


class LocalTimeOffsetTest(SimpleTestCase):
    """Lot AB4 — `hour_index` en heure LOCALE, pour qu'un profil d'occupation
    « 7 h - 19 h » veuille dire 7 h - 19 h à l'horloge de l'utilisateur.

    La météo est demandée en UTC (indispensable à la position solaire, qui est
    un instant physique) ; seul l'étiquetage horaire est décalé. Décalage FIXE,
    sans changement d'heure : vérifié en réel que Open-Meteo renvoie le même
    `utc_offset_seconds` en janvier et en juillet, et une année continue de
    8784 heures — arbitrage tranché avec l'utilisateur (to_do.md, Lot AB4).
    """

    databases = []

    @staticmethod
    def _payload(times):
        n = len(times)
        return {'hourly': {
            'time': times, 'temperature_2m': [10.0] * n,
            'direct_normal_irradiance': [0.0] * n, 'diffuse_radiation': [0.0] * n,
            'wind_speed_10m': [3.0] * n,
        }}

    def test_offset_shifts_hour_index(self):
        times = [f'2024-01-10T{h:02d}:00' for h in range(24)]
        series, _ = weather_source._assemble_weather_series(
            48.85, 2.35, self._payload(times), utc_offset_seconds=3600,
        )
        # 00:00 UTC = 01:00 locale le même jour -> hour_index part à 1.
        self.assertEqual(series[0]['hour_index'], 1)
        # 23:00 UTC = 00:00 locale le LENDEMAIN -> jour 1, heure 0.
        self.assertEqual(series[-1]['hour_index'], 24)
        self.assertEqual(series[-1]['hour_index'] % 24, 0)

    def test_zero_offset_is_the_previous_behaviour(self):
        times = [f'2024-01-10T{h:02d}:00' for h in range(24)]
        series, _ = weather_source._assemble_weather_series(48.85, 2.35, self._payload(times))
        self.assertEqual([p['hour_index'] for p in series], list(range(24)))

    def test_negative_offset_never_produces_a_negative_index(self):
        """Fuseau très à l'ouest : la première heure locale tombe la veille. Le
        jour 0 est celui du PREMIER point, donc hour_index reste positif — sans
        quoi le serializer (min_value=0) rejetterait la série."""
        times = [f'2024-01-10T{h:02d}:00' for h in range(24)]
        series, _ = weather_source._assemble_weather_series(
            48.85, 2.35, self._payload(times), utc_offset_seconds=-8 * 3600,
        )
        self.assertTrue(all(p['hour_index'] >= 0 for p in series))
        self.assertEqual(series[0]['hour_index'], 16)  # 00:00 UTC -> 16:00 la veille

    def test_solar_position_is_untouched_by_the_offset(self):
        """Le point clé : décaler l'étiquette horaire ne doit RIEN changer à la
        position du soleil, qui est un instant physique. Sans quoi on
        corrigerait un confort d'usage en cassant la physique."""
        times = [f'2024-06-21T{h:02d}:00' for h in range(24)]
        utc, local = (weather_source._assemble_weather_series(
            48.85, 2.35, self._payload(times), utc_offset_seconds=off,
        )[0] for off in (0, 2 * 3600))
        for a, b in zip(utc, local):
            self.assertAlmostEqual(a['sun_elevation'], b['sun_elevation'], places=12)
            self.assertAlmostEqual(a['sun_azimuth'], b['sun_azimuth'], places=12)

    def test_full_year_stays_within_serializer_bounds(self):
        """Un décalage positif reporte les dernières heures sur un jour de plus :
        la borne haute du serializer doit l'accepter (8807 et non 8783)."""
        times = [f'2024-{m:02d}-{d:02d}T{h:02d}:00'
                 for m, d in ((1, 1), (12, 31)) for h in range(24)]
        series, _ = weather_source._assemble_weather_series(
            48.85, 2.35, self._payload(times), utc_offset_seconds=2 * 3600,
        )
        field = serializers.BuildingWeatherPointSerializer().fields['hour_index']
        biggest = max(p['hour_index'] for p in series)
        self.assertLessEqual(biggest, field.max_value)

    def test_tmy_offset_applies_too(self):
        rows = [{'time(UTC)': f'20110101:{h:02d}00', 'T2m': 5.0, 'Gb(n)': 0.0,
                 'Gd(h)': 0.0, 'WS10m': 2.0} for h in range(24)]
        series, _ = weather_source._assemble_tmy_series(
            48.85, 2.35, {'outputs': {'tmy_hourly': rows}}, utc_offset_seconds=3600,
        )
        self.assertEqual(series[0]['hour_index'], 1)
        self.assertEqual(series[-1]['hour_index'], 24)

    def test_fetch_request_accepts_and_defaults_the_offset(self):
        base = {'lat': 48.85, 'lon': 2.35, 'start_date': '2024-01-01', 'end_date': '2024-01-02'}
        s = serializers.WeatherFetchRequestSerializer(data=base)
        self.assertTrue(s.is_valid(), s.errors)
        self.assertIsNone(s.validated_data['utc_offset_h'])  # None = détection auto

        s = serializers.WeatherFetchRequestSerializer(data={**base, 'utc_offset_h': 5.5})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data['utc_offset_h'], 5.5)

        s = serializers.WeatherFetchRequestSerializer(data={**base, 'utc_offset_h': 20})
        self.assertFalse(s.is_valid())
