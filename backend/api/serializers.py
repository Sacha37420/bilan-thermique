from rest_framework import serializers
from .models import Department, UserRecord, ParoiModel, Building, Environment, Job
from . import geometry


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
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def validate_name(self, value):
        qs = ParoiModel.objects.filter(name=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Un modèle de paroi porte déjà ce nom.")
        return value

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
            triangles = [
                {'v': t['v'], 'group': t.get('group'), 'paroi_model_id': t.get('paroi_model_id')}
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


class GenerateBuildingEnvironmentRequestSerializer(serializers.Serializer):
    """POST /api/batiments/<id>/generer-environnement/ — voir tasks.generate_environment_for_building."""

    radius_m = serializers.FloatField(min_value=10.0, max_value=400.0)


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


class BuildingInteriorSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=['imposed', 'free', 'thermostat'])
    h_i = serializers.FloatField(min_value=0.1, max_value=100.0)
    t_int = serializers.FloatField(required=False, min_value=-30.0, max_value=50.0)
    c_air_int = serializers.FloatField(required=False, min_value=100.0, max_value=1_000_000_000.0)
    t_min = serializers.FloatField(required=False, min_value=-30.0, max_value=50.0)
    t_max = serializers.FloatField(required=False, min_value=-30.0, max_value=50.0)

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
        return data


class BuildingCalculRequestSerializer(serializers.Serializer):
    dx_max = serializers.FloatField(min_value=0.001, max_value=1.0)
    h_e = serializers.FloatField(min_value=0.1, max_value=100.0)
    interior = BuildingInteriorSerializer()
    t_init = serializers.FloatField(min_value=-30.0, max_value=50.0)
    weather = BuildingWeatherPointSerializer(many=True, min_length=1, max_length=8784)
    # 'precomputed' (défaut) : grille d'ombrage précalculée (rapide, discrétisée).
    # 'realtime' : lancer de rayons réel à chaque heure (précis, plus lent) —
    # voir building_solver.run_building_simulation.
    shadow_mode = serializers.ChoiceField(choices=['precomputed', 'realtime'], required=False, default='precomputed')


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
