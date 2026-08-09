from rest_framework import serializers
from .models import Department, UserRecord, ParoiModel, Building, Environment, Job
from . import building_solver, geometry


class LayerSerializer(serializers.Serializer):
    e = serializers.FloatField(min_value=0.0001, max_value=5.0)
    lam = serializers.FloatField(min_value=0.001, max_value=500.0)
    rho = serializers.FloatField(min_value=1.0, max_value=25000.0)
    c = serializers.FloatField(min_value=1.0, max_value=10000.0)
    tau = serializers.FloatField(min_value=0.0, max_value=1.0)
    r = serializers.FloatField(min_value=0.0, max_value=1.0)
    alpha = serializers.FloatField(min_value=0.0, max_value=1.0)

    def validate(self, data):
        total = data['tau'] + data['r'] + data['alpha']
        if abs(total - 1.0) > 0.01:
            raise serializers.ValidationError(
                f"tau + r + alpha doit valoir 1 (obtenu {total:.3f})."
            )
        return data


class ParoiModelSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(allow_blank=True, required=False, default='')
    layers = LayerSerializer(many=True, min_length=1, max_length=20)
    # Cadre de fenêtre (Lot I, optionnel) — voir ParoiModel.frame_u/frame_fraction.
    # frame_fraction plafonné à 0,95 (pas 1,0) : à 1,0 l'aire "vitrage" du
    # triangle tomberait exactement à zéro (building_solver.run_building_simulation
    # réduit l'aire du maillage à (1-frame_fraction)*aire), ce qui annulerait
    # entièrement son bloc dans la matrice globale (lignes nulles) — système
    # singulier, spla.splu échouerait. Un modèle 100% cadre ne modélise de toute
    # façon plus un vitrage.
    frame_u = serializers.FloatField(required=False, allow_null=True, default=None, min_value=0.1, max_value=20.0)
    frame_fraction = serializers.FloatField(required=False, allow_null=True, default=None, min_value=0.0, max_value=0.95)
    # Modèle de vitrage plutôt que de paroi opaque (Lot T) — voir ParoiModel.is_glazing.
    is_glazing = serializers.BooleanField(required=False, default=False)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def validate_name(self, value):
        qs = ParoiModel.objects.filter(name=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Un modèle de paroi porte déjà ce nom.")
        return value

    def validate(self, data):
        frame_u = data.get('frame_u')
        frame_fraction = data.get('frame_fraction')
        if (frame_u is None) != (frame_fraction is None):
            raise serializers.ValidationError(
                "frame_u et frame_fraction doivent être renseignés ensemble (ou ni l'un ni l'autre)."
            )
        return data

    def create(self, validated_data):
        return ParoiModel.objects.create(**validated_data)

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class TriangleInputSerializer(serializers.Serializer):
    v = serializers.ListField(child=serializers.IntegerField(min_value=0), min_length=3, max_length=3)
    group = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)
    paroi_model_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    # Lot K : un triangle 'ground' (plancher bas au contact du sol) échange avec
    # t_ground plutôt qu'avec t_ext — le sol a une température bien plus stable/
    # amortie que l'air extérieur. Défaut 'exterior_air' = comportement historique
    # (mur/toiture face à l'air extérieur), inchangé pour tout maillage existant.
    boundary = serializers.ChoiceField(choices=['exterior_air', 'ground'], required=False, default='exterior_air')
    # Lot J : dispositif d'occultation mobile (volet/store) éventuellement
    # installé sur ce triangle — indépendant de paroi_model_id (deux fenêtres
    # du même modèle de vitrage peuvent avoir des dispositifs différents, ou
    # aucun). Défaut None = comportement historique (aucun dispositif, jamais
    # affecté par un planning `volets_fermes`, voir building_solver.SHADING_PROFILES).
    shading_profile_id = serializers.ChoiceField(
        choices=list(building_solver.SHADING_PROFILES), required=False, allow_null=True, default=None,
    )


