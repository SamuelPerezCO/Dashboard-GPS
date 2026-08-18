"""Alta de DashboardUsuario en /admin/ para crear y editar cuentas del sitio."""

from django import forms
from django.contrib import admin

from .models import DashboardUsuario


class DashboardUsuarioForm(forms.ModelForm):
    """Pide la clave en texto plano y la hashea al guardar.

    En blanco al editar deja la clave actual sin tocar; en un usuario nuevo
    es obligatoria.
    """

    clave = forms.CharField(
        label='Clave', required=False, widget=forms.PasswordInput,
        help_text='Déjalo en blanco para no cambiar la clave existente.',
    )

    class Meta:
        model = DashboardUsuario
        fields = ('usuario', 'empresas', 'activo', 'puede_invitar', 'invitado_por')

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk and not cleaned.get('clave'):
            raise forms.ValidationError('La clave es obligatoria para un usuario nuevo.')
        return cleaned

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
    form = DashboardUsuarioForm
    list_display = ('usuario', 'empresas', 'activo', 'puede_invitar', 'invitado_por', 'creado_en')
    list_filter = ('activo', 'puede_invitar')
    search_fields = ('usuario',)
