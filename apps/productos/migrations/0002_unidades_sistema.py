from django.db import migrations

from ._datos import UNIDADES


def crear(apps, schema_editor):
    U = apps.get_model("productos", "UnidadMedida")
    for codigo, nombre, tipo, factor in UNIDADES:
        U.objects.update_or_create(empresa=None, codigo=codigo, defaults={
            "nombre": nombre, "tipo": tipo, "factor_referencia": factor, "es_sistema": True})


class Migration(migrations.Migration):
    dependencies = [("productos", "0001_initial")]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
