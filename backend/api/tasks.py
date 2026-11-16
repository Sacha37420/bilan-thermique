from celery import shared_task
from django.utils import timezone

from .models import Job, Building, Environment, ParoiModel
from . import shadow
from . import building_solver
from . import geodata
from . import weather_source
from . import net
from . import calc_runs


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
        # Refonte (Lot 14) : protections fixes en mode géométrique = obstacles supplémentaires.
        from . import shading_service
        environment_envelope = shading_service.merge_envelopes(
            environment_envelope, shading_service.geometric_occluders(building)) or environment_envelope

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
        job.set_state(status=Job.ERROR, message=net.public_message(exc))


@shared_task(bind=True)
def run_building_calcul(self, job_id: int, building_id: int, calcul_payload: dict):
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])

    import time as _time
    started = _time.monotonic()
    calc_runs.mark_running(job_id)
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
        from . import shading_service
        environment_envelope = shading_service.merge_envelopes(
            environment_envelope, shading_service.geometric_occluders(building)) or environment_envelope

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
            'protrusions': result.get('protrusions'),
            't_air': result['t_air'],
            't_op': result.get('t_op'),
            't_mrt': result.get('t_mrt'),
            'envelope_flux_w': result['envelope_flux_w'],
            'final_exterior_surface_temp': result['final_exterior_surface_temp'],
            'final_interior_surface_temp': result['final_interior_surface_temp'],
        }
        job.save(update_fields=['result'])
        # Lot 3 (refonte) : le résultat reste consultable après avoir quitté la page.
        calc_runs.finish(job_id, job.result, started, surface_m2=building.surface_ref_m2)
        job.set_state(
            status=Job.DONE, progress=100,
            message=f"Calcul terminé — {result['hours']}h, "
                    f"chauffage {result['heating_kwh']:.0f} kWh, clim {result['cooling_kwh']:.0f} kWh.",
        )
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=net.public_message(exc))
        calc_runs.fail(job_id, net.public_message(exc))


@shared_task(bind=True)
def run_n2_fiche(self, job_id):
    """N2 depuis la fiche (Lot 23) : le `CalcRun` a été créé avec le job ; ses entrées sont reconstruites
    ici depuis la fiche (calc_service.run_n2)."""
    from . import calc_service
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])
    run = calc_runs.for_job(job_id)
    if run is None:
        job.set_state(status=Job.ERROR, message='Calcul introuvable.')
        return
    import time as _time
    last = [0.0]

    def say(pct, msg):
        now = _time.monotonic()
        if pct >= 99 or now - last[0] > 2.0:
            last[0] = now
            job.set_state(progress=min(pct, 99), message=msg)
    try:
        calc_runs.mark_running(job_id)
        job.set_state(status=Job.RUNNING, progress=1, message='Préparation des entrées depuis la fiche…')
        run = calc_service.run_n2(run, say)
        job.result = {'calc_run_id': run.pk, 'heating_kwh': run.results['heating_kwh']}
        job.save(update_fields=['result'])
        job.set_state(status=Job.DONE, progress=100,
                      message=f"N2 terminé — chauffage {run.results['heating_kwh']:.0f} kWh.")
    except Exception as exc:  # noqa: BLE001
        job.set_state(status=Job.ERROR, message=net.public_message(exc))
        calc_runs.fail(job_id, net.public_message(exc))


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
        'mns': "Nuage LiDAR absent : modèle numérique de surface (MNS) de l'IGN…",
        'bdtopo': "Emprises des bâtiments (BD TOPO)…",
        'rasters': "Terrain et hauteurs (MNT, toits, végétation)…",
        'registration': "Recalage des emprises BD TOPO sur le LiDAR…",
        'buildings': "Reconstruction des bâtiments et toitures…",
        'lidar-only': "Bâtiments absents de la BD TOPO…",
        'vegetation': "Arbres et massifs…",
        'terrain': "Maillage du terrain…",
        'ortho': "Orthophotos : albédo du sol et couleur des toits…",
        'cosia': "CoSIA : essences des arbres (conifères, feuillus)…",
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
        if summary['source'] in ('lidar', 'mns_lidar', 'mns_correl'):
            n_buildings = sum(stats.get(k, 0) for k in (
                'buildings_confirmed', 'buildings_lidar_only', 'buildings_masked', 'buildings_uncovered'))
            detail = f"{n_buildings} bâtiment(s), {stats.get('trees', 0)} arbre(s)"
        else:
            detail = f"{stats.get('buildings', 0)} bâtiment(s), {stats.get('trees', 0)} élément(s) de végétation"
        job.set_state(
            status=Job.DONE, progress=100,
            message=f"« {env.name} » : {detail}, {summary['n_triangles']} triangles"
                    + {'lidar': " (LiDAR HD).", 'mns_lidar': " (MNS LiDAR HD).",
                       'mns_correl': " (MNS Correl)."}.get(summary['source'], " (repli BD TOPO / OSM)."),
        )
    except (geodata.GeodataError, environment_service.EnvironmentServiceError) as exc:
        job.set_state(status=Job.ERROR, message=net.public_message(exc))
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=net.public_message(exc))


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
        job.set_state(status=Job.ERROR, message=net.public_message(exc))
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=net.public_message(exc))


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


