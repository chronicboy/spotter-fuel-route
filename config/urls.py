from django.contrib import admin
from django.urls import path

from fuel import views

urlpatterns = [
    path("", views.route_map, name="route-map"),
    path("api/route/", views.RoutePlanView.as_view(), name="route-plan"),
    path("admin/", admin.site.urls),
]
