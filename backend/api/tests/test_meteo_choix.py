"""Lot 13 (refonte) — météo choisie : stations Météo-France, TRACC, imports, périodes, comparaison."""

from datetime import date
from unittest import mock

from django.test import SimpleTestCase, TestCase

from .. import calc_service, facts, weather_service as ws
from ..models import Building, WeatherDataset
from ..testing import client_for, patch_jwks, synthetic_year, without_auto_environment


# Lot 30 : environnement relevé « à la demande » pour ce module (voir api.testing.without_auto_environment).
_NO_AUTO_ENV = without_auto_environment()


def setUpModule():
    _NO_AUTO_ENV.start()


def tearDownModule():
    _NO_AUTO_ENV.stop()


MF_HEADER = ['NUM_POSTE', 'NOM_USUEL', 'LAT', 'LON', 'ALTI', 'AAAAMMJJHH'] + ['X'] * 4 + ['FF'] + ['X'] * 5 + ['T'] + \
            ['X'] * 3 + ['GLO', 'DIR', 'DIF']


def mf_lines(station='37261001', days=3, radiation=True, gap=None):
    out = [';'.join(MF_HEADER) + '\n']
    for h in range(days * 24):
        if gap and gap[0] <= h < gap[1]:
            continue
        stamp = f'202301{1 + h // 24:02d}{h % 24:02d}'
        row = [station, 'TOURS', '47.4445', '0.7272', '108', stamp] + [''] * 4 + ['3.5'] + [''] * 5 + [f'{5 + (h % 24) / 3:.1f}'] + \
              [''] * 3 + ([f'{max(0, 120 - abs(h % 24 - 12) * 20)}', '', ''] if radiation else ['', '', ''])
        out.append(';'.join(row) + '\n')
    out.append(';'.join(['37999001', 'AUTRE', '47.0', '0.5', '90', '2023010100'] + [''] * 4 + ['1'] + [''] * 5 + ['2'] + [''] * 6) + '\n')
    return out


class StationParsingTest(SimpleTestCase):
    def test_station_list(self):
        st = ws.parse_mf_stations(iter(mf_lines()))
        self.assertEqual(st['37261001']['name'], 'TOURS')
        self.assertTrue(st['37261001']['has_radiation'])

    def test_series_with_global_radiation_and_gap_filling(self):
        rows, meta = ws.parse_mf_series(iter(mf_lines(gap=(30, 32))), '37261001', date(2023, 1, 1), date(2023, 1, 3))
        series, q = ws.series_from_station_rows(rows, meta, date(2023, 1, 1), date(2023, 1, 3))
        self.assertEqual(len(series), 72)
        self.assertEqual(q['filled_hours'], 2)
        self.assertIn('station (global, décomposé Erbs)', q['radiation'])
        noon = series[12]
        self.assertGreater(noon['e_dir'] + noon['e_dif'], 0)
        self.assertEqual([p['hour_index'] for p in series], list(range(72)))

    def test_long_gap_left_missing(self):
        rows, meta = ws.parse_mf_series(iter(mf_lines(gap=(10, 20))), '37261001', date(2023, 1, 1), date(2023, 1, 3))
        series, q = ws.series_from_station_rows(rows, meta, date(2023, 1, 1), date(2023, 1, 3))
        self.assertEqual(q['missing_hours'], 10)

    def test_periods_cover_years(self):
        self.assertEqual(ws.mf_periods(2019, 2021), ['2010-2019', 'previous-2020-2024'])

    def test_erbs_bounds(self):
        dni, dhi = ws.erbs_split(600, 45, 172)
        self.assertGreater(dni, 0)
        self.assertLess(dhi, 600)
        self.assertEqual(ws.erbs_split(0, -5, 1), (0.0, 0.0))


