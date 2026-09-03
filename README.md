# Dashboard GPS — Rastrelital

Dashboard de ocupación de pasajeros para la flota de **Expreso Brasilia** (35
buses), construido sobre el WebService de **Service24GPS**.

Responde una pregunta: *¿qué tan llenos van los buses?* Cruza los viajes (los
"servicios") con los timbrados de iButton (los pasajeros que suben) y calcula
la ocupación por vehículo, por empresa y por día.

---

## Cómo funciona

El API de Service24GPS **no tiene un campo de empresa o cliente**. La única
señal disponible es el nombre de la geocerca, que viene incrustado en el texto
de la alerta:

```
Unidad WEO 371 Generó ALERTA DENTRO DE GEOCERCA PROCAPS el 2026/07/21 21:31:19
```

De ahí sale todo el modelo:

| Concepto | De dónde sale |
|---|---|
| **Servicio** (un viaje) | Una tanda de timbradas seguidas: si el bus pasa más de 25 minutos sin que nadie timbre, lo que venga después es otro viaje. |
| **Empresa** | Nombre de la geocerca (`PROCAPS`, `DITAR`, `RELIANZ`) en la que entra el bus. |
| **Timbrada** (un pasajero) | Evento `2720` del historial, con el `iButton_ID`. |
| **Capacidad** | Tabla fija por interno en `services.py` (lista física de la flota). |
| **Tipo de vehículo** | La misma tabla: buseta, busetón o bus. Filtra la flota entera, no los viajes. |

La ocupación de un bus es:

```
ocupación % = timbradas / (servicios × capacidad) × 100
```

Es decir, pasajeros promedio por viaje ÷ asientos. Los buses sin capacidad
conocida o sin servicios registrados quedan fuera del promedio en vez de entrar
como cero, para no ensuciarlo.

Como una timbrada no dice a qué empresa pertenece, se le atribuye la de la
**siguiente entrada a geocerca** de ese mismo bus ese día.

### Por qué un servicio se cuenta por timbradas y no por geocercas

Contar los servicios como entradas a geocerca daba una ocupación al doble de la
real. Un día normal de un bus son hasta seis viajes —tres turnos por dos
sentidos— pero solo la mitad entra a una geocerca:

```
04:33-05:13  suben 12 en sus casas   ─┐ viaje 1
05:40        ENTRA A PROCAPS          ─┘ (sí dispara geocerca)
06:13-06:22  suben 6 en la planta    ─┐ viaje 2
             (terminan en sus casas)  ─┘ (no dispara: no hay geocerca allá)
```

Los viajes que *salen* de la planta a dejar gente en su casa no cruzan ninguna
geocerca, pero sus pasajeros sí timbran. El numerador los contaba y el
denominador no, así que la ocupación salía inflada —hasta 291 % en un caso, más
pasajeros que asientos—. Las timbradas, en cambio, están en todos los viajes:
sin pasajeros no hay viaje que medir.

El corte de 25 minutos (`HUECO_ENTRE_SERVICIOS` en `services.py`) se midió
sobre el mes completo: es el más angosto que todavía respeta el techo de seis
viajes al día, y por encima empiezan a aparecer tandas con más pasajeros que
asientos, o sea dos viajes contados como uno.

Las entradas a geocerca se siguen contando aparte, en `entradas_geocerca`,
porque son las que dicen a qué empresa pertenece cada viaje y las que delatan
una geocerca mal puesta en la plataforma (`manage.py auditar_servicios`).

### La pestaña «Sin identificar»

> **Hoy la única geocerca creada en la plataforma es la de `PROCAPS`**
> (verificado contra el API el 2026-07-25: 148 de 148 entradas en 7 días).

Por eso las pestañas de **DITAR** y **RELIANZ** salen en cero: no es un error
del código, es que sus geocercas no existen todavía. Toda la actividad que no
se puede atribuir cae en **Sin identificar**, y el dashboard lo explica en
pantalla para que un cero no se lea como "no trabajaron".

En cuanto alguien cree esas geocercas en la plataforma web, **ambas pestañas se
llenan solas**: el código ya reconoce `DITAR` y `RELIANZ` en cualquier parte del
nombre de la geocerca. No hay que tocar nada.

---

## Requisitos

- Python 3.14
- Credenciales del WebService de Service24GPS

## Instalación

