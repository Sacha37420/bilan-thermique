"""Géométrie corrigée d'un bâtiment (Lot 15, refonte — § B3).

La géométrie automatique (BD TOPO, `Building.pipeline`) reste la base ; une édition (`GeometryEdit`)
est une surcharge : emprise (polygone en mètres, repère local du bâtiment), hauteur à l'égout,
niveaux, forme et pente de toiture. À l'enregistrement :
1. validation côté serveur (polygone simple, surface, nombre de sommets, hauteurs) ;
2. résumé recalculé (emprise, périmètre, murs exposés par orientation, mitoyenneté et horizon avec
   les voisins gardés par la chaîne) → utilisé par N0/N1 ;
3. faits déclarés (emprise, hauteur, niveaux, mitoyenneté, toiture) ;
4. enveloppe N2 régénérée (toit plat, deux ou quatre pans) et **vérifiée** (maillage fermé, normales
   sortantes, aucun triangle dégénéré) — seulement si l'enveloppe actuelle n'a pas d'assignation de
   paroi faite à la main, ou si on le demande.
"""

import math

import numpy as np
import shapely.geometry
import trimesh

from . import facts, geometry
from .pipeline import geometry_from_footprints

MAX_VERTICES = 60


class GeometryEditError(ValueError):
    pass


def auto_footprint(building):
    p = building.pipeline or {}
    if p.get('footprint_xy'):
        return p['footprint_xy']
    env = building.envelope or {}
    if env.get('triangles'):
        from .geodata import envelope_footprint_polygon
        try:
            poly = envelope_footprint_polygon(env)
        except Exception:  # noqa: BLE001
            return None
        if poly is not None and not poly.is_empty:
            poly = poly if poly.geom_type == 'Polygon' else max(poly.geoms, key=lambda q: q.area)
            return [[round(x, 2), round(y, 2)] for x, y in list(poly.exterior.coords)[:-1]]
    return None


def active_edit(building, scenario=None):
    qs = building.geometry_edits.filter(retired_at__isnull=True, scenario=scenario)
    return qs.order_by('-created_at').first()


def effective_summary(building, scenario=None):
    """Résumé géométrique utilisé par N0/N1 : l'édition active, sinon l'automatique."""
    e = active_edit(building, scenario) or (active_edit(building) if scenario is not None else None)
    if e is not None:
        return e.summary
    return (building.pipeline or {}).get('geometry')


def _neighbours(building):
    return [(shapely.geometry.Polygon(n['xy']), n['h']) for n in (building.pipeline or {}).get('neighbours') or []
            if len(n.get('xy') or []) >= 3]


def validate(footprint, eave, levels, roof_type, pitch):
    if not isinstance(footprint, list) or not 3 <= len(footprint) <= MAX_VERTICES:
        raise GeometryEditError(f'Emprise : de 3 à {MAX_VERTICES} sommets.')
    try:
        pts = [(float(x), float(y)) for x, y in footprint]
    except (TypeError, ValueError):
        raise GeometryEditError('Emprise : coordonnées numériques attendues.') from None
    if any(abs(c) > 2000 for p in pts for c in p):
        raise GeometryEditError('Emprise : coordonnées hors de la zone du bâtiment.')
    poly = shapely.geometry.Polygon(pts)
    if not poly.is_valid or not poly.is_simple:
        raise GeometryEditError('Emprise : le contour se recoupe.')
    if poly.area < 5:
        raise GeometryEditError('Emprise : moins de 5 m².')
    if not 2.0 <= eave <= 300:
        raise GeometryEditError('Hauteur à l’égout : entre 2 et 300 m.')
    if not 1 <= levels <= 80:
        raise GeometryEditError('Niveaux : entre 1 et 80.')
    if roof_type not in ('terrasse', '2pans', '4pans'):
        raise GeometryEditError('Toiture : terrasse, 2pans ou 4pans.')
    if roof_type != 'terrasse' and not 5 <= pitch <= 70:
        raise GeometryEditError('Pente : entre 5 et 70°.')
    return shapely.geometry.polygon.orient(poly, sign=1.0)


def envelope_for(poly, eave, roof_type, pitch):
    """Enveloppe fermée : murs extrudés jusqu'à l'égout, toit plat, ou pans sur le rectangle orienté
    minimal quand l'emprise s'en approche (sinon toit plat, signalé)."""
    xy = list(poly.exterior.coords)[:-1]
    note = None
    if roof_type != 'terrasse':
        rect = poly.minimum_rotated_rectangle
        if poly.area / rect.area < 0.85:
            roof_type, note = 'terrasse', 'Emprise trop éloignée d’un rectangle : toiture traitée comme terrasse.'
        else:
            xy = list(shapely.geometry.polygon.orient(rect, sign=1.0).exterior.coords)[:-1]
    from .geodata import extrude_footprint_grouped
    vertices, triangles = extrude_footprint_grouped(xy, eave)
    if roof_type != 'terrasse':
        vertices, triangles = _pitched_roof(vertices, triangles, xy, eave, roof_type, pitch)
    triangles = geometry.compute_envelope_geometry(vertices, triangles)
    check_closed(vertices, triangles)
    return {'vertices': vertices, 'triangles': triangles}, note


