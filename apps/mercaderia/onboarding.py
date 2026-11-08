"""Plantillas de onboarding por rubro (Extensión A). Todo lo precargado queda editable. Idempotente."""

from decimal import Decimal as D

from django.db import transaction

from apps.depositos.models import Deposito
from apps.empresas.models import Sucursal
from apps.nucleo.contexto import empresa_requerida
from apps.productos.models import Categoria, Producto, UnidadMedida

from .models import AtributoCategoria, CurvaTalles, EjeVariante, ReglaPrecio

RUBROS = {
    "almacen": "Almacén / autoservicio",
    "distribuidora": "Distribuidora de bebidas",
    "ferreteria": "Ferretería / corralón",
    "indumentaria": "Indumentaria / calzado",
    "electronica": "Electrónica",
    "restaurante": "Restaurante",
    "hotel": "Hotel",
    "polleria": "Pollería",
    "supermercado": "Supermercado / autoservicio con producción propia",
}

# categoría, IVA %, markup %
CATEGORIAS_ALMACEN = [
    ("Almacén seco", "21", "30"),
    ("Bebidas", "21", "35"),
    ("Lácteos", "21", "25"),
    ("Fiambres", "21", "40"),
    ("Congelados", "21", "30"),
    ("Limpieza", "21", "35"),
    ("Perfumería", "21", "40"),
    ("Golosinas", "21", "45"),
]


def _deposito(nombre, tipo, **kw):
    d = Deposito.objects.filter(nombre=nombre).first()
    if d is None:
        d = Deposito(sucursal=Sucursal.objects.filter(activa=True).first(), nombre=nombre, tipo=tipo, **kw)
        d.save()
    return d


def _categoria(nombre, padre=None):
    c, _ = Categoria.objects.get_or_create(nombre=nombre, padre=padre, defaults={"empresa": empresa_requerida()})
    return c


def _regla_categoria(categoria, markup, redondeo="10"):
    if not ReglaPrecio.objects.filter(alcance="categoria", categoria=categoria).exists():
        ReglaPrecio(alcance="categoria", categoria=categoria, markup=D(markup), redondeo=redondeo).save()


def _producto(codigo, nombre, unidad="un", **kw):
    p = Producto.objects.filter(codigo=codigo).first()
    if p is None:
        p = Producto(
            codigo=codigo, nombre=nombre, unidad_base=UnidadMedida.objects.get(empresa=None, codigo=unidad), **kw
        )
        p.save()
    return p


def _eje(nombre, valores):
    e, _ = EjeVariante.objects.get_or_create(
        nombre=nombre, defaults={"valores": valores, "empresa": empresa_requerida()}
    )
    return e


def _centros(nombres):
    from apps.consumo.models import CentroCosto

    suc = Sucursal.objects.filter(activa=True).first()
    for n in nombres:
        CentroCosto.objects.get_or_create(nombre=n, defaults={"sucursal": suc, "empresa": empresa_requerida()})


def _nivel_par(producto, sector, cantidad):
    from apps.consumo.models import NivelPar

    NivelPar.objects.get_or_create(
        producto=producto, deposito=sector, defaults={"cantidad": D(cantidad), "empresa": empresa_requerida()}
    )


def _bien(producto, valor, **kw):
    from apps.consumo.models import BienCirculante
    from apps.consumo.services import definir_bien

    if not BienCirculante.objects.filter(producto=producto).exists():
        definir_bien(producto, D(valor), **kw)


