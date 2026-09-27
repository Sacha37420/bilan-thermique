from celery import shared_task
from django.utils import timezone

from .models import Job, Building, Environment, ParoiModel
from . import shadow
from . import building_solver
from . import geodata
from . import weather_source


def _environment_ground(environment):
    """(grille d'albédo du sol, identifiants des objets terrain actifs) d'un
    environnement observé ; (None, set()) sinon."""
    if environment is None:
        return None, set()
    albedo = (environment.generation or {}).get('ground_albedo')
    ids = {o['id'] for o in (environment.scene_objects or [])
           if o.get('kind') == 'terrain' and o.get('status') == 'active'}
    return albedo, ids


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

        # Lot AI : réflexion par le sol, si l'environnement porte un albédo relevé.
        albedo, ground_ids = _environment_ground(building.environment)
        if albedo:
            from . import observed_env
            job.set_state(progress=92, message="Réflexion par le sol (albédo relevé)…")
            result['ground_reflect_factor'] = shadow.compute_ground_reflection_factors(
                building.envelope, environment_envelope, observed_env.albedo_lookup(albedo),
                ground_obj_ids=ground_ids,
            )

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

        albedo, ground_ids = _environment_ground(building.environment)
        result = building_solver.run_building_simulation(
            building.envelope, paroi_layers, sun_visibility, calcul_payload,
            environment_envelope=environment_envelope, progress_cb=progress_cb,
            paroi_frame_by_id=paroi_frame_by_id,
            ground_albedo=albedo, ground_obj_ids=ground_ids,
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
    """Génère ET enregistre un environnement (Lot AH) : bâtiments, arbres et
    terrain reconstruits depuis le LiDAR HD croisé avec la BD TOPO, avec repli
    BD TOPO / OpenStreetMap hors couverture. Voir api.environment_service.

    `job.result` porte `environment_id` : l'environnement n'est plus renvoyé
    brut pour relecture puis réenvoyé par le navigateur (plusieurs Mo pour une
    zone réelle) — il est enregistré directement, puis se relit et s'édite
    objet par objet.

    `building_id` (optionnel) : repère du bâtiment de référence, qui est
    reconnu parmi les objets et marqué comme bâtiment étudié au lieu d'être un
    obstacle (successeur du filtrage du Lot X)."""
    from . import environment_service

    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])

    stage_messages = {
        'lidar-index': "Recherche des dalles LiDAR HD…",
        'lidar-read': "Lecture du nuage de points LiDAR HD…",
        'bdtopo': "Emprises des bâtiments (BD TOPO)…",
        'rasters': "Terrain et hauteurs (MNT, toits, végétation)…",
        'registration': "Recalage des emprises BD TOPO sur le LiDAR…",
        'buildings': "Reconstruction des bâtiments et toitures…",
        'lidar-only': "Bâtiments absents de la BD TOPO…",
        'vegetation': "Arbres et massifs…",
        'terrain': "Maillage du terrain…",
        'ortho': "Orthophotos : albédo du sol et couleur des toits…",
        'legacy-buildings': "Bâtiments (BD TOPO / OpenStreetMap)…",
        'legacy-vegetation': "Végétation (BD TOPO / OpenStreetMap)…",
        'legacy-terrain': "Altitude du terrain…",
        'save': "Enregistrement…",
        'done': "Assemblage…",
    }

    def progress_cb(stage, pct):
        job.set_state(status=Job.RUNNING, progress=min(int(pct), 99),
                      message=stage_messages.get(stage, stage))

    try:
        job.set_state(status=Job.RUNNING, progress=0, message="Préparation de la zone…")
        building = None
        if params.get('building_id'):
            building = Building.objects.filter(pk=params['building_id']).first()
        env, summary = environment_service.generate(params, building=building, progress_cb=progress_cb)

        job.result = summary
        job.save(update_fields=['result'])
        stats = summary['stats']
        if summary['source'] == 'lidar':
            n_buildings = sum(stats.get(k, 0) for k in (
                'buildings_confirmed', 'buildings_lidar_only', 'buildings_masked', 'buildings_uncovered'))
            detail = f"{n_buildings} bâtiment(s), {stats.get('trees', 0)} arbre(s)"
        else:
            detail = f"{stats.get('buildings', 0)} bâtiment(s), {stats.get('trees', 0)} élément(s) de végétation"
        job.set_state(
            status=Job.DONE, progress=100,
            message=f"« {env.name} » : {detail}, {summary['n_triangles']} triangles"
                    + (" (LiDAR HD)." if summary['source'] == 'lidar' else " (repli BD TOPO / OSM)."),
        )
    except (geodata.GeodataError, environment_service.EnvironmentServiceError) as exc:
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


from celery.signals import worker_ready  # noqa: E402


@worker_ready.connect
def _release_orphan_jobs(**_kwargs):
    """Au démarrage du worker, toute tâche encore « en attente » ou « en cours »
    en base est orpheline : le worker unique (--concurrency=1) vient de
    redémarrer, et la file Redis, sans volume, est recréée à chaque
    déploiement. Laissée telle quelle, elle bloquait indéfiniment tout nouveau
    précalcul d'ombrage et tout calcul (verrous « un seul à la fois » des vues,
    409) — constaté après un redéploiement pendant un précalcul (Lot AI)."""
    Job.objects.filter(status__in=[Job.PENDING, Job.RUNNING]).update(
        status=Job.ERROR, message="Interrompu : le worker a redémarré. Relancez l'opération.",
    )