@shared_task(bind=True)
def analyse_facades(self, job_id, building_id):
    """Lot AK — photos de rue Panoramax → texture redressée et baies détectées
    de chaque façade exposée (api.facades). Ne modifie PAS l'enveloppe :
    l'insertion des vitrages est une étape séparée, après vérification."""
    from . import facades, geodata, observed_env
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])
    try:
        job.set_state(status=Job.RUNNING, progress=1, message="Préparation…")
        building = Building.objects.get(pk=building_id)
        env = building.environment
        if building.georef_lat is None:
            raise facades.FacadeError("Bâtiment non géoréférencé.")
        frame = observed_env.LocalFrame(building.georef_lat, building.georef_lon,
                                        building.georef_north_offset_deg or 0.0)
        base = (building.facades or {}).get('base') or {
            'vertices': building.envelope['vertices'],
            'triangles': [{k: v for k, v in t.items() if k not in ('area', 'normal', 'tilt_deg', 'azimuth_deg')}
                          for t in building.envelope['triangles']],
        }
        own = geodata.envelope_footprint_polygon({'vertices': base['vertices'], 'triangles': base['triangles']})
        objects = list(env.scene_objects) if env is not None else []
        if own is not None:
            objects.append(observed_env.make_object(10 ** 9, 'building', 'etudie', 'bâtiment étudié',
                                                    base['vertices'], base['triangles'], own))

        def progress(msg, pct):
            job.set_state(progress=pct, message=msg)

        ground_z = env.georef_ground_z if env is not None and env.georef_ground_z is not None \
            else building.georef_ground_z
        result = facades.analyse(building.pk, base['vertices'], base['triangles'], frame, objects, progress,
                                 ground_z=ground_z, own_footprint=own)
        building.facades = {**result, 'base': base}
        building.save(update_fields=['facades', 'updated_at'])
        try:   # Lot 26 (refonte) : taux de vitrage vus sur photo → propositions à valider (onglet Documents)
            from . import expert_facts
            expert_facts.facade_proposals(building, job.owner_sub)
        except Exception:  # noqa: BLE001 — l'analyse reste valable sans propositions
            pass
        n = sum(1 for f in result['facades'].values() if f['status'] in ('analysee', 'partielle'))
        n_open = sum(len(f['openings']) for f in result['facades'].values())
        job.result = {'n_facades': len(result['facades']), 'n_analysees': n, 'n_openings': n_open}
        job.save(update_fields=['result'])
        job.set_state(status=Job.DONE, progress=100,
                      message=f"{n}/{len(result['facades'])} façade(s) vues sur photo, {n_open} baie(s).")
    except Exception as exc:  # noqa: BLE001
        job.set_state(status=Job.ERROR, message=net.public_message(exc))