class TraccAndImportTest(SimpleTestCase):
    def test_tracc_rows_to_series(self):
        rows = [{'mois': 7, 'jour': 1, 'heure': h, 't2m': 30 + h % 5, 'rdn': 500 if 8 < h < 18 else 0,
                 'rdi': 100 if 6 < h < 20 else 0, 'ff': 2} for h in range(24)]
        s = ws.tracc_series_from_rows(rows, 'TOURS')
        self.assertEqual(len(s), 24)
        self.assertEqual(s[0]['hour_index'], (31 + 28 + 31 + 30 + 31 + 30) * 24)
        self.assertGreater(max(p['sun_elevation'] for p in s), 50)

    def test_nearest_tracc_station(self):
        self.assertEqual(ws.nearest_tracc_station(47.39, 0.69), 'TOURS')
        self.assertEqual(ws.nearest_tracc_station(43.3, 5.4), 'MARIGNANE')

    def test_epw_parsing(self):
        head = 'LOCATION,Tours,-,FRA,src,123,47.44,0.73,1.0,108\n' + 'X\n' * 7
        body = ''.join(f'2023,1,{1 + h // 24},{h % 24 + 1},60,x,{3 + h % 7},0,0,0,0,0,0,0,{200 if 9 < h % 24 < 16 else 0},{50 if 8 < h % 24 < 17 else 0},0,0,0,0,0,{2.5}\n'
                       for h in range(48))
        series, lat, lon, place = ws.parse_epw(head + body)
        self.assertEqual((len(series), lat, place), (48, 47.44, 'Tours'))
        with self.assertRaises(ws.WeatherError):
            ws.parse_epw('pas un epw')

    def test_csv_parsing_with_ghi(self):
        txt = 'date;t_ext;ghi;vent\n' + ''.join(f'2024-01-01T{h:02d}:00:00Z;{4 + h / 4:.1f};{max(0, 300 - abs(h - 12) * 50)};2\n' for h in range(24))
        s = ws.parse_csv_weather(txt, 47.39, 0.69)
        self.assertEqual(len(s), 24)
        with self.assertRaises(ws.WeatherError):
            ws.parse_csv_weather('date;x\n2024-01-01;1\n', 47.39, 0.69)

    def test_stats_dju(self):
        cold = [{'t_ext': 8.0, 'sun_elevation': -5, 'e_dir': 0, 'e_dif': 0, 'hour_index': h} for h in range(48)]
        self.assertEqual(ws.stats(cold)['dju_18'], 20.0)


