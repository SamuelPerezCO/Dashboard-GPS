"""Rutas del sitio.

La raíz es la portada pública; el dashboard vive en /dashboard/.
"""

from django.urls import path

from . import views

app_name = 'tracking'

urlpatterns = [
    path('', views.home, name='home'),
    path('entrar/', views.login_view, name='login'),
    path('salir/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('api/dashboard/', views.api_dashboard, name='api_dashboard'),
    path('mapa/', views.fleet_dashboard, name='fleet'),
    path('api/fleet/', views.api_fleet, name='api_fleet'),
]