class EnvironmentTriangleInputSerializer(serializers.Serializer):
    v = serializers.ListField(child=serializers.IntegerField(min_value=0), min_length=3, max_length=3)


class EnvironmentSerializer(serializers.Serializer):
    """Maillage d'environnement (obstacles) — géométrie brute uniquement, pas
    d'assignation de paroi ni de champs calculés (voir api.shadow, seul
    consommateur : trimesh calcule ses propres normales de face)."""

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(allow_blank=True, required=False, default='')
    vertices = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=3, max_length=3),
        min_length=3, max_length=geometry.MAX_VERTICES,
        write_only=True, required=False, default=list,
    )
    triangles = EnvironmentTriangleInputSerializer(
        many=True, write_only=True, required=False, default=list, max_length=geometry.MAX_TRIANGLES,
    )
    envelope = serializers.JSONField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def validate_name(self, value):
        qs = Environment.objects.filter(name=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Un environnement porte déjà ce nom.")
        return value

    def _build_envelope(self, validated_data):
        if 'vertices' not in validated_data and 'triangles' not in validated_data:
            return None
        existing = (self.instance.envelope if self.instance else None) or {}
        vertices = validated_data.pop('vertices', None)
        if vertices is None:
            vertices = existing.get('vertices', [])
        triangles = validated_data.pop('triangles', None)
        if triangles is None:
            triangles = [{'v': t['v']} for t in existing.get('triangles', [])]
        else:
            triangles = [dict(t) for t in triangles]
        try:
            geometry.validate_indices(vertices, triangles)
        except geometry.GeometryError as exc:
            raise serializers.ValidationError({'triangles': str(exc)})
        return {'vertices': vertices, 'triangles': triangles}

    def create(self, validated_data):
        envelope = self._build_envelope(validated_data) or {'vertices': [], 'triangles': []}
        return Environment.objects.create(envelope=envelope, **validated_data)

    def update(self, instance, validated_data):
        envelope = self._build_envelope(validated_data)
        if envelope is not None:
            instance.envelope = envelope
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = ['id', 'kind', 'status', 'progress', 'message', 'params', 'result', 'created_at', 'updated_at']
        read_only_fields = fields


class BuildingSerializer(serializers.Serializer):
    """Bâtiment = enveloppe triangulaire + assignation d'un ParoiModel par triangle.

    vertices/triangles sont en écriture seule (entrée brute du maillage importé
    côté client) ; la lecture se fait via `envelope`, qui contient en plus les
    champs géométriques (area/normal/tilt_deg/azimuth_deg) toujours recalculés
    côté serveur — jamais pris tels quels depuis le client (voir api.geometry).
    """

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(allow_blank=True, required=False, default='')
    vertices = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=3, max_length=3),
        min_length=3, max_length=geometry.MAX_VERTICES,
        write_only=True, required=False, default=list,
    )
    triangles = TriangleInputSerializer(
        many=True, write_only=True, required=False, default=list, max_length=geometry.MAX_TRIANGLES,
    )
    envelope = serializers.JSONField(read_only=True)
    environment_id = serializers.PrimaryKeyRelatedField(
        source='environment', queryset=Environment.objects.all(), required=False, allow_null=True,
    )
    georef_lat = serializers.FloatField(required=False, allow_null=True, default=None,
                                         min_value=-90.0, max_value=90.0)
    georef_lon = serializers.FloatField(required=False, allow_null=True, default=None,
                                         min_value=-180.0, max_value=180.0)
    georef_north_offset_deg = serializers.FloatField(required=False, default=0.0)
    georef_ground_z = serializers.FloatField(required=False, allow_null=True, default=None)
    surface_ref_m2 = serializers.FloatField(required=False, allow_null=True, default=None, min_value=0.01)
    # Suggestion de ventilation (Lot T) — bornes identiques à
    # BuildingInteriorSerializer.debit_vent_m3h/eta_recup_vent, que ces champs
    # préremplissent côté Calcul 3D (voir Building.suggested_debit_vent_m3h).
    suggested_debit_vent_m3h = serializers.FloatField(required=False, allow_null=True, default=None,
                                                       min_value=0.0, max_value=100_000.0)
    suggested_eta_recup_vent = serializers.FloatField(required=False, allow_null=True, default=None,
                                                        min_value=0.0, max_value=0.95)
    sun_visibility_stale = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def validate_name(self, value):
        qs = Building.objects.filter(name=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Un bâtiment porte déjà ce nom.")
        return value

    def validate(self, data):
        triangles = data.get('triangles') or []
        ids = {t['paroi_model_id'] for t in triangles if t.get('paroi_model_id') is not None}
        if ids:
            existing = set(ParoiModel.objects.filter(pk__in=ids).values_list('pk', flat=True))
            missing = ids - existing
            if missing:
                raise serializers.ValidationError(
                    {'triangles': f"paroi_model_id inconnu(s) : {sorted(missing)}."}
                )
        return data

    def _build_envelope(self, validated_data):
        """vertices/triangles sont toujours un remplacement complet de l'un ou
        l'autre quand fourni — pas de fusion par élément (les triangles n'ont
        pas d'identifiant stable). Si un seul des deux est envoyé (cas courant :
        PATCH triangles seul pour ne sauver que des changements d'assignation,
        sans renvoyer tous les sommets), l'autre est repris de l'enveloppe
        existante plutôt que vidé.
        """
        if 'vertices' not in validated_data and 'triangles' not in validated_data:
            return None
        existing = (self.instance.envelope if self.instance else None) or {}

        vertices = validated_data.pop('vertices', None)
        if vertices is None:
            vertices = existing.get('vertices', [])

        triangles = validated_data.pop('triangles', None)
        if triangles is None:
            # ATTENTION (piège déjà rencontré au Lot K) : toute clé de triangle
            # absente de ce dict est silencieusement perdue au prochain PATCH
            # "triangles seul" — étendre explicitement cette liste pour tout
            # nouveau champ ajouté à TriangleInputSerializer (ex. Lot J,
            # shading_profile_id).
            triangles = [
                {'v': t['v'], 'group': t.get('group'), 'paroi_model_id': t.get('paroi_model_id'),
                 'boundary': t.get('boundary', 'exterior_air'),
                 'shading_profile_id': t.get('shading_profile_id')}
                for t in existing.get('triangles', [])
            ]
        try:
            computed = geometry.compute_envelope_geometry(vertices, [dict(t) for t in triangles])
        except geometry.GeometryError as exc:
            raise serializers.ValidationError({'triangles': str(exc)})
        return {'vertices': vertices, 'triangles': computed}

    def create(self, validated_data):
        envelope = self._build_envelope(validated_data) or {'vertices': [], 'triangles': []}
        return Building.objects.create(envelope=envelope, sun_visibility_stale=True, **validated_data)

    def update(self, instance, validated_data):
        envelope = self._build_envelope(validated_data)
        # L'ombrage précalculé ne reste valide que si ni l'enveloppe ni
        # l'environnement associé n'ont changé depuis le dernier calcul.
        if envelope is not None or 'environment' in validated_data:
            instance.sun_visibility_stale = True
        if envelope is not None:
            instance.envelope = envelope
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class RefineMeshRequestSerializer(serializers.Serializer):
    max_edge_length = serializers.FloatField(min_value=0.02, max_value=50.0)


class GenerateEnvironmentRequestSerializer(serializers.Serializer):
    """POST /api/environnements/generer/ — voir api.geodata.generate_environment_mesh."""

    lat = serializers.FloatField(min_value=-90.0, max_value=90.0)
    lon = serializers.FloatField(min_value=-180.0, max_value=180.0)
    radius_m = serializers.FloatField(min_value=10.0, max_value=400.0)


class SearchNearbyBuildingsRequestSerializer(serializers.Serializer):
    """POST /api/batiments/rechercher/ (Lot T, mode simplifié) — voir
    api.geodata.search_nearby_buildings. Rayon volontairement plus petit que
    GenerateEnvironmentRequestSerializer (400 m) : ici on cherche UN bâtiment
    précis (le sien), pas tous les obstacles alentour."""

    lat = serializers.FloatField(min_value=-90.0, max_value=90.0)
    lon = serializers.FloatField(min_value=-180.0, max_value=180.0)
    radius_m = serializers.FloatField(required=False, min_value=5.0, max_value=150.0, default=50.0)



class GenerateBuildingEnvironmentRequestSerializer(serializers.Serializer):
    """POST /api/batiments/<id>/generer-environnement/ — voir tasks.generate_environment_for_building."""

    radius_m = serializers.FloatField(min_value=10.0, max_value=400.0)


class WeatherFetchRequestSerializer(serializers.Serializer):
    """POST /api/meteo/recuperer/ (Lot L : 'archive' ; Lot S : 'tmy') — voir
    api.weather_source.build_weather_series / build_tmy_or_fallback_series.
    Endpoint autonome (pas rattaché à un bâtiment) : lat/lon peuvent être ceux du
    géoréférencement d'un bâtiment (repris côté client) ou saisis librement — la
    météo n'est jamais persistée sur un Building, seulement renvoyée au client."""

    lat = serializers.FloatField(min_value=-90.0, max_value=90.0)
    lon = serializers.FloatField(min_value=-180.0, max_value=180.0)
    # 'archive' (défaut, comportement du Lot L) : historique réel entre start_date
    # et end_date. 'tmy' (Lot S) : année type PVGIS, statistiquement représentative
    # — start_date/end_date ne servent alors QUE de repli si PVGIS ne couvre pas la
    # zone (toujours requis : le contrat reste le même quel que soit source, plutôt
    # que de rendre les dates conditionnellement obligatoires).
    source = serializers.ChoiceField(choices=['archive', 'tmy'], required=False, default='archive')
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    # Cap real->local azimuth (Building.georef_north_offset_deg si le bâtiment
    # d'origine est géoréférencé) — 0.0 = pas de rotation (comportement par défaut,
    # correct pour un bâtiment non géoréférencé).
    north_offset_deg = serializers.FloatField(required=False, default=0.0)

    def validate(self, data):
        if data['start_date'] > data['end_date']:
            raise serializers.ValidationError("start_date doit être antérieure ou égale à end_date.")
        if (data['end_date'] - data['start_date']).days > 366:
            raise serializers.ValidationError("Période limitée à 366 jours (un an d'heures au plus).")
        return data


class WeatherPointSerializer(serializers.Serializer):
    t_ext = serializers.FloatField(min_value=-60.0, max_value=60.0)
    h_s = serializers.FloatField(min_value=-90.0, max_value=90.0)
    theta_i = serializers.FloatField(min_value=0.0, max_value=180.0)
    e_dir = serializers.FloatField(min_value=0.0, max_value=1400.0)
    e_dif = serializers.FloatField(min_value=0.0, max_value=600.0)


class InteriorSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=['imposed', 'free'])
    # h_i (résistance superficielle intérieure) s'applique dans les deux modes — voir
    # solver.run_simulation : c'est ce qui sépare la température d'air T_int de la
    # température de SURFACE intérieure, exactement comme h_e le fait côté extérieur.
    h_i = serializers.FloatField(min_value=0.1, max_value=100.0)
    t_int = serializers.FloatField(required=False, min_value=-30.0, max_value=50.0)
    c_air_int = serializers.FloatField(required=False, min_value=100.0, max_value=10_000_000.0)
    # Renouvellement d'air (mode 'free' uniquement — ignoré en 'imposed', voir
    # solver.py) : conductance déjà ramenée au m² de la paroi étudiée, comme
    # c_air_int — PAS un débit réel, le 1D ne connaît pas de volume de
    # bâtiment (voir BuildingInteriorSerializer.debit_vent_m3h côté 3D pour
    # l'équivalent en grandeur physique réelle).
    g_vent = serializers.FloatField(required=False, min_value=0.0, max_value=100.0, default=0.0)

    def validate(self, data):
        if data['mode'] == 'imposed' and 't_int' not in data:
            raise serializers.ValidationError("t_int est requis en mode 'imposed'.")
        if data['mode'] == 'free' and 'c_air_int' not in data:
            raise serializers.ValidationError("c_air_int est requis en mode 'free'.")
        return data