@shared_task(bind=True)
def recompose_facades(self, job_id, building_id):
    """Lot AL — recompose les textures multi-vues perdues du cache (vues et
    recalages enregistrés ; ni recalage ni détection refaits)."""
    from . import facades, geodata, observed_env
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id
    job.save(update_fields=['celery_task_id'])
    try:
        job.set_state(status=Job.RUNNING, progress=1, message="Préparation…")
        building = Building.objects.get(pk=building_id)
        env = building.environment
        data = building.facades or {}
        base = data.get('base') or building.envelope
        frame = observed_env.LocalFrame(building.georef_lat, building.georef_lon,
                                        building.georef_north_offset_deg or 0.0)
        own = geodata.envelope_footprint_polygon({'vertices': base['vertices'], 'triangles': base['triangles']})
        objects = list(env.scene_objects) if env is not None else []
        if own is not None:
            objects.append(observed_env.make_object(10 ** 9, 'building', 'etudie', 'bâtiment étudié',
                                                    base['vertices'], base['triangles'], own))
        ground_z = env.georef_ground_z if env is not None and env.georef_ground_z is not None \
            else building.georef_ground_z
        n = facades.recompose_textures(building.pk, base['vertices'], base['triangles'], data.get('facades', {}),
                                       frame, objects, ground_z=ground_z, own_footprint=own,
                                       progress_cb=lambda m, p: job.set_state(progress=p, message=m))
        job.set_state(status=Job.DONE, progress=100, message=f"{n} texture(s) recomposée(s).")
    except Exception as exc:  # noqa: BLE001
        job.set_state(status=Job.ERROR, message=net.public_message(exc))


@shared_task(bind=True)
def init_building(self, job_id, building_id):
    """Refonte (Lot 9) : chaîne automatique A1→A6 d'un bâtiment (réseau, léger en calcul)."""
    from . import pipeline
    job = Job.objects.get(pk=job_id)
    job.celery_task_id = self.request.id or ''
    job.save(update_fields=['celery_task_id'])
    try:
        building = Building.objects.get(pk=building_id)
        Building.objects.filter(pk=building_id).update(pipeline_status='en_cours')
        job.set_state(status=Job.RUNNING, progress=1, message="Initialisation…")
        report = pipeline.initialize(building, progress=lambda m, p: job.set_state(progress=p, message=m))
        after_init(building, job)
        job.result = {'building_id': building.pk, 'pipeline_status': building.pipeline_status,
                      'steps': report['steps']}
        job.save(update_fields=['result'])
        job.set_state(status=Job.DONE, progress=100,
                      message='Fiche initiale prête.' if building.pipeline_status == 'complet'
                      else 'Fiche initiale incomplète (voir les étapes).')
    except Exception as exc:
        Building.objects.filter(pk=building_id).update(pipeline_status='incomplet')
        job.set_state(status=Job.ERROR, message=net.public_message(exc))


def after_init(building, job):
    """Suite de la chaîne (A7→A9) : météo de l'année type du site, calculs N0 puis N1."""
    from . import calc_service, pipeline
    if building.georef_lat is None:
        return None
    # Bâtiment de parc : environnement observé (A6), météo par défaut (A7) et calculs automatiques,
    # selon les réglages du parc.
    a6 = pipeline.apply_environment(building, progress=lambda m, p: job.set_state(progress=p, message=m))
    if a6 is not None:
        pipe = dict(building.pipeline or {})
        pipe.setdefault('steps', {})['A6'] = a6
        building.pipeline = pipe
        building.save(update_fields=['pipeline'])
    a7 = pipeline.apply_default_weather(building, job.owner_sub)
    if a7 is not None:
        pipe = dict(building.pipeline or {})
        pipe.setdefault('steps', {})['A7'] = a7
        building.pipeline = pipe
        building.save(update_fields=['pipeline'])
    levels = pipeline.auto_levels(building)
    try:
        job.set_state(progress=96, message="Météo et calculs N0 / N1…")
        for lvl in levels:
            calc_service.run_level(building, lvl, owner_sub=job.owner_sub, job=None)
    except calc_service.CalcError as exc:
        pipe = dict(building.pipeline or {})
        pipe.setdefault('steps', {})['A8'] = {'ok': False, 'error': str(exc)[:300]}
        building.pipeline = pipe
        building.pipeline_status = 'incomplet'
        building.save(update_fields=['pipeline', 'pipeline_status'])
        return None
    pipe = dict(building.pipeline or {})
    pipe.setdefault('steps', {})['A8'] = {'ok': True, **({} if levels else {'skipped': True, 'message': pipeline.SKIPPED})}
    building.pipeline = pipe
    building.save(update_fields=['pipeline'])
    return None


