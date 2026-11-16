"""Lot 33 — parties de l'enveloppe hors du volume chauffé : un mur qui dépasse n'échange pas avec le nœud
intérieur (ses deux faces sont dehors), il reste une ailette qui conduit, stocke et reçoit le soleil."""

import numpy as np
from django.test import SimpleTestCase

from .. import building_solver, geometry, protrusions
from ..testing import box_envelope
from .helpers import _flat_wall_layer, _no_sun_weather_3d


def box(x0, y0, z0, x1, y1, z1, faces=('x0', 'x1', 'y0', 'y1', 'z0', 'z1'), group='mur'):
    """Pavé (normales sortantes), faces choisies ; rend (vertices, triangles)."""
    v = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]
    quads = {'z0': (0, 3, 2, 1), 'z1': (4, 5, 6, 7), 'y0': (0, 1, 5, 4), 'y1': (2, 3, 7, 6),
             'x0': (3, 0, 4, 7), 'x1': (1, 2, 6, 5)}
    tris = []
    for f in faces:
        a, b, c, d = quads[f]
        tris += [{'v': [a, b, c], 'group': f'{group}_{f}'}, {'v': [a, c, d], 'group': f'{group}_{f}'}]
    return v, tris


def merged(*parts):
    V, T = [], []
    for v, t in parts:
        off = len(V)
        V += v
        T += [{**x, 'v': [i + off for i in x['v']]} for x in t]
    return V, T


class DetectTest(SimpleTestCase):
    def test_closed_building_has_no_protrusion(self):
        v, t = box(0, 0, 0, 10, 8, 6)
        out, rep = protrusions.detect(v, t)
        self.assertEqual((out, rep['n']), ([], 0))
        ell = merged(box(0, 0, 0, 10, 8, 6, faces=('x0', 'y0', 'z0', 'z1', 'y1')),
                     box(10, 0, 0, 16, 8, 6, faces=('x1', 'y0', 'z0', 'z1', 'y1')))
        self.assertEqual(protrusions.detect(*ell)[0], [])           # deux volumes accolés sans cloison : un seul

    def test_eave_wedges_are_not_thin_parts(self):
        import shapely.geometry as sg
        from .. import geometry_service
        for roof, pitch in (('2pans', 15.0), ('4pans', 45.0)):
            env, _n = geometry_service.envelope_for(sg.box(0, 0, 12, 7), 5.0, roof, pitch)
            self.assertEqual(protrusions.detect(env['vertices'], env['triangles'])[0], [], roof)

    def test_wall_sticking_out_is_found(self):
        building = box(0, 0, 0, 10, 8, 6)
        fin = box(10, 3.9, 0, 13, 4.1, 6, faces=('x1', 'y0', 'y1', 'z1'), group='ailette')   # mur de 20 cm, 3 m
        v, t = merged(building, fin)
        out, rep = protrusions.detect(v, t)
        groups = {t[i]['group'] for i in out}
        self.assertEqual(groups, {'ailette_x1', 'ailette_y0', 'ailette_y1', 'ailette_z1'})
        self.assertAlmostEqual(rep['aire_m2'], 2 * 3 * 6 + 0.2 * 6 + 3 * 0.2, places=1)

    def test_other_convention_is_left_alone(self):
        # Murs épais en deux faces (pièce = vide entre deux coques) : tout serait « mince » — rien n'est reclassé.
        outer = box(0, 0, 0, 10, 8, 3)
        inner_v, inner_t = box(0.3, 0.3, 0.3, 9.7, 7.7, 2.7)
        inner_t = [{**x, 'v': list(reversed(x['v']))} for x in inner_t]   # normales vers la pièce
        out, rep = protrusions.detect(*merged(outer, (inner_v, inner_t)))
        self.assertEqual(out, [])
        self.assertIn('hors convention', rep['avertissement'])


class SolverTest(SimpleTestCase):
    def _envelope(self, with_fin):
        base = box_envelope(w=10.0, d=8.0, h=6.0, paroi_id=1)
        V, T = list(base['vertices']), [dict(t) for t in base['triangles']]
        if with_fin:
            fin_v, fin_t = box(10, 3.9, 0, 13, 4.1, 6, faces=('x1', 'y0', 'y1', 'z1'), group='ailette')
            off = len(V)
            V += fin_v
            T += [{'v': [i + off for i in x['v']], 'group': x['group'], 'paroi_model_id': 1,
                   'boundary': 'exterior_air'} for x in fin_t]
        return {'vertices': V, 'triangles': geometry.compute_envelope_geometry(V, T)}

    def test_fin_does_not_exchange_with_the_interior(self):
        layers = {1: [_flat_wall_layer(e=0.2, lam=1.0)]}
        payload = {'dx_max': 0.05, 'h_e': 25.0, 't_init': 20.0, 'weather': _no_sun_weather_3d([0.0] * 72),
                   'interior': {'mode': 'thermostat', 'h_i': 8.0, 'c_air_int': 1.2 * 1006 * 480,
                                't_min': 20.0, 't_max': 26.0}}
        alone = building_solver.run_building_simulation(self._envelope(False), layers, None, payload)
        env = self._envelope(True)
        fin = building_solver.run_building_simulation(env, layers, None, payload)
        self.assertEqual(fin['protrusions']['n'], 8)
        # Sans la détection, les deux faces de l'ailette (37 m²) étaient reliées à l'air chauffé : besoin gonflé.
        old = building_solver.run_building_simulation(env, layers, None, {**payload, 'detect_protrusions': False})
        self.assertAlmostEqual(fin['heating_kwh'], alone['heating_kwh'], delta=0.01 * alone['heating_kwh'])
        self.assertGreater(old['heating_kwh'], 1.08 * alone['heating_kwh'])   # +37 m² d'ailette sur 376 m²
