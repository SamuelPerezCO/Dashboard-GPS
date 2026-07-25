"""Rutas URL de la app tracking.

Define tanto las páginas HTML del dashboard como sus endpoints JSON
correspondientes (ver :mod:`tracking.views`).
"""

from django.urls import path

from . import views

app_name = 'tracking'

urlpatterns = [
    path('entrar/', views.login_view, name='login'),  # Formulario de acceso.
    path('salir/', views.logout_view, name='logout'),  # Cierra la sesión (POST).
    path('', views.dashboard, name='dashboard'),  # Dashboard de ocupación por rango de fechas.
    path('api/dashboard/', views.api_dashboard, name='api_dashboard'),  # JSON del dashboard.
    path('mapa/', views.fleet_dashboard, name='fleet'),  # Mapa de flota en vivo.
    path('api/fleet/', views.api_fleet, name='api_fleet'),  # JSON del mapa de flota.
]