class DatasetsTest(TestCase):
    def test_save_dedupes_by_series(self):
        s = synthetic_year(days=3)
        a = ws.save('import', 'A', s)
        b = ws.save('import', 'B', s)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(WeatherDataset.objects.count(), 1)

    def test_from_station_streams_and_saves(self):
        with mock.patch.object(ws, '_mf_lines', side_effect=lambda dep, period: iter(mf_lines(days=3))) as lines:
            ds = ws.from_station({'id': '37261001', 'department': '37', 'name': 'TOURS'}, date(2023, 1, 1), date(2023, 1, 3))
        self.assertEqual(lines.call_args.args, ('37', 'previous-2020-2024'))
        self.assertEqual((ds.kind, len(ds.series)), ('station', 72))
        self.assertIn('station', ds.label.lower())

    def test_period_slicing_tmy_by_month_day(self):
        ds = ws.save('tmy', 'tmy', synthetic_year())
        part = ws.slice_period(ds.series, '2030-06-01', '2030-08-31', ds)
        self.assertEqual(len(part), 92 * 24)
        self.assertEqual(part[0]['hour_index'], 0)

    def test_building_default_weather_used_by_calc(self):
        b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69)
        facts.record(b, 'identite.surface_m2', 100.0, 'infere')
        hot = ws.save('tracc', 'TRACC +4', synthetic_year(t_mean=15.5))
        b.weather_dataset = hot
        b.save()
        run, _ = calc_service.run_level(b, 'N1', uncertainty=False)
        self.assertEqual(run.weather['dataset_id'], hot.pk)

    def test_from_tracc_fetches_and_caches(self):
        rows = [{'mois': 1 + (h // 24) // 31 % 12, 'jour': 1 + (h // 24) % 28, 'heure': h % 24, 't2m': 10, 'rdn': 0, 'rdi': 0, 'ff': 1}
                for h in range(8760)]
        resp = mock.Mock(status_code=200, json=lambda: {'results': rows, 'next': None})
        resp.raise_for_status = lambda: None
        with mock.patch('requests.get', return_value=resp) as get:
            ds = ws.from_tracc(47.39, 0.69, '+2.7')
            ws.from_tracc(47.39, 0.69, '+2.7')
        self.assertEqual(get.call_count, 1)
        self.assertEqual(ds.meta['station'], 'TOURS')
        self.assertIn('qs', get.call_args.kwargs['params'])


@patch_jwks()
class MeteoApiTest(TestCase):
    def setUp(self):
        self.b = Building.objects.create(name='b', georef_lat=47.39, georef_lon=0.69)
        for k, v in (('identite.surface_m2', 120.0), ('mur.u', 1.2), ('identite.usage', 'maison')):
            facts.record(self.b, k, v, 'infere')
        self.tmy = ws.save('tmy', 'Année type', synthetic_year(), lat=47.39, lon=0.69)
        self.real = ws.save('reel', 'Hiver froid', synthetic_year(t_mean=9.0), lat=47.39, lon=0.69)
        self.future = ws.save('tracc', 'TRACC +4', synthetic_year(t_mean=15.0, t_amp=9.0), lat=47.44, lon=0.73)

    def test_same_building_three_weathers_three_results(self):
        """Recette du lot : même bâtiment, 3 météos, 3 résultats comparés."""
        r = client_for().post(f'/api/batiments/{self.b.pk}/comparer-meteo/',
                              {'jeux': [self.tmy.pk, self.real.pk, self.future.pk]}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()['rows']
        heats = [x['indicators']['heating_kwh'] for x in rows]
        self.assertEqual(len(set(round(h) for h in heats)), 3)
        self.assertGreater(heats[1], heats[0])  # hiver plus froid
        self.assertLess(heats[2], heats[0])     # climat futur plus chaud
        self.assertGreater(rows[1]['weather_stats']['dju_18'], rows[0]['weather_stats']['dju_18'])

    def test_period_comparison(self):
        r = client_for().post(f'/api/batiments/{self.b.pk}/comparer-meteo/',
                              {'jeux': [self.tmy.pk], 'start': '2023-12-01', 'end': '2023-12-31'}, format='json')
        self.assertEqual(r.json()['rows'][0]['weather_stats']['n_hours'], 31 * 24)

    def test_list_select_and_launch(self):
        lst = client_for().get(f'/api/meteo/jeux/?batiment={self.b.pk}').json()
        self.assertEqual(len(lst['results']), 3)
        self.assertNotIn('series', lst['results'][0])
        self.assertEqual(client_for().put(f'/api/batiments/{self.b.pk}/meteo/', {'dataset_id': self.real.pk},
                                          format='json').json()['selected'], self.real.pk)
        with mock.patch('api.tasks.build_weather_dataset.delay') as delay:
            r = client_for().post('/api/meteo/jeux/', {'type': 'tracc', 'batiment': self.b.pk, 'scenario': '+4-chaleur'}, format='json')
            bad = client_for().post('/api/meteo/jeux/', {'type': 'reel', 'batiment': self.b.pk, 'start': '2024-01-01',
                                                         'end': '2026-01-01'}, format='json')
        self.assertEqual(r.status_code, 202)
        self.assertEqual(bad.status_code, 400)
        delay.assert_called_once()

    def test_import_csv(self):
        txt = 'date;t_ext;dni;dhi\n' + ''.join(f'2024-03-01T{h:02d}:00:00Z;8;0;0\n' for h in range(24))
        r = client_for().post('/api/meteo/importer/', {'format': 'csv', 'content': txt, 'batiment': self.b.pk}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['stats']['n_hours'], 24)
