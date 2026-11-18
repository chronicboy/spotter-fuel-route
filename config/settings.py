"""Django settings for the fuel route planner.

Everything tunable can be overridden with environment variables.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None, cast=str):
    value = os.environ.get(name)
    if value is None:
        return default
    if cast is bool:
        return value.lower() in {"1", "true", "yes", "on"}
    return cast(value)


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env("DJANGO_DEBUG", True, bool)
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "fuel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route",
        "OPTIONS": {"MAX_ENTRIES": 2000},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

# --- Fuel route planner -----------------------------------------------------

DATA_DIR = BASE_DIR / "data"
PLACES_CSV = DATA_DIR / "us_places.csv"
FUEL_PRICES_CSV = DATA_DIR / "fuel-prices-for-be-assessment.csv"

FUEL_MAX_RANGE_MILES = env("FUEL_MAX_RANGE_MILES", 500.0, float)
FUEL_MPG = env("FUEL_MPG", 10.0, float)
# Stations are geocoded to their town, so allow some distance from the route line.
FUEL_DEFAULT_CORRIDOR_MILES = env("FUEL_DEFAULT_CORRIDOR_MILES", 8.0, float)
# Keep only the cheapest station per N-mile stretch of route (bounds the
# optimiser's input; 0 disables).
FUEL_SEGMENT_MILES = env("FUEL_SEGMENT_MILES", 25.0, float)
# USD of "inconvenience" charged per stop in the optimiser's objective so it
# won't stop just to save cents. 0 = pure minimum fuel cost.
FUEL_DEFAULT_STOP_PENALTY = env("FUEL_DEFAULT_STOP_PENALTY", 5.0, float)

# Free routing: OSRM public demo server (no API key). Swap for a self-hosted
# OSRM instance in production.
OSRM_BASE_URL = env("OSRM_BASE_URL", "https://router.project-osrm.org")
# Only used when an input is not "lat,lon" and not a known "City, ST".
NOMINATIM_URL = env("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
HTTP_USER_AGENT = env("HTTP_USER_AGENT", "spotter-fuel-route/1.0 (assessment demo)")
HTTP_TIMEOUT_SECONDS = env("HTTP_TIMEOUT_SECONDS", 20.0, float)

ROUTE_CACHE_SECONDS = env("ROUTE_CACHE_SECONDS", 60 * 60 * 24, int)
PLAN_CACHE_SECONDS = env("PLAN_CACHE_SECONDS", 60 * 60, int)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"fuel": {"handlers": ["console"], "level": "INFO"}},
}