@shared_task(bind=True, acks_late=True)
def process_parc_next(self, portfolio_id, owner_sub=''):
    """Refonte (Lot 11) : traite UN bâtiment du parc puis se replanifie (file `parc`, D13) — un job
    interactif en attente sur la file `celery` passe avant le bâtiment suivant."""
    from . import parc
    b = parc.next_building(portfolio_id)
    if b is None:
        return 'fini'
    parc.process_one(b, owner_sub=owner_sub)
    process_parc_next.apply_async(args=[portfolio_id, owner_sub], queue='parc')
    return b.pk


@shared_task(bind=True)
def build_weather_dataset(self, job_id, params):
    """Refonte (Lot 13) : construit un jeu météo (réseau) — année type, réanalyse, station, TRACC."""
    from datetime import date as _date
    from . import weather_service as ws
    job = Job.objects.get(pk=job_id)
    try:
        job.set_state(status=Job.RUNNING, progress=5, message='Récupération des données météo…')
        kind, lat, lon, owner = params['type'], params['lat'], params['lon'], job.owner_sub
        if kind == 'tmy':
            ds = ws.from_tmy(lat, lon, owner)
        elif kind == 'reel':
            ds = ws.from_reanalysis(lat, lon, _date.fromisoformat(params['start']), _date.fromisoformat(params['end']), owner)
        elif kind == 'station':
            job.set_state(progress=10, message='Lecture des fichiers horaires Météo-France (en flux)…')
            ds = ws.from_station(params['station'], _date.fromisoformat(params['start']), _date.fromisoformat(params['end']), owner)
        elif kind == 'tracc':
            ds = ws.from_tracc(lat, lon, params['scenario'], owner)
        else:
            raise ws.WeatherError('Type de météo inconnu.')
        job.result = {'dataset_id': ds.pk, 'label': ds.label}
        job.save(update_fields=['result'])
        job.set_state(status=Job.DONE, progress=100, message=f'« {ds.label} » prêt.')
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=net.public_message(exc, 'Météo indisponible.'))


@shared_task(bind=True)
def list_weather_stations(self, job_id, params):
    """Refonte (Lot 13) : stations Météo-France proches (première fois par département : lecture en flux)."""
    from . import weather_service as ws
    job = Job.objects.get(pk=job_id)
    try:
        job.set_state(status=Job.RUNNING, progress=10, message='Recherche des stations Météo-France…')
        stations = ws.stations_near(params['lat'], params['lon'], params.get('alt'))
        job.result = {'stations': stations}
        job.save(update_fields=['result'])
        job.set_state(status=Job.DONE, progress=100, message=f'{len(stations)} station(s) à moins de {ws.STATION_MAX_KM} km.')
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=net.public_message(exc, 'Stations indisponibles.'))


