"""Registro de modelos de tracking en el admin de Django.

Aquí es donde se dan de alta las personas que entran al dashboard: una fila
por persona, sin tocar el código y sin volver a desplegar.
"""

from django import forms
from django.contrib import admin

from .models import EMPRESAS_ASIGNABLES, DashboardUsuario


class DashboardUsuarioForm(forms.ModelForm):
    """Formulario del admin para una cuenta del dashboard.

    Hace dos cosas que el formulario de fábrica no haría bien: recibe la
    clave en claro y la guarda hasheada (el modelo solo tiene el hash), y
    pinta las empresas como casillas en vez de pedir que alguien escriba
    'PROCAPS,DITAR' a mano y sin erratas.
    """

    clave = forms.CharField(
        label='Clave', required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Obligatoria al crear la cuenta. Al editarla, déjala en '
                  'blanco para conservar la que ya tiene.',
    )
    empresas = forms.MultipleChoiceField(
        label='Empresas', required=False,
        choices=[(e, e) for e in EMPRESAS_ASIGNABLES],
        widget=forms.CheckboxSelectMultiple,
        help_text='Los viajes que ve. Se ignora si marcas «acceso total».',
    )

    class Meta:
        model = DashboardUsuario
        fields = ('correo', 'nombre', 'acceso_total', 'empresas', 'activo')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El modelo guarda 'PROCAPS,DITAR'; las casillas quieren una lista.
        if self.instance.pk and self.instance.empresas:
            self.initial['empresas'] = [
                e.strip().upper()
                for e in self.instance.empresas.split(',') if e.strip()
            ]

    def clean_empresas(self):
        """Devuelve las casillas marcadas como la cadena que guarda el modelo."""
        return ','.join(self.cleaned_data.get('empresas') or [])

    def clean(self):
        datos = super().clean()
        if not self.instance.pk and not datos.get('clave'):
            self.add_error('clave', 'Ponle una clave, si no la cuenta no entra.')
        if not datos.get('acceso_total') and not datos.get('empresas'):
            self.add_error(
                'empresas',
                'Marca al menos una empresa, o dale acceso total: sin nada '
                'marcado la cuenta entraría a un dashboard vacío.')
        return datos

    def save(self, commit=True):
        usuario = super().save(commit=False)
        clave = self.cleaned_data.get('clave')
        if clave:
            usuario.set_clave(clave)
        if commit:
            usuario.save()
        return usuario


@admin.register(DashboardUsuario)
class DashboardUsuarioAdmin(admin.ModelAdmin):
    """Alta y baja de las cuentas del dashboard."""

    form = DashboardUsuarioForm
    list_display = ('correo', 'nombre', 'que_ve', 'activo', 'ultimo_ingreso')
    list_filter = ('activo', 'acceso_total')
    search_fields = ('correo', 'nombre')
    readonly_fields = ('creado_en', 'ultimo_ingreso')
    fields = ('correo', 'nombre', 'clave', 'acceso_total', 'empresas',
              'activo', 'creado_en', 'ultimo_ingreso')

    @admin.display(description='qué ve')
    def que_ve(self, usuario):
        if usuario.acceso_total:
            return 'Todo, y el mapa de flota'
        return usuario.empresas or '—'