@transaction.atomic
def precargar_rubro(rubro: str) -> list[str]:
    if rubro not in RUBROS:
        raise ValueError(f"Rubro desconocido: {rubro}")
    hecho = []
    if rubro == "supermercado":
        from apps.elaboracion.precarga import precargar_supermercado

        return precargar_rubro("almacen") + precargar_supermercado()
    if rubro == "almacen":
        for nombre, _iva, markup in CATEGORIAS_ALMACEN:
            _regla_categoria(_categoria(nombre), markup)
        _deposito("Salón", "mostrador", permite_venta=True)
        _deposito("Depósito", "general")
        from apps.produccion.models import CategoriaMerma

        for n in ("Rotura", "Vencimiento"):
            CategoriaMerma.objects.get_or_create(
                nombre=n, defaults={"imputacion": "perdida", "empresa": empresa_requerida()}
            )
        hecho = ["8 categorías con IVA y markup", "depósitos Salón y Depósito", "mermas de rotura y vencimiento"]
    elif rubro == "distribuidora":
        _producto("ENV-CAJON", "Cajón plástico", es_retornable=True, valor_deposito=D("8000"))
        _producto("ENV-BOTELLON", "Botellón 20 l", es_retornable=True, valor_deposito=D("6000"))
        _producto("ENV-BARRIL", "Barril 50 l", es_retornable=True, valor_deposito=D("150000"))
        _producto("ENV-PALLET", "Pallet", es_retornable=True, valor_deposito=D("25000"))
        _deposito("Envases", "envases")
        hecho = ["envases retornables (cajón, botellón, barril, pallet)", "depósito de envases"]
    elif rubro == "ferreteria":
        tornillos = _categoria("Tornillos")
        for nombre, tipo, unidad, valores in [
            ("Diámetro", "numero", "mm", []),
            ("Largo", "numero", "mm", []),
            ("Material", "lista", "", ["acero", "acero inoxidable", "bronce"]),
            ("Rosca", "lista", "", ["madera", "métrica", "autoperforante"]),
        ]:
            AtributoCategoria.objects.get_or_create(
                categoria=tornillos,
                nombre=nombre,
                defaults={"tipo": tipo, "unidad": unidad, "valores": valores, "empresa": empresa_requerida()},
            )
        cables = _categoria("Cables")
        _producto(
            "CAB-2.5",
            "Cable unipolar 2,5 mm (por metro)",
            unidad="m",
            categoria=cables,
            vende_por_medida=True,
            controla_piezas=True,
            venta_minima=D("0.5"),
            multiplo_venta=D("0.5"),
            umbral_retazo=D("2"),
        )
        herramientas = _categoria("Herramientas en préstamo")
        for codigo, nombre, valor in [
            ("HER-TALADRO", "Taladro percutor", "120000"),
            ("HER-AMOLADORA", "Amoladora", "95000"),
        ]:
            _bien(_producto(codigo, nombre, categoria=herramientas), valor)
        hecho = [
            "atributos de tornillos (diámetro, largo, material, rosca)",
            "cable por metro con control de rollos",
            "herramientas en préstamo",
        ]
    elif rubro == "indumentaria":
        talle = _eje("talle", ["XS", "S", "M", "L", "XL", "XXL"])
        _eje("color", ["Negro", "Blanco", "Azul", "Rojo", "Gris"])
        numero = _eje("número", [str(n) for n in range(35, 46)])
        for nombre, eje, cant in [
            ("Curva adulto S-XL", talle, {"S": 1, "M": 2, "L": 2, "XL": 1}),
            ("Curva calzado 38-42", numero, {"38": 1, "39": 2, "40": 3, "41": 2, "42": 1}),
        ]:
            CurvaTalles.objects.get_or_create(
                nombre=nombre, defaults={"eje": eje, "cantidades": cant, "empresa": empresa_requerida()}
            )
        hecho = ["ejes talle, color y número", "curvas de talles estándar"]
    elif rubro == "electronica":
        _deposito("En reparación", "reparacion")
        for codigo, nombre, cat, formato in [
            ("CEL-GENERICO", "Celular (modelo a definir)", "Celulares", r"^\d{15}$"),  # IMEI: 15 dígitos
            ("TV-GENERICO", "Televisor (modelo a definir)", "Televisores", ""),
            ("HEL-GENERICO", "Heladera (modelo a definir)", "Electrodomésticos", ""),
        ]:
            _producto(
                codigo, nombre, categoria=_categoria(cat), controla_serie=True, meses_garantia=12, formato_serie=formato
            )
        hecho = [
            "depósito «En reparación»",
            "productos modelo con serie (celular con formato IMEI) y 12 meses de garantía",
        ]
    elif rubro == "restaurante":
        _centros(["Cocina", "Salón", "Barra"])
        barra = _deposito("Barra", "sector")
        for codigo, nombre, valor in [
            ("VAJ-PLATO", "Plato playo", "4500"),
            ("VAJ-COPA", "Copa de vino", "3500"),
            ("MAN-MANTEL", "Mantel", "12000"),
        ]:
            _bien(_producto(codigo, nombre), valor)
        for codigo, nombre, par in [
            ("INS-SERVILLETA", "Servilletas (paquete)", "20"),
            ("INS-SORBETE", "Sorbetes (caja)", "4"),
        ]:
            _nivel_par(_producto(codigo, nombre, uso_interno=True), barra, par)
        hecho = [
            "centros de costo cocina, salón y barra",
            "sector Barra con niveles par de insumos",
            "vajilla y mantelería en circulación",
        ]
    elif rubro == "hotel":
        _centros(["Habitaciones", "Lavandería", "Desayuno"])
        office = _deposito("Office de piso", "sector")
        for codigo, nombre, valor in [("BLA-SABANA", "Sábana 2 plazas", "15000"), ("BLA-TOALLA", "Toalla", "9000")]:
            _bien(_producto(codigo, nombre), valor)
        amenities = []
        for codigo, nombre, par in [("AME-SHAMPOO", "Shampoo amenity", "50"), ("AME-JABON", "Jabón amenity", "50")]:
            p = _producto(codigo, nombre, uso_interno=True)
            _nivel_par(p, office, par)
            amenities.append(p)
        kit = _producto("KIT-BIENVENIDA", "Kit de bienvenida")
        if not kit.tipo_kit:
            from apps.kits.services import definir_kit

            definir_kit(kit, "armado", [(p, D("1")) for p in amenities])
        hecho = [
            "centros de costo habitaciones, lavandería y desayuno",
            "blancos en circulación",
            "amenities con nivel par en el office de piso",
            "kit de bienvenida armado",
        ]
    elif rubro == "polleria":
        from apps.desposte.services import precargar_polleria

        precargar_polleria()
        _producto("ENV-CAJON-POLLO", "Cajón de pollo", es_retornable=True, valor_deposito=D("7000"))
        hecho = ["catálogo de cortes, plantilla de desposte y categorías de merma", "cajón de pollo retornable"]
    return hecho