class CalculRequestSerializer(serializers.Serializer):
    layers = LayerSerializer(many=True, min_length=1, max_length=20)
    dx_max = serializers.FloatField(min_value=0.001, max_value=1.0)
    h_e = serializers.FloatField(min_value=0.1, max_value=100.0)
    wall_tilt_deg = serializers.FloatField(required=False, min_value=0.0, max_value=180.0, default=90.0)
    interior = InteriorSerializer()
    t_init = serializers.FloatField(min_value=-30.0, max_value=50.0)
    weather = WeatherPointSerializer(many=True, min_length=1, max_length=8784)


class BuildingWeatherPointSerializer(serializers.Serializer):
    t_ext = serializers.FloatField(min_value=-60.0, max_value=60.0)
    sun_azimuth = serializers.FloatField(min_value=0.0, max_value=360.0)
    sun_elevation = serializers.FloatField(min_value=-90.0, max_value=90.0)
    e_dir = serializers.FloatField(min_value=0.0, max_value=1400.0)
    e_dif = serializers.FloatField(min_value=0.0, max_value=600.0)
    # Vitesse de vent à 10 m (m/s, Lot R) — optionnelle, requise heure par
    # heure uniquement si BuildingCalculRequestSerializer.h_e_dynamic est
    # activé (voir sa validation). Alimentée automatiquement par le fetch météo
    # (Open-Meteo Archive/PVGIS TMY, tous deux normalisés en m/s) — absente
    # pour une météo collée à la main, sans effet dans ce cas (h_e reste la
    # constante du payload).
    wind_m_s = serializers.FloatField(required=False, allow_null=True, default=None,
                                       min_value=0.0, max_value=100.0)
    # Heure absolue de ce point (Lot AB1) : nombre d'heures depuis minuit du
    # premier jour de la série, donc `% 24` = heure du jour. Alimentée par le
    # fetch météo depuis l'horodatage réel de chaque ligne. Remplace, quand elle
    # est présente, l'indexation par POSITION (`heure_debut + hour_idx`) du
    # planning horaire (Lot Q) et des volets (Lot J) : le fetch saute les heures
    # à donnée manquante, donc la position ne correspond plus à l'heure réelle
    # dès le premier trou — et la dérive était définitive et silencieuse.
    # Absente (série collée à la main) : repli sur `heure_debut`, comportement
    # d'origine. Bornée à un an d'heures, comme la série elle-même.
    hour_index = serializers.IntegerField(required=False, allow_null=True, default=None,
                                           min_value=0, max_value=8783)
    # Consignes thermostat de CETTE heure (Lot V, calendrier d'occupation) —
    # optionnelles, remplacent interior.t_min/t_max pour cette heure
    # uniquement (mode 'thermostat' seulement, sans effet sinon). Résolues
    # côté client à partir d'un profil d'usage (scolaire/tertiaire/habitation)
    # — le serveur ne connaît que ces deux nombres, jamais de date calendaire.
    t_min = serializers.FloatField(required=False, allow_null=True, default=None,
                                    min_value=-30.0, max_value=50.0)
    t_max = serializers.FloatField(required=False, allow_null=True, default=None,
                                    min_value=-30.0, max_value=50.0)


class BuildingInteriorSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=['imposed', 'free', 'thermostat'])
    # h_i devient optionnel si h_i_auto est activé (voir validate) — sinon requis
    # comme avant.
    h_i = serializers.FloatField(required=False, min_value=0.1, max_value=100.0)
    # Lot R : dérive h_i par triangle depuis son tilt_deg (ISO 6946 —
    # mur/plafond/plancher, voir building_solver.h_i_from_tilt) plutôt que la
    # constante h_i ci-dessus (alors ignorée). Défaut False : comportement
    # historique inchangé.
    h_i_auto = serializers.BooleanField(required=False, default=False)
    t_int = serializers.FloatField(required=False, min_value=-30.0, max_value=50.0)
    c_air_int = serializers.FloatField(required=False, min_value=100.0, max_value=1_000_000_000.0)
    t_min = serializers.FloatField(required=False, min_value=-30.0, max_value=50.0)
    t_max = serializers.FloatField(required=False, min_value=-30.0, max_value=50.0)
    # Renouvellement d'air (modes 'free'/'thermostat' — ignoré en 'imposed',
    # voir building_solver.py) : grandeurs physiques réelles pour tout le
    # bâtiment, converties en conductance absolue g_vent = 0,34 * debit_vent_m3h
    # * (1 - eta_recup_vent) et ajoutée une seule fois au nœud d'air global.
    debit_vent_m3h = serializers.FloatField(required=False, min_value=0.0, max_value=100_000.0, default=0.0)
    eta_recup_vent = serializers.FloatField(required=False, min_value=0.0, max_value=0.95, default=0.0)
    # Apports internes (Lot H) : occupants + éclairage + électroménager, une seule
    # puissance constante pour tout le run si aucun `planning` (Lot Q,
    # BuildingCalculRequestSerializer) n'est fourni — ignorée en mode 'imposed'
    # pour la même raison que debit_vent_m3h (Dirichlet écrase la ligne du nœud
    # d'air, voir building_solver.py).
    apports_internes_w = serializers.FloatField(required=False, min_value=0.0, max_value=1_000_000.0, default=0.0)

    def validate(self, data):
        mode = data['mode']
        if mode == 'imposed' and 't_int' not in data:
            raise serializers.ValidationError("t_int est requis en mode 'imposed'.")
        if mode in ('free', 'thermostat') and 'c_air_int' not in data:
            raise serializers.ValidationError(f"c_air_int est requis en mode '{mode}'.")
        if mode == 'thermostat':
            if 't_min' not in data or 't_max' not in data:
                raise serializers.ValidationError("t_min et t_max sont requis en mode 'thermostat'.")
            if data['t_min'] >= data['t_max']:
                raise serializers.ValidationError("t_min doit être strictement inférieur à t_max.")
        if not data.get('h_i_auto', False) and 'h_i' not in data:
            raise serializers.ValidationError("h_i est requis sauf si h_i_auto est activé.")
        return data


