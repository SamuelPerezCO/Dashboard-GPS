"""Puerta de entrada de Phusion Passenger (cPanel, «Setup Python App»).

Passenger no ejecuta `manage.py runserver` ni gunicorn: importa este archivo
y busca dentro una variable que se llame `application`. Eso es todo lo que
tiene que haber aquí; el resto del arranque de Django ya vive en
config/wsgi.py, que es el mismo que usaría gunicorn.

En el formulario de cPanel se traduce así:

    Application startup file : passenger_wsgi.py
    Application Entry point  : application

Para reiniciar la aplicación después de cambiar el código o las variables de
entorno: el botón «Restart» de cPanel, o `touch tmp/restart.txt` desde la
carpeta de la aplicación.
"""

import os
import sys
from pathlib import Path

# La carpeta de este archivo, que es también la de manage.py y la del paquete
# `config`. Passenger arranca la aplicación desde una carpeta que no tiene por
# qué ser esta, y sin la línea de abajo el import de `config.settings` falla
# con ModuleNotFoundError.
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# config/settings.py lee el .env que está junto a manage.py, así que las
# credenciales pueden venir de ahí o de las variables de entorno de cPanel.
# Las de cPanel ganan: load_dotenv() no pisa lo que ya está puesto.
from config.wsgi import application  # noqa: E402  (va después del sys.path)
