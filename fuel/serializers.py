from django.conf import settings
from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200, help_text="'City, ST', 'lat,lon' or a US address, e.g. 'Chicago, IL'"
    )
    finish = serializers.CharField(max_length=200, help_text="Same formats as start")
    start_fuel_gallons = serializers.FloatField(
        required=False, min_value=0.0,
        help_text="Fuel in the tank at the start. Defaults to a full tank.",
    )
    corridor_miles = serializers.FloatField(
        required=False, min_value=0.5, max_value=25.0,
        help_text="Max distance a station may be from the route line.",
    )
    stop_penalty = serializers.FloatField(
        required=False, min_value=0.0, max_value=1000.0,
        help_text="USD cost assigned to each extra stop when optimising (0 = cheapest fuel only).",
    )
    include_geometry = serializers.BooleanField(required=False, default=True)

    def validate_start_fuel_gallons(self, value):
        tank = settings.FUEL_MAX_RANGE_MILES / settings.FUEL_MPG
        if value > tank:
            raise serializers.ValidationError(f"Tank holds at most {tank:g} gallons.")
        return value

    def validate(self, attrs):
        if attrs["start"].strip().lower() == attrs["finish"].strip().lower():
            raise serializers.ValidationError("start and finish must be different.")
        return attrs
