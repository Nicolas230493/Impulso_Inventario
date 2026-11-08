"""Menú lateral. Un ítem se muestra solo si su URL existe, su módulo está activo y el usuario tiene permiso."""

from django.urls import NoReverseMatch, reverse

MENU = [
    ("Inicio", [("Tablero", "nucleo:inicio", None, None, "speedometer2")]),
    (
        "Inventario",
        [
            ("Productos", "productos:lista", "inventario", "productos.ver", "box-seam"),
            ("Depósitos", "depositos:lista", "inventario", "inventario.ver", "building"),
            ("Stock", "inventario:stock", "inventario", "inventario.ver", "boxes"),
            ("Movimientos", "inventario:movimientos", "inventario", "inventario.ver", "arrow-left-right"),
            ("Kardex", "inventario:kardex_buscar", "inventario", "inventario.ver", "journal-text"),
        ],
    ),
    (
        "Mercadería",
        [
            ("Búsqueda rápida", "mercaderia:buscar", "inventario", "productos.ver", "search"),
            ("Alta por escaneo", "mercaderia:alta_rapida", "inventario", "productos.administrar", "upc-scan"),
            ("Precios", "mercaderia:precios", "inventario", "productos.administrar", "currency-dollar"),
            ("Listas de proveedores", "mercaderia:listas", "inventario", "productos.administrar", "filetype-csv"),
            ("Etiquetas", "mercaderia:etiquetas", "inventario", "productos.ver", "printer"),
            ("Vencimientos", "mercaderia:vencimientos", "inventario", "inventario.ver", "calendar-x"),
            ("Sugerencia de pedido", "mercaderia:sugerencia", "inventario", "compras.ver", "lightbulb"),
            ("Piezas", "mercaderia:piezas", "inventario", "inventario.ver", "rulers"),
            ("Por atributos", "mercaderia:atributos", "inventario", "productos.ver", "funnel"),
            ("Kits y combos", "kits:lista", "inventario", "inventario.ver", "basket"),
            ("Envases", "envases:saldos", "inventario", "inventario.ver", "recycle"),
            ("Números de serie", "series:buscar", "inventario", "inventario.ver", "upc"),
            ("Consignación", "consignacion:lista", "inventario", "compras.ver", "arrow-repeat"),
            ("Pedidos internos", "consumo:pedidos", "inventario", "inventario.ver", "clipboard"),
            ("Nivel par", "consumo:par", "inventario", "inventario.ver", "bar-chart-steps"),
            ("Bienes en circulación", "consumo:circulacion", "inventario", "inventario.ver", "arrow-clockwise"),
        ],
    ),
    (
        "Análisis",
        [
            ("Indicadores", "analisis:indicadores", "inventario", "reportes.ver", "clipboard-data"),
            ("Clasificación ABC", "analisis:abc", "inventario", "reportes.ver", "sort-down"),
            ("Conteo cíclico", "analisis:conteo", "inventario", "inventario.ver", "arrow-repeat"),
            ("Consumo por centro", "consumo:reporte", "inventario", "reportes.ver", "pie-chart"),
        ],
    ),
    (
        "Compras",
        [
            ("Proveedores", "proveedores:lista", "compras", "compras.ver", "truck"),
            ("Órdenes de compra", "compras:ordenes", "compras", "compras.ver", "cart"),
            ("Recepciones", "compras:recepciones", "compras", "compras.ver", "box-arrow-in-down"),
            ("Recepción por escaneo", "mercaderia:recepcion_escaneo_nueva", "compras", "compras.operar", "upc-scan"),
            ("Lotes", "lotes:lista", "compras", "inventario.ver", "tags"),
            ("Inventario físico", "compras:inventarios", "compras", "inventario_fisico.operar", "clipboard-check"),
            ("Ajustes pendientes", "inventario:pendientes", "inventario", "inventario.autorizar", "hourglass"),
        ],
    ),
    (
        "Producción",
        [
            ("Despiece", "desposte:inicio", "desposte", "desposte.operar", "scissors"),
            ("Bandeja supervisor", "desposte:bandeja", "desposte", "desposte.supervisar", "inbox"),
            ("Plantillas", "desposte:plantillas", "desposte", "desposte.administrar", "diagram-3"),
            ("Tablero de desposte", "desposte:tablero", "desposte", "reportes.ver", "bar-chart"),
            ("Reportes de desposte", "desposte:reportes", "desposte", "reportes.ver", "graph-up"),
            ("Piezas identificadas", "elaboracion:piezas", "produccion", "inventario.ver", "tag"),
            ("Recetas", "elaboracion:recetas", "produccion", "produccion.operar", "journal-richtext"),
            ("Elaboración", "elaboracion:ordenes", "produccion", "produccion.operar", "fire"),
            ("Costos de proceso", "elaboracion:costos", "produccion", "reportes.ver", "calculator"),
            ("Rentabilidad", "elaboracion:rentabilidad", "produccion", "reportes.ver", "cash-coin"),
        ],
    ),
    (
        "Trazabilidad",
        [
            ("Buscar lote", "trazabilidad:inicio", "trazabilidad", "calidad.ver", "diagram-2"),
            ("Retiros de producto", "trazabilidad:retiros", "trazabilidad", "calidad.ver", "exclamation-octagon"),
        ],
    ),
    (
        "Calidad",
        [
            ("Tablero de calidad", "calidad:inicio", "calidad", "calidad.ver", "patch-check"),
            ("Alertas", "calidad:alertas", "calidad", "calidad.ver", "bell"),
            ("Inspecciones", "calidad:plantillas", "calidad", "calidad.ver", "list-check"),
            ("No conformidades", "calidad:no_conformidades", "calidad", "calidad.ver", "x-octagon"),
            ("Limpieza (POES)", "calidad:limpieza", "calidad", "calidad.ver", "droplet"),
            ("Documentación", "calidad:documentos", "calidad", "calidad.ver", "file-earmark-text"),
            ("Actividades declaradas", "calidad:actividades", "calidad", "calidad.ver", "building-check"),
        ],
    ),
    (
        "Cadena de frío",
        [
            ("Cámaras", "cadena_frio:inicio", "cadena_frio", "calidad.ver", "thermometer-snow"),
            ("Registros", "cadena_frio:registros", "cadena_frio", "calidad.ver", "thermometer-half"),
            ("Perfiles", "cadena_frio:perfiles", "cadena_frio", "calidad.administrar", "sliders"),
        ],
    ),
    ("Ventas", [("Mostrador", "ventas:mostrador", "ventas", None, "shop")]),
    (
        "Configuración",
        [
            ("Empresa", "empresas:datos", None, "empresa.administrar", "gear"),
            ("Sucursales", "empresas:sucursales", None, "empresa.administrar", "geo-alt"),
            ("Usuarios", "empresas:usuarios", None, "usuarios.administrar", "people"),
            ("Roles", "empresas:roles", None, "usuarios.administrar", "person-badge"),
            ("Módulos", "empresas:modulos", None, "empresa.administrar", "toggles"),
            ("Auditoría", "auditoria:lista", None, "auditoria.ver", "shield-check"),
        ],
    ),
]


