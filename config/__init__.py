"""Paquete de configuración del proyecto.

Lo único que vive aquí es el apaño de PyMySQL, y está aquí y no en
passenger_wsgi.py porque tiene que correr antes de que Django importe el
backend de MySQL. Eso pasa al importar `config.settings`, que es lo primero
que hacen tanto Passenger como `manage.py`; poniéndolo en el __init__ del
paquete, los dos caminos quedan cubiertos con una sola línea.
"""

try:
    import pymysql
except ImportError:
    # No hay nada que apañar: o la base no es MySQL (sqlite en desarrollo,
    # Postgres si algún día se vuelve a Supabase), o está instalado
    # mysqlclient, que es el driver que Django espera de serie.
    pass
else:
    # Django 6.0 exige mysqlclient 2.2.1 o más nuevo, y lo comprueba leyendo
    # `version_info` del módulo que encuentre registrado como MySQLdb (ver
    # django/db/backends/mysql/base.py). PyMySQL habla el mismo protocolo y
    # expone la misma API, pero se numera por su cuenta (1.1.x), así que sin
    # estas dos líneas Django se planta con «mysqlclient 2.2.1 or newer is
    # required» aunque el driver funcione perfectamente.
    #
    # Se usa PyMySQL y no mysqlclient porque el hosting no deja compilar:
    # mysqlclient es una extensión en C y su `pip install` muere con
    # «[Errno 13] Permission denied: 'gcc'». PyMySQL es Python puro.
    pymysql.version_info = (2, 2, 7, 'final', 0)
    pymysql.__version__ = '2.2.7'
    pymysql.install_as_MySQLdb()
