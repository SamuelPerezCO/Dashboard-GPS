"""Rutas del sitio.

La raíz es el login; la portada pública vive en /inicio/ y el dashboard
en /dashboard/.
"""

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'tracking'

urlpatterns = [
    path('', views.login_view, name='login'),
    # /entrar/ era el login antes de que se mudara a la raíz. Se queda como
    # redirección para no romper enlaces guardados; `query_string` conserva
    # el ?next= con el que llega la gente que iba a una página protegida.
    path('entrar/', RedirectView.as_view(pattern_name='tracking:login',
                                         query_string=True), name='login_antiguo'),
    path('inicio/', views.home, name='home'),
    # TEMPORAL: botón de acceso instantáneo sin cuenta. Se borra con la
    # vista, el botón del login y la entrada 'invitado' de settings.
    path('acceso-temporal/', views.acceso_temporal_view, name='acceso_temporal'),
    path('salir/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('api/dashboard/', views.api_dashboard, name='api_dashboard'),
    path('mapa/', views.fleet_dashboard, name='fleet'),
    path('api/fleet/', views.api_fleet, name='api_fleet'),
]