# Si la empresa declaró actividades (5.5), estas opciones se muestran solo con alguna de las indicadas.
POR_ACTIVIDAD = {
    "desposte:": {"despiece_aves", "despostadero"},
    "elaboracion:recetas": {"elaboracion", "fraccionamiento"},
    "elaboracion:ordenes": {"elaboracion", "fraccionamiento"},
}


def _habilitado(url_name, declaradas):
    if not declaradas:
        return True
    for prefijo, requeridas in POR_ACTIVIDAD.items():
        if url_name.startswith(prefijo):
            return bool(requeridas & declaradas)
    return True


def menu_visible(request):
    from apps.empresas.services import actividades

    declaradas = actividades() if getattr(request, "empresa", None) else set()
    resultado = []
    for seccion, items in MENU:
        visibles = []
        for nombre, url_name, modulo, permiso, icono in items:
            if modulo and modulo not in request.modulos:
                continue
            if permiso and not request.membresia.tiene_permiso(permiso):
                continue
            if not _habilitado(url_name, declaradas):
                continue
            try:
                url = reverse(url_name)
            except NoReverseMatch:
                continue  # todavía no implementado
            activo = request.path.startswith(url) and url != "/"
            visibles.append({"nombre": nombre, "url": url, "icono": icono, "activo": activo})
        if visibles:
            resultado.append({"seccion": seccion, "items": visibles})
    return resultado