class PlanningEntrySerializer(serializers.Serializer):
    """Une heure d'un `planning` (Lot Q) — mêmes bornes que les champs constants
    équivalents de BuildingInteriorSerializer, qu'elle remplace heure par heure."""

    debit_vent_m3h = serializers.FloatField(required=False, min_value=0.0, max_value=100_000.0, default=0.0)
    eta_recup_vent = serializers.FloatField(required=False, min_value=0.0, max_value=0.95, default=0.0)
    apports_internes_w = serializers.FloatField(required=False, min_value=0.0, max_value=1_000_000.0, default=0.0)
    # Lot J : UN SEUL planning volets pour tous les triangles ayant un
    # shading_profile_id (envelope.triangles[i]) — pas par fenêtre. Contrairement
    # aux trois champs ci-dessus, s'applique dans TOUS les modes intérieurs (y
    # compris 'imposed'), voir building_solver.run_building_simulation.
    volets_fermes = serializers.BooleanField(required=False, default=False)


class BuildingCalculRequestSerializer(serializers.Serializer):
    dx_max = serializers.FloatField(min_value=0.001, max_value=1.0)
    # h_e reste requis même si h_e_dynamic est activé (alors simplement ignoré)
    # — évite une exigence conditionnelle pour un gain marginal.
    h_e = serializers.FloatField(min_value=0.1, max_value=100.0)
    # Lot R : dérive h_e heure par heure depuis weather[h].wind_m_s (corrélation
    # de Jürges, building_solver.h_e_from_wind) plutôt que la constante h_e
    # ci-dessus — exige alors wind_m_s sur CHAQUE point météo (voir validate).
    # Défaut False : comportement historique inchangé.
    h_e_dynamic = serializers.BooleanField(required=False, default=False)
    interior = BuildingInteriorSerializer()
    t_init = serializers.FloatField(min_value=-30.0, max_value=50.0)
    weather = BuildingWeatherPointSerializer(many=True, min_length=1, max_length=8784)
    # 'precomputed' (défaut) : grille d'ombrage précalculée (rapide, discrétisée).
    # 'realtime' : lancer de rayons réel à chaque heure (précis, plus lent) —
    # voir building_solver.run_building_simulation.
    shadow_mode = serializers.ChoiceField(choices=['precomputed', 'realtime'], required=False, default='precomputed')
    # Lot K : température de sol constante, utilisée uniquement par les triangles
    # marqués boundary='ground' (défaut ignoré sinon). 12°C = moyenne annuelle
    # usuelle en France (10-14°C) — dérivation automatique depuis la météo
    # reportée au Lot L (météo réelle datée), voir to_do.md racine.
    t_ground = serializers.FloatField(required=False, min_value=-10.0, max_value=30.0, default=12.0)
    # Lot AB3 : résistance du SOL sous un triangle 'ground', en remplacement de
    # h_e (un dallage n'a pas de film convectif, et depuis le Lot R son couplage
    # à la terre variait avec le vent). Minimum 0,05 plutôt que 0 : la
    # conductance vaut 1/r_ground, et un contact « parfait » n'a pas de sens
    # physique ici. Voir building_solver.DEFAULT_R_GROUND.
    r_ground = serializers.FloatField(required=False, min_value=0.05, max_value=10.0,
                                       default=building_solver.DEFAULT_R_GROUND)
    # Lot Q : profil "jour type" (24 heures, index 0 = minuit) qui remplace heure
    # par heure debit_vent_m3h/eta_recup_vent/apports_internes_w de `interior` —
    # modes 'free'/'thermostat' uniquement (ignoré en 'imposed', comme les
    # constantes qu'il remplace). Absent -> comportement inchangé (constantes de
    # `interior`).
    planning = PlanningEntrySerializer(many=True, required=False)
    # Heure du premier point de `weather` (0=minuit) — sert à indexer le planning
    # (planning[(heure_debut + hour_idx) % 24]), sans effet si `planning` absent.
    heure_debut = serializers.IntegerField(required=False, min_value=0, max_value=23, default=0)

    def validate(self, data):
        if data.get('h_e_dynamic', False):
            missing = [i for i, w in enumerate(data.get('weather', [])) if w.get('wind_m_s') is None]
            if missing:
                raise serializers.ValidationError(
                    {'weather': f"h_e_dynamic actif : vent manquant pour {len(missing)} heure(s) "
                                 f"(ex. index {missing[0]}) — chaque point météo doit fournir wind_m_s."}
                )
        return data

    def validate_planning(self, value):
        if len(value) != 24:
            raise serializers.ValidationError(
                f"planning doit contenir exactement 24 entrées (une par heure de la journée), {len(value)} reçue(s)."
            )
        return value


class DepartmentSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(source='members.count', read_only=True)

    class Meta:
        model = Department
        fields = ['id', 'name', 'description', 'member_count']


class UserRecordSerializer(serializers.ModelSerializer):
    department = DepartmentSerializer(read_only=True)

    class Meta:
        model = UserRecord
        fields = ['email', 'display_name', 'department', 'registered_at']
