# Generated manually: los usuarios del dashboard vuelven a vivir
# hardcodeados en settings.DASHBOARD_USUARIOS.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tracking', '0003_dashboardusuario_invitado_por_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='DashboardUsuario',
        ),
    ]
