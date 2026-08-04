from rest_framework import serializers
from .models import Department, UserRecord, ParoiModel


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