@shared_task(bind=True)
def calibrate_building(self, job_id, building_id, measurement_id, dataset_id=None):
    """Refonte (Lot 17) : calage sur factures (météo réelle de la période). Rend une proposition ;
    rien n'est écrit dans la fiche avant que la personne ne l'applique."""
    from datetime import date as _date
    from . import calibration, geometry_service, weather_service as ws
    from .models import Measurement, WeatherDataset
    job = Job.objects.get(pk=job_id)
    try:
        job.set_state(status=Job.RUNNING, progress=5, message='Météo réelle de la période facturée…')
        b = Building.objects.get(pk=building_id)
        m = Measurement.objects.get(pk=measurement_id, building=b)
        bills = m.values
        start, end = calibration.period_bounds(bills)
        chosen = b.weather_dataset if (not dataset_id and b.weather_dataset_id and b.weather_dataset.kind in
                                         ('reel', 'station', 'import') and b.weather_dataset.meta.get('start')) else None
        if chosen is not None and not (chosen.meta['start'] <= start.isoformat() and
                                       (chosen.meta.get('end') or '9999') >= end.isoformat()):
            chosen = None  # la météo retenue ne couvre pas la période facturée
        if dataset_id or chosen is not None:
            ds = chosen or WeatherDataset.objects.get(pk=dataset_id)
            weather = ws.slice_period(ds.series, start.isoformat(), end.isoformat(), ds)
            wlabel = ds.label
        else:
            ds = ws.from_reanalysis(b.georef_lat, b.georef_lon, start, end, job.owner_sub)
            weather, wlabel = ds.series, ds.label
        job.set_state(progress=25, message='Sensibilité (Morris) puis optimisation…')
        report = calibration.calibrate(b, bills, m.energy or 'gaz', weather, geom=geometry_service.effective_summary(b))
        report['weather'] = wlabel
        report['measurement_id'] = m.pk
        job.result = report
        job.save(update_fields=['result'])
        msg = ('Calage conforme (ASHRAE 14)' if report.get('ok') else 'Calage hors critères') if report.get('after') else report.get('reason', '')
        job.set_state(status=Job.DONE, progress=100, message=msg)
    except Exception as exc:
        job.set_state(status=Job.ERROR, message=net.public_message(exc, 'Calage impossible.'))


@shared_task(bind=True)
def extract_document(self, job_id, run_id):
    """Refonte (Lot 21) : lecture d'un document par l'IA, avec la clé de la personne qui l'a demandée
    (job.owner_sub == document.owner_sub, vérifié par la vue et revérifié ici)."""
    from . import documents
    from .ia import fournisseurs, registre
    from .models import AIExtractionRun, AiKeyVault
    job = Job.objects.get(pk=job_id)
    run = AIExtractionRun.objects.select_related('document__building').get(pk=run_id)
    doc = run.document
    try:
        if not (job.owner_sub and job.owner_sub == run.owner_sub == doc.owner_sub):
            raise ValueError('owner')
        job.set_state(status=Job.RUNNING, progress=10, message='Lecture du document par l’IA…')
        vault = AiKeyVault.objects.filter(owner_sub=job.owner_sub).first()
        key = getattr(vault, registre.FOURNISSEURS[run.provider]['champ'], '') if vault else ''
        if not key:
            raise documents.DocumentError('Aucune clé configurée pour ce fournisseur (page « Intelligence artificielle »).')
        rows, usage = documents.run_extraction(run.provider, key, run.model, doc.pages, run.pages_sent)
        del key
        props = documents.make_proposals(run, doc.building, rows, doc.pages)
        run.tokens_input, run.tokens_output = usage['input_tokens'], usage['output_tokens']
        run.status, run.message = 'DONE', f'{len(props)} proposition(s)'
        run.save(update_fields=['tokens_input', 'tokens_output', 'status', 'message'])
        job.result = {'run_id': run.pk, 'proposals': len(props)}
        job.save(update_fields=['result'])
        job.set_state(status=Job.DONE, progress=100, message=f'{len(props)} fait(s) proposé(s) : à valider.')
    except fournisseurs.ErreurFournisseur as exc:
        msg = exc.message or 'Document trop long pour ce modèle : envoyez moins de pages.'
        run.status, run.message = 'ERROR', msg[:500]
        run.save(update_fields=['status', 'message'])
        job.set_state(status=Job.ERROR, message=msg[:500])
    except Exception as exc:  # noqa: BLE001 — message fixe, jamais la clé ni le texte d'une exception réseau
        msg = str(exc) if isinstance(exc, documents.DocumentError) else 'Lecture impossible.'
        run.status, run.message = 'ERROR', msg[:500]
        run.save(update_fields=['status', 'message'])
        job.set_state(status=Job.ERROR, message=msg[:500])
