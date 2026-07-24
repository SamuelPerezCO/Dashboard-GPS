"""Configuración de la app Django tracking."""

from django.apps import AppConfig


class TrackingConfig(AppConfig):
    """Configuración de la app tracking (dashboard y API de flota GPS)."""

    name = 'tracking'
