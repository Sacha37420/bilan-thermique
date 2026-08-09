from celery import shared_task
from django.utils import timezone

from .models import Job, Building, Environment, ParoiModel
from . import shadow
from . import building_solver
from . import geodata
from . import geometry
from . import weather_source
from . import elevation


@shared_task(bind=True)
def precompute_shadows(self, job_id: int, building_id: int):
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])

    try:
        job.set_state(status=Job.RUNNING, progress=0, message="Préparation de la géométrie…")
        building = Building.objects.get(pk=building_id)
        environment_envelope = building.environment.envelope if building.environment_id else None

        def progress_cb(done, total):
            # Grille de visibilité solaire : 0-79 % ; facteur de vue du ciel : 80-98 %.
            pct = int(1 + done * 79 / total)
            job.set_state(progress=pct, message=f"Test de visibilité solaire… {done}/{total} positions")

        result = shadow.compute_visibility_grid(
            building.envelope, environment_envelope, progress_cb=progress_cb,
        )

        job.set_state(progress=80, message="Facteur de vue du ciel (occlusion réelle)…")
        result['sky_view_factor'] = shadow.compute_sky_view_factors(building.envelope, environment_envelope)

        building.sun_visibility = result
        building.sun_visibility_stale = False
        building.save(update_fields=['sun_visibility', 'sun_visibility_stale'])

        job.result = {
            'n_triangles': len(building.envelope['triangles']),
            'n_azimuths': len(result['azimuths_deg']),
            'n_elevations': len(result['elevations_deg']),
        }
        job.save(update_fields=['result'])
        job.set_state(status=Job.DONE, progress=100, message="Précalcul d'ombrage terminé.")
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=str(exc))


@shared_task(bind=True)
def run_building_calcul(self, job_id: int, building_id: int, calcul_payload: dict):
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])

    try:
        job.set_state(status=Job.RUNNING, progress=0, message="Assemblage du système…")
        building = Building.objects.get(pk=building_id)
        triangles = building.envelope.get('triangles', [])
        paroi_ids = {t['paroi_model_id'] for t in triangles}
        paroi_models = list(ParoiModel.objects.filter(pk__in=paroi_ids))
        paroi_layers = {p.pk: p.layers for p in paroi_models}
        # Cadre de fenêtre (Lot I) : dict séparé plutôt que d'étendre paroi_layers
        # (qui reste {pid: layers} — convention utilisée telle quelle par les
        # tests existants), seulement les modèles où les deux champs sont
        # renseignés (voir ParoiModel.frame_u/frame_fraction).
        paroi_frame_by_id = {
            p.pk: (p.frame_u, p.frame_fraction)
            for p in paroi_models if p.frame_u is not None and p.frame_fraction is not None
        }
        sun_visibility = building.sun_visibility if building.sun_visibility.get('per_triangle') else None
        environment_envelope = building.environment.envelope if building.environment_id else None

        # DB write throttlée (~1 % du run, jamais moins de 5s d'intervalle) —
        # un job de plusieurs milliers d'heures ne doit pas faire une écriture
        # Job par heure simulée.
        import time
        last_write = [0.0]

        def progress_cb(done, total):
            pct = int(1 + done * 98 / total)
            now = time.monotonic()
            if pct == 100 or now - last_write[0] > 2.0:
                last_write[0] = now
                job.set_state(progress=pct, message=f"Résolution heure par heure… {done}/{total}")

        result = building_solver.run_building_simulation(
            building.envelope, paroi_layers, sun_visibility, calcul_payload,
            environment_envelope=environment_envelope, progress_cb=progress_cb,
            paroi_frame_by_id=paroi_frame_by_id,
        )

        job.result = {
            'hours': result['hours'],
            't_air_mean': result['t_air_mean'],
            'heating_kwh': result['heating_kwh'],
            'cooling_kwh': result['cooling_kwh'],
            'flux_positive_kwh': result['flux_positive_kwh'],
            'flux_negative_kwh': result['flux_negative_kwh'],
            # Bilan par poste au nœud d'air (Lot AB2) — None en mode 'imposed',
            # où la ligne du nœud d'air est écrasée par Dirichlet.
            'balance': result['balance'],
            't_air': result['t_air'],
            'envelope_flux_w': result['envelope_flux_w'],
            'final_exterior_surface_temp': result['final_exterior_surface_temp'],
            'final_interior_surface_temp': result['final_interior_surface_temp'],
        }
        job.save(update_fields=['result'])
        job.set_state(
            status=Job.DONE, progress=100,
            message=f"Calcul terminé — {result['hours']}h, "
                    f"chauffage {result['heating_kwh']:.0f} kWh, clim {result['cooling_kwh']:.0f} kWh.",
        )
    except building_solver.BuildingSimulationError as exc:
        job.set_state(status=Job.ERROR, message=str(exc))
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=str(exc))