```bash
python -m venv venv
venv\Scripts\activate           # Windows
pip install -r requirements.txt
python manage.py runserver
```

El login queda en <http://localhost:8000/>, la portada pública en
<http://localhost:8000/inicio/> y el dashboard en
<http://localhost:8000/dashboard/>.

## Configuración (`.env`)

El archivo `.env` va junto a `manage.py` y **nunca se sube a git**:

```ini
GPS_API_BASE_URL=https://api.service24gps.com/api/v1
GPS_APIKEY=...
GPS_USERNAME=...
GPS_PASSWORD=...

DJANGO_SECRET_KEY=...
DJANGO_DEBUG=1                  # 1 solo en desarrollo

DASHBOARD_CORREO_ADMIN=...      # opcional: cambia el correo y la clave de
DASHBOARD_CLAVE_ADMIN=...       #           una cuenta sin tocar el código
DASHBOARD_CORREO_PROCAPS=...
DASHBOARD_CLAVE_PROCAPS=...
DASHBOARD_CORREO_DITAR=...
DASHBOARD_CLAVE_DITAR=...
DASHBOARD_CORREO_RELIANZ=...
DASHBOARD_CLAVE_RELIANZ=...
```

> ⚠️ Los correos y las contraseñas por defecto están en el repositorio y el
> sitio es público: para producción escribe las ocho variables
> `DASHBOARD_CORREO_*` y `DASHBOARD_CLAVE_*` en el `.env` (que no se sube a
> git). `DJANGO_DEBUG` por defecto está apagado, para que un servidor sin la
> variable no muestre el código y las credenciales al primer error.

**Una variable de cuenta a medio llenar no arranca.** Dejar
`DASHBOARD_CORREO_ADMIN=` (o la clave) sin valor abriría una cuenta a la que se
entra con el campo vacío, y repetir un correo en dos cuentas le daría a una los
permisos de la otra en silencio. `_catalogo_de` en `config/settings.py` revisa
las cuatro cosas al arrancar y lanza `ImproperlyConfigured` diciendo cuál
variable quedó mal; para volver al valor por defecto hay que **borrar** la
línea, no dejarla vacía.

---

## La puerta de entrada (`/`)

La raíz del sitio es el **formulario de acceso**: quien llega sin sesión
aterriza ahí, y quien ya entró pasa derecho al dashboard. Debajo del botón
«Entrar» hay un botón secundario **«Ver la página web»** que lleva a la portada
pública, para poder mostrar el sitio sin necesidad de una cuenta.

## La portada pública (`/inicio/`)

La portada es una **copia de <https://www.rastrelital.com>**: los mismos
textos, imágenes y colores, pero maquetada a mano (el original es WordPress con
Porto + Visual Composer + Revolution Slider). Vive en
`tracking/templates/tracking/home.html`, es contenido fijo —no consulta el API—
y sus imágenes están descargadas en `tracking/static/tracking/site/`, así que no
depende del servidor del otro sitio.

Lo único que se le agregó al diseño original: **al lado del botón azul «Inicio
de Sesión» va un botón ámbar «ENTRAR»** que devuelve al formulario de acceso.
Quien ya tiene sesión iniciada no ve el formulario: el login lo manda derecho al
dashboard.

El botón azul conserva lo que hace en el sitio real: despliega las plataformas
externas de Rastrelital (rastreo y combustible), que no son parte de este
proyecto.

| Ruta | Qué es | ¿Pide sesión? |
|---|---|---|
| `/` | Formulario de acceso | No |
| `/inicio/` | Portada pública (copia del sitio) | No |
| `/entrar/` | Dirección vieja del login: redirige a `/` | No |
| `/dashboard/` | Dashboard de ocupación | Sí |
| `/mapa/` | Mapa de flota en vivo | Sí (solo la cuenta de acceso total) |
| `/api/dashboard/`, `/api/fleet/` | JSON de cada página | Sí |

---

## Acceso

El dashboard entero (páginas y endpoints JSON) exige iniciar sesión en la raíz
del sitio. El login y la portada son las únicas páginas que se ven sin sesión.
No hay usuarios en base de datos: **el catálogo de cuentas es
`DASHBOARD_USUARIOS` en `config/settings.py`**, y ahí se agregan y se quitan.
Tras 5 intentos fallidos desde la misma IP el login se bloquea 60 segundos.
`/admin/` conserva su propio login de Django.

