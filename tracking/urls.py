"""
Rutas (URLs) de la app "tracking".

config/urls.py incluye este archivo en la raíz del sitio, así que:

    http://127.0.0.1:8210/                -> dashboard principal (el del boceto)
    http://127.0.0.1:8210/api/dashboard/  -> datos JSON del dashboard
    http://127.0.0.1:8210/mapa/           -> mapa de la flota (guardado para
                                             después, sin enlace en el menú)
    http://127.0.0.1:8210/api/fleet/      -> datos JSON de la flota

Los "name=" permiten referirse a cada URL desde los templates con
{% url 'tracking:dashboard' %} sin escribir la ruta a mano.
"""

from django.urls import path

from . import views

app_name = 'tracking'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/dashboard/', views.api_dashboard, name='api_dashboard'),
    path('mapa/', views.fleet_dashboard, name='fleet'),
    path('api/fleet/', views.api_fleet, name='api_fleet'),
]