@shared_task(bind=True)
def generate_environment(self, job_id, params):
    """Génère un maillage d'obstacles et le renvoie via `job.result` pour
    relecture avant enregistrement (page Environnement).

    Lot AD — générateur UNIQUE du lab. `building_id` (optionnel) apporte tout ce
    que faisait l'ancienne variante attachée à un bâtiment : alignement sur son
    repère local, mise à l'écart du bâtiment étudié lui-même et rognage des
    obstacles qui l'empiètent, altitude de référence. Sans lui, comportement
    exploratoire d'origine — non aligné, sans filtrage.
    """
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])

    stage_messages = {
        'bbox': "Préparation de la zone…",
        'ign': "Interrogation de l'IGN (BD TOPO)…",
        'osm': "Repli sur OpenStreetMap…",
        'extrude': "Extrusion des bâtiments…",
        'done': "Assemblage du maillage…",
    }

    def progress_cb(stage, pct):
        job.set_state(status=Job.RUNNING, progress=pct, message=stage_messages.get(stage, ''))

    try:
        job.set_state(status=Job.RUNNING, progress=0, message=stage_messages['bbox'])

        building = None
        if params.get('building_id'):
            building = Building.objects.filter(pk=params['building_id']).first()

        north_offset_deg = building.georef_north_offset_deg if building else 0.0
        ground_z_ref = building.georef_ground_z if building else None
        self_envelope = building.envelope if building else None

        def elevation_lookup(points):
            return elevation.fetch_elevations(points)[0]

        result = geodata.generate_environment_mesh(
            params['lat'], params['lon'], params['radius_m'], progress_cb=progress_cb,
            north_offset_deg=north_offset_deg, ground_z_ref=ground_z_ref,
            self_envelope=self_envelope, elevation_lookup=elevation_lookup,
        )

        self_footprint = geodata.envelope_footprint_polygon(self_envelope)

        n_vegetation = 0
        if params.get('include_vegetation'):
            try:
                job.set_state(progress=62, message="Végétation (IGN / OpenStreetMap)…")
                veg = geodata.generate_vegetation_mesh(
                    params['lat'], params['lon'], params['radius_m'],
                    north_offset_deg=north_offset_deg, ground_z_ref=ground_z_ref,
                    self_footprint=self_footprint, elevation_lookup=elevation_lookup,
                )
                result['warnings'].extend(veg['warnings'])
                result['stats'].update(veg['stats'])
                n_vegetation = veg['stats']['vegetation_used']
                if (len(result['vertices']) + len(veg['vertices']) > geometry.MAX_VERTICES
                        or len(result['triangles']) + len(veg['triangles']) > geometry.MAX_TRIANGLES):
                    result['warnings'].append("Végétation abandonnée : limite de maillage atteinte.")
                    n_vegetation = 0
                else:
                    offset = len(result['vertices'])
                    result['vertices'].extend(veg['vertices'])
                    result['triangles'].extend(
                        {'v': [i + offset for i in t['v']], 'k': t['k'], 'obj': t['obj']}
                        for t in veg['triangles']
                    )
            except geodata.GeodataError as exc:
                result['warnings'].append(f"Végétation non chargée ({exc}).")

        n_terrain = 0
        if params.get('terrain_spacing_m'):
            try:
                job.set_state(progress=75, message="Altitude du terrain…")
                terrain_mesh, terrain_source, n_terrain = elevation.build_terrain_for_building(
                    params['lat'], params['lon'], params['radius_m'], params['terrain_spacing_m'],
                    north_offset_deg=north_offset_deg, ground_z_ref=ground_z_ref,
                    footprint_polygon=self_footprint,
                )
                if (len(result['vertices']) + len(terrain_mesh['vertices']) > geometry.MAX_VERTICES
                        or len(result['triangles']) + len(terrain_mesh['triangles']) > geometry.MAX_TRIANGLES):
                    result['warnings'].append(
                        "Terrain abandonné : le maillage atteindrait la limite. Augmentez le pas."
                    )
                    n_terrain = 0
                else:
                    offset = len(result['vertices'])
                    result['vertices'].extend(terrain_mesh['vertices'])
                    result['triangles'].extend(
                        {'v': [i + offset for i in t['v']]} for t in terrain_mesh['triangles']
                    )
                    result['stats']['terrain_source'] = terrain_source
                    result['stats']['terrain_points'] = n_terrain
            except elevation.ElevationError as exc:
                result['warnings'].append(f"Terrain non chargé ({exc}) — obstacles bâtis conservés.")
                n_terrain = 0

        job.result = result
        job.save(update_fields=['result'])
        stats = result['stats']
        job.set_state(
            status=Job.DONE, progress=100,
            message=f"{stats['buildings_used']} bâtiment(s), {len(result['triangles'])} triangles "
                    f"(IGN : {stats['buildings_ign']}, OSM : {stats['buildings_osm']})."
                    + (f" {stats['buildings_self']} écarté(s) : bâtiment étudié."
                       if stats.get('buildings_self') else "")
                    + (f" {stats['buildings_clipped']} rogné(s)."
                       if stats.get('buildings_clipped') else "")
                    + (f" Végétation : {n_vegetation}." if n_vegetation else "")
                    + (f" Terrain : {n_terrain} points." if n_terrain else ""),
        )
    except geodata.GeodataError as exc:
        job.set_state(status=Job.ERROR, message=str(exc))
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=str(exc))


