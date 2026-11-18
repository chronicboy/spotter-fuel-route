from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .optimizer import InfeasibleRouteError
from .routing import GeocodingError, RoutingError
from .serializers import RouteRequestSerializer
from .services import plan_trip

ERROR_STATUS = {
    GeocodingError: status.HTTP_400_BAD_REQUEST,
    InfeasibleRouteError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    RoutingError: status.HTTP_502_BAD_GATEWAY,
}


def _run(data):
    """Validate input and plan. Returns (payload, http_status)."""
    serializer = RouteRequestSerializer(data=data)
    if not serializer.is_valid():
        return {"errors": serializer.errors}, status.HTTP_400_BAD_REQUEST
    try:
        return plan_trip(**serializer.validated_data), status.HTTP_200_OK
    except tuple(ERROR_STATUS) as exc:
        return {"error": str(exc)}, ERROR_STATUS[type(exc)]


class RoutePlanView(APIView):
    """Plan the cheapest fuel stops between two US locations.

    GET  /api/route/?start=Chicago, IL&finish=Denver, CO
    POST /api/route/  {"start": "Chicago, IL", "finish": "Denver, CO"}

    Optional: start_fuel_gallons (default full tank = 50), corridor_miles
    (default 8), include_geometry (default true).
    """

    def get(self, request):
        return self._respond(request, request.query_params)

    def post(self, request):
        return self._respond(request, request.data)

    def _respond(self, request, data):
        payload, code = _run(data)
        if code == status.HTTP_200_OK:
            query = {k: v for k, v in data.items() if k != "format"}
            payload["map_url"] = request.build_absolute_uri(
                reverse("route-map") + "?" + urlencode(query)
            )
        return Response(payload, status=code)


def route_map(request):
    """HTML page: a form, plus a Leaflet map of the plan when start/finish are given."""
    context = {"start": request.GET.get("start", ""), "finish": request.GET.get("finish", ""),
               "start_fuel_gallons": request.GET.get("start_fuel_gallons", "")}
    if context["start"] and context["finish"]:
        payload, code = _run(request.GET)
        if code == status.HTTP_200_OK:
            context["plan"] = payload
        else:
            context["error"] = payload.get("error") or payload.get("errors")
    return render(request, "fuel/map.html", context)