### Cuentas y qué ve cada una

**Se entra con el correo completo, no con un nombre de usuario.** Los correos
de aquí abajo son los que trae el código por defecto, para desarrollo; en
producción cada uno se cambia por el correo real de la persona con la variable
`DASHBOARD_CORREO_*` correspondiente, sin tocar el código.

**Puede ser cualquier correo**: no hay dominio privilegiado y `rastrelital.com`
no tiene nada de especial. `jefe@procaps.com.co`, `samuel@gmail.com` o
`coordinacion@transportes-del-caribe.com.co` sirven igual. Lo único que se
exige es que lleve `@`, que es lo que hace honesto el aviso del login cuando
alguien escribe todavía un nombre corto.

| Correo (por defecto) | Contraseña | Ve los viajes de | Mapa `/mapa/` |
|---|---|---|---|
| `admin@rastrelital.com` | `Admin` | Todas las empresas | Sí |
| `procaps@rastrelital.com` | `Procaps` | PROCAPS + Sin identificar | No |
| `ditar@rastrelital.com` | `Ditar` | DITAR + Sin identificar | No |
| `relianz@rastrelital.com` | `relianz` | RELIANZ + Sin identificar | No |

El correo **no** distingue mayúsculas ni espacios de sobra
(`  Procaps@Rastrelital.com ` = `procaps@rastrelital.com`); la contraseña
**sí**. En la barra del dashboard se muestra lo que va antes del `@`, y el
correo completo queda en el `title` de esa píldora.

> Los nombres cortos de antes (`admin`, `procaps`, `ditar`, `relianz`) **ya no
> sirven para entrar**. Quien escriba uno recibe un aviso de que ahora va el
> correo completo, y ese intento cuenta igual para el bloqueo por IP.

**Los viajes sin empresa se muestran en todas las ventanas.** La pestaña «Sin
identificar» se le agrega a todo usuario, porque ahí cae lo que no se pudo
atribuir a nadie y hoy es casi toda la actividad de DITAR y RELIANZ (sus
geocercas no existen todavía). Sin eso, esos dos usuarios verían el dashboard
vacío.

Para un usuario restringido, la pestaña «Todas» significa *todas las suyas*: un
usuario de PROCAPS nunca ve viajes de DITAR, ni siquiera sumados en un total.

El reparto se aplica en dos capas, y la que manda es la segunda:

| Capa | Dónde | Qué hace |
|---|---|---|
| Pestañas | `views.dashboard` | Solo dibuja las empresas permitidas. |
| Datos | `views.api_dashboard` | Responde `403` a `?empresa=` de otra empresa, y limita el total a las permitidas. |

Ocultar la pestaña no protege nada por sí solo: la URL del JSON se puede
escribir a mano. Por eso el techo del usuario se le pasa siempre a
`services.range_summary`.

Para agregar una cuenta nueva basta con una línea en `_CUENTAS`, la tabla de
la que sale `DASHBOARD_USUARIOS`:

```python
# (sufijo de las variables, correo por defecto, clave por defecto, empresas)
('NUEVO', 'nuevo@rastrelital.com', 'clave', ('PROCAPS', 'DITAR')),
#                                            None = acceso total
```

Con eso la cuenta queda configurable desde el `.env` con
`DASHBOARD_CORREO_NUEVO` y `DASHBOARD_CLAVE_NUEVO`.

**La sesión dura lo que dure la pestaña.** Al cerrarla (o cerrar el navegador)
hay que volver a escribir correo y contraseña; recargar o navegar dentro de la
misma pestaña no molesta. Son dos piezas: la cookie va sin fecha de vencimiento
(`SESSION_EXPIRE_AT_BROWSER_CLOSE`), y el bloque `guardia_pestana` de
`base.html` sella la pestaña en `sessionStorage`, que es lo único que el
navegador borra al cerrarla. Consecuencia a tener en cuenta: **abrir el
dashboard en una segunda pestaña cierra la sesión de las dos**, porque la
pestaña nueva no trae sello y no hay forma de distinguirla de una reabierta.

Mientras la persona escribe su contraseña, el servidor **precalienta en
segundo plano** las consultas al API del último mes, para adelantar trabajo
antes de que la persona elija un rango.

## Pruebas

```bash
python manage.py test
```