def _pitched_roof(vertices, triangles, rect, eave, roof_type, pitch):
    """Remplace la toiture plate d'un parallélépipède rectangle par deux ou quatre pans."""
    tris = [t for t in triangles if t['group'] != 'toiture']
    V = [list(v) for v in vertices]
    p = [np.array(c, dtype=float) for c in rect]
    e0, e1 = p[1] - p[0], p[2] - p[1]
    if np.linalg.norm(e0) < np.linalg.norm(e1):  # faîtage parallèle au grand côté
        p = p[1:] + p[:1]
        e0, e1 = p[1] - p[0], p[2] - p[1]
    half = np.linalg.norm(e1) / 2.0
    rise = half * math.tan(math.radians(pitch))
    top = eave
    idx = {}
    for i, v in enumerate(V):
        if abs(v[2] - top) < 1e-6:
            idx[(round(v[0], 6), round(v[1], 6))] = i

    def vid(q):
        return idx[(round(q[0], 6), round(q[1], 6))]
    a, b, c, d = (vid(q) for q in p)
    mid0, mid1 = (p[0] + p[3]) / 2.0, (p[1] + p[2]) / 2.0
    if roof_type == '4pans':
        inset = e0 / np.linalg.norm(e0) * min(half, np.linalg.norm(e0) / 2.0 - 0.01)
        r0, r1 = mid0 + inset, mid1 - inset
    else:
        r0, r1 = mid0, mid1
    ri0, ri1 = len(V), len(V) + 1
    V += [[float(r0[0]), float(r0[1]), top + rise], [float(r1[0]), float(r1[1]), top + rise]]
    new = [([a, b, ri1], 'toiture'), ([a, ri1, ri0], 'toiture'), ([c, d, ri0], 'toiture'), ([c, ri0, ri1], 'toiture')]
    if roof_type == '4pans':
        new += [([d, a, ri0], 'toiture'), ([b, c, ri1], 'toiture')]
    else:  # pignons : murs triangulaires
        new += [([d, a, ri0], 'pignon_1'), ([b, c, ri1], 'pignon_2')]
    for v, g in new:
        tris.append({'v': v, 'group': g, 'boundary': 'exterior_air'})
    return V, tris


def check_closed(vertices, triangles):
    mesh = trimesh.Trimesh(vertices=np.asarray(vertices, dtype=float), faces=[t['v'] for t in triangles], process=False)
    if not mesh.is_watertight:
        raise GeometryEditError('Enveloppe non fermée.')
    if mesh.volume <= 0:
        raise GeometryEditError('Normales tournées vers l’intérieur.')
    if min(t['area'] for t in triangles) < 1e-6:
        raise GeometryEditError('Triangle dégénéré.')
    return float(mesh.volume)


def apply_edit(building, data, author_sub='', author_name='', regenerate_envelope=None):
    from .models import GeometryEdit
    footprint = data.get('footprint_xy') or auto_footprint(building)
    if not footprint:
        raise GeometryEditError('Aucune emprise connue : dessinez-la.')
    try:
        eave = float(data.get('eave_height_m'))
        levels = int(data.get('levels'))
        pitch = float(data.get('roof_pitch_deg') or 0.0)
    except (TypeError, ValueError):
        raise GeometryEditError('Hauteur, niveaux et pente : nombres attendus.') from None
    roof_type = data.get('roof_type') or 'terrasse'
    poly = validate(footprint, eave, levels, roof_type, pitch)
    summary = geometry_from_footprints(poly, _neighbours(building), eave)
    summary['footprint_xy'] = [[round(x, 2), round(y, 2)] for x, y in list(poly.exterior.coords)[:-1]]
    envelope, note = envelope_for(poly, eave, roof_type, pitch)
    if note:
        summary['note'] = note
    has_manual = any(t.get('paroi_model_id') is not None for t in (building.envelope or {}).get('triangles') or [])
    regen = (not has_manual) if regenerate_envelope is None else bool(regenerate_envelope)
    edit = GeometryEdit.objects.create(
        building=building, footprint_xy=summary['footprint_xy'], eave_height_m=eave, levels=levels, roof_type=roof_type,
        roof_pitch_deg=pitch, summary=summary, envelope_regenerated=regen, author_sub=author_sub, author_name=author_name)
    building.geometry_edits.filter(retired_at__isnull=True).exclude(pk=edit.pk).update(retired_at=edit.created_at)
    src = 'géométrie corrigée'
    for key, value in (('identite.emprise_m2', summary['emprise_m2']), ('identite.hauteur_m', round(eave, 2)),
                       ('identite.niveaux', levels), ('identite.mitoyennete', min(0.9, summary['mitoyennete'])),
                       ('toiture.type', roof_type), ('toiture.pente_deg', pitch if roof_type != 'terrasse' else 0.0)):
        facts.record(building, key, value, 'declare', source=src, author_sub=author_sub, author_name=author_name, refresh=False)
    if regen:
        building.envelope = envelope
        building.sun_visibility_stale = True
        building.save(update_fields=['envelope', 'sun_visibility_stale'])
    facts.refresh_scores(building)
    return edit


def retire_edit(building, by_sub=''):
    from django.utils import timezone
    e = active_edit(building)
    if e is None:
        return None
    e.retired_at = timezone.now()
    e.save(update_fields=['retired_at'])
    for f in building.facts.filter(source='géométrie corrigée', retired_at__isnull=True, superseded_by__isnull=True):
        facts.retire(f, by_sub)
    return e


def diff(building):
    auto = (building.pipeline or {}).get('geometry') or {}
    e = active_edit(building)
    if e is None:
        return None
    out = []
    for key, label, unit in (('emprise_m2', 'emprise', 'm²'), ('hauteur_m', 'hauteur', 'm'), ('mitoyennete', 'mitoyenneté', '')):
        a, b = auto.get(key), e.summary.get(key)
        if a is not None and b is not None and abs(a - b) > 1e-6:
            out.append({'label': label, 'auto': a, 'edit': b, 'unit': unit})
    return {'changes': out, 'author': e.author_name, 'at': e.created_at, 'roof_type': e.roof_type,
            'roof_pitch_deg': e.roof_pitch_deg, 'levels': e.levels, 'envelope_regenerated': e.envelope_regenerated,
            'note': e.summary.get('note')}
