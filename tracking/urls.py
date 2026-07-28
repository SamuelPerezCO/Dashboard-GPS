"""Rutas URL de la app tracking.

Define la portada pública del sitio, las páginas HTML del dashboard y
sus endpoints JSON correspondientes (ver :mod:`tracking.views`).

La raíz ``/`` es la portada comercial (pública); el dashboard vive en
``/dashboard/``, que es a donde lleva el botón DASHBOARD de la portada
y a donde vuelve el login cuando no se pidió otra página.
"""

from django.urls import path

from . import views

app_name = 'tracking'

urlpatterns = [
    path('', views.home, name='home'),  # Portada pública (copia de rastrelital.com).
    path('entrar/', views.login_view, name='login'),  # Formulario de acceso.
    path('salir/', views.logout_view, name='logout'),  # Cierra la sesión (POST).
    path('dashboard/', views.dashboard, name='dashboard'),  # Ocupación por rango de fechas.
    path('api/dashboard/', views.api_dashboard, name='api_dashboard'),  # JSON del dashboard.
    path('mapa/', views.fleet_dashboard, name='fleet'),  # Mapa de flota en vivo.
    path('api/fleet/', views.api_fleet, name='api_fleet'),  # JSON del mapa de flota.
]