@shared_task(bind=True)
def fetch_weather(self, job_id, params):
    """Lot L ('archive') + Lot S ('tmy') — récupère une série météo horaire réelle
    (Open-Meteo Archive, ou PVGIS TMY avec repli automatique sur Open-Meteo Archive
    hors couverture PVGIS) + position solaire calculée, voir api.weather_source.
    Ne persiste rien sur un Building — job.result porte directement la série, le
    frontend la récupère et la met dans son propre état local (calcul-3d.component)."""
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])

    source_label = {'archive': "Open-Meteo Archive", 'tmy': "PVGIS TMY"}.get(params.get('source', 'archive'))
    try:
        job.set_state(status=Job.RUNNING, progress=10, message=f"Interrogation de {source_label}…")
        north_offset_deg = params.get('north_offset_deg', 0.0)

        # Lot AB4 : décalage horaire local appliqué à `hour_index` (planning et
        # calendrier d'occupation), jamais à la position solaire. Détecté si
        # l'appelant ne l'impose pas ; repli silencieux sur UTC si la sonde
        # échoue — mieux vaut un décalage nul qu'une récupération météo annulée.
        utc_offset_h = params.get('utc_offset_h')
        offset_warning = None
        if utc_offset_h is None:
            detected = weather_source.fetch_utc_offset_seconds(params['lat'], params['lon'])
            if detected is None:
                utc_offset_h = 0.0
                offset_warning = ("Fuseau horaire non détecté — heures laissées en UTC. "
                                  "Renseignez le décalage à la main si un planning horaire est utilisé.")
            else:
                utc_offset_h = detected / 3600.0
        utc_offset_seconds = int(round(utc_offset_h * 3600.0))

        if params.get('source') == 'tmy':
            series, n_missing, source, warning = weather_source.build_tmy_or_fallback_series(
                params['lat'], params['lon'], str(params['start_date']), str(params['end_date']),
                north_offset_deg=north_offset_deg, utc_offset_seconds=utc_offset_seconds,
            )
        else:
            series, n_missing = weather_source.build_weather_series(
                params['lat'], params['lon'], str(params['start_date']), str(params['end_date']),
                north_offset_deg=north_offset_deg, utc_offset_seconds=utc_offset_seconds,
            )
            source, warning = 'open-meteo-archive', None

        if offset_warning:
            warning = f"{warning} {offset_warning}" if warning else offset_warning

        job.result = {
            'weather': series, 'n_hours': len(series), 'n_missing': n_missing,
            'source': source, 'warning': warning, 'utc_offset_h': utc_offset_h,
        }
        job.save(update_fields=['result'])

        source_display = {'pvgis-tmy': "PVGIS TMY", 'open-meteo-archive': "Open-Meteo Archive"}[source]
        message = f"{len(series)} heure(s) récupérée(s) ({source_display})."
        if warning:
            message += f" {warning}"
        if n_missing:
            message += f" {n_missing} heure(s) ignorée(s), donnée manquante."
        job.set_state(status=Job.DONE, progress=100, message=message)
    except weather_source.WeatherSourceError as exc:
        job.set_state(status=Job.ERROR, message=str(exc))
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=str(exc))
