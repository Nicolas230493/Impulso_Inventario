from django.db import migrations


def crear_roles(apps, schema_editor):
    from apps.nucleo.permisos import ROLES_SISTEMA

    Rol = apps.get_model("usuarios", "Rol")
    for codigo, (nombre, permisos) in ROLES_SISTEMA.items():
        Rol.objects.update_or_create(empresa=None, codigo=codigo, defaults={"nombre": nombre, "permisos": permisos})


class Migration(migrations.Migration):
    dependencies = [("usuarios", "0001_initial")]
    operations = [migrations.RunPython(crear_roles, migrations.RunPython.noop)]
