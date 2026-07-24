"""Modelos de la app tracking.

La app no persiste datos propios en la base de datos: toda la
información de vehículos, posiciones y alertas se obtiene en tiempo
real desde el API de Service24GPS (ver :mod:`tracking.api_client`).
"""

from django.db import models