125 pruebas que **no tocan la red**: el API se simula con `mock`, así que
corren sin credenciales y en un par de segundos. El catálogo de cuentas también
va fijado en la suite (`CATALOGO` en `tracking/tests.py`), así que las pruebas
no dependen de lo que cada `.env` tenga escrito.

---

## Rendimiento

Una consulta hace una petición por cada día del rango (alertas) más una por cada
bus (timbradas). Se lanzan **en paralelo** (8 a la vez), y todo se cachea.

El dashboard **abre sin fechas y no consulta nada solo**: las gráficas dicen
«Selecciona las fechas» y la consulta arranca cuando el usuario pone el rango
(o usa un atajo). Así no se gasta una consulta pesada en un rango que nadie
pidió.

El login precalienta el **último mes** (de hace 30 días a hoy, por ejemplo
25/06 → 25/07, ver `DIAS_ULTIMO_MES` en `services.py`) — medido contra el API
real:

| | Tiempo |
|---|---|
| Último mes en frío (lo que tarda el precalentamiento) | ~21 s |
| La misma consulta ya precalentada | ~0,4 s |

Como las alertas se cachean **día por día**, ese precalentamiento le sirve a
cualquier rango que caiga dentro del último mes, no solo al rango exacto.

Si el rango cabe en el mes en curso se pide el mes completo de una vez y se
recorta en memoria, en vez de una consulta distinta por cada rango.

Cuando el API falla para algunas unidades, el dashboard **avisa cuántas** en vez
de mostrar un cero limpio, porque un fallo se ve igual que "no llevó pasajeros".

---

## Estructura

```
config/            settings, urls y wsgi del proyecto Django
tracking/
  api_client.py    cliente del WebService (token, cache, reintentos)
  services.py      lógica de negocio: servicios, timbradas, ocupación
  views.py         páginas y endpoints JSON + login + reparto por empresa
  middleware.py    exige sesión iniciada en todo el sitio menos login y portada
  tests.py         pruebas (API simulado)
  templates/       portada, base, dashboard, flota y acceso
  static/tracking/site/   imágenes de la portada (copiadas del sitio)
```

El dashboard de mapa en vivo existe en `/mapa/` pero no está en el menú, y es
solo para `admin`: muestra la flota completa, que no está repartida por empresa.

## Despliegue (Render)

Las peticiones a la GPS API siguen sin tocar base de datos, y la sesión del
login sigue viajando en una cookie firmada. Lo que sí vive en base de
datos ahora son las cuentas del dashboard (`DashboardUsuario`), en Postgres
de Supabase — el disco de Render es efímero, así que no puede ser
`db.sqlite3`.

El build necesita:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --no-input
```

La primera vez, además:

```bash
# Migra las 4 cuentas históricas (admin/procaps/ditar/relianz) a la base
# nueva. Idempotente: se puede volver a correr sin duplicar nada.
python manage.py seed_dashboard_usuarios

# Para poder entrar a /admin/ y gestionar DashboardUsuario desde ahí.
python manage.py createsuperuser
```

De ahí en adelante, las cuentas del dashboard se crean, editan y desactivan
desde `/admin/` — no hace falta volver a tocar variables de entorno ni
redesplegar.

Variables de entorno que hay que poner en Render:

| Variable | Por qué |
|---|---|
| `DJANGO_DEBUG` | **`0`.** Con `1`, cualquier error muestra el código, las variables locales y las credenciales a quien entre. |
| `DJANGO_SECRET_KEY` | Firma la cookie de sesión. Si falta, se usa la clave de ejemplo que está en el repo y **cualquiera podría fabricarse una sesión válida**. |
| `DATABASE_URL` | Conexión a Postgres de Supabase (usa el "Transaction pooler", puerto 6543). Sin ella cae a sqlite, que en Render se pierde en cada redeploy. |
| `GPS_APIKEY`, `GPS_USERNAME`, `GPS_PASSWORD` | Acceso al WebService. |
| `DASHBOARD_CLAVE_ADMIN`, `DASHBOARD_CLAVE_PROCAPS`, `DASHBOARD_CLAVE_DITAR`, `DASHBOARD_CLAVE_RELIANZ` | Solo las lee `seed_dashboard_usuarios`, una vez. Después de correrlo se pueden quitar: las claves reales ya viven hasheadas en la base. |

## Licencia

MIT
