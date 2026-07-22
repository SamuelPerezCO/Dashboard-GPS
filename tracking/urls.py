from django.urls import path

from . import views

app_name = 'tracking'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('api/dashboard/', views.api_dashboard, name='api_dashboard'),
    path('mapa/', views.fleet_dashboard, name='fleet'),
    path('api/fleet/', views.api_fleet, name='api_fleet'),
]
