# Impulso Inventario

Inventario con lotes y desposte para comercios de alimentos, construido según
`Impulso Inventario — Especificaciones por fases.md`.

**Estado:** Fases 0 a 5 y Extensión A (mercadería general multirubro, A1 a A11) implementadas, con criterios de
aceptación automatizados y pantallas. La Fase 4 se hizo en clave general de supermercado (no solo carnicería):
despiece en etapas con piezas identificadas, elaboración con recetas, costos de proceso y rentabilidad. La Fase 5 suma
trazabilidad completa, retiros de producto, cadena de frío, calidad y actividades declaradas.

## Levantar el sistema

```bash
cp .env.example .env            # solo para desarrollo local
docker compose up -d            # web, db, redis, worker, beat (migra solo al arrancar)
docker compose exec web python manage.py cargar_demo
```

Abrir http://localhost:8000 (o el puerto definido en `WEB_PORT`). La demo crea la «Pollería Demo» con
usuarios `dueno@`, `supervisor@`, `operario@` y `calidad@demo.local`, contraseña `demo-impulso-2026`, más una
recepción de 100 pollos y un desposte aprobado.

Demo completa de supermercado («Súper Impulso», ~30 min): `python manage.py cargar_supermercado` crea +700
productos, 2 sucursales, 14 proveedores y simula 30 días de compras, reposición, ventas, producción propia,
calidad, cadena de frío y trazabilidad. Usuarios `dueno@`, `encargado@`, `encargado.norte@`, `supervisor@`,
`carnicero@`, `panadero@`, `cajero@`, `calidad@` y `contador@super.local`, contraseña `demo-impulso-2026`.

Para cargar el catálogo de pollería en una empresa real: `python manage.py precargar_polleria <CUIT>`,
o el botón «Precargar catálogo de pollería» en Producción → Plantillas.

### Desarrollo sin Docker para la app

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
docker run -d --name impulso-inv-pg -e POSTGRES_USER=impulso -e POSTGRES_PASSWORD=impulso \
  -e POSTGRES_DB=impulso -p 55433:5432 postgres:16-alpine
.venv/bin/python manage.py migrate && .venv/bin/python manage.py runserver
```

### Calidad

```bash
.venv/bin/ruff check .
.venv/bin/pytest --create-db --cov
.venv/bin/coverage report --include="apps/*/services.py" --fail-under=85
```

El CI (`.github/workflows/ci.yml`) corre linter, chequeo de migraciones, tests y cobertura en cada push.
Para que un test fallido **bloquee el merge** hay que activar en GitHub la protección de rama
(Settings → Branches → «Require status checks to pass» con el check `tests`).

## Estructura

```
config/            settings (base, dev, prod, test), urls, celery, API
apps/nucleo/       ModeloEmpresa + EmpresaManager, numeradores, decimales, permisos, menú, exportación
apps/empresas/     Empresa, Sucursal, ConfiguracionEmpresa, ModuloEmpresa
apps/usuarios/     Usuario (email + MFA TOTP), Rol, Membresia
apps/auditoria/    EventoAuditoria
apps/productos/    Categoría, Marca, Unidades, Producto, conversiones, códigos de barras, importación CSV
apps/depositos/    Depósitos por sucursal
apps/inventario/   Kardex inmutable, Stock materializado, costo promedio ponderado, reservas, conciliación
apps/lotes/        Lotes, FEFO, bloqueo/liberación, vencimientos
apps/proveedores/  Proveedores y precios por producto
apps/compras/      Órdenes de compra, recepciones, costos adicionales, devoluciones, inventario físico
apps/produccion/   Motor genérico de transformación (N entradas → N salidas)
apps/desposte/     Configuración de pollería, pantallas de operario y supervisor, tablero y reportes
apps/mercaderia/   Ext. A1/A2/A6/A10: presentaciones, precios y reglas, listas de proveedor, variantes, piezas, atributos
apps/analisis/     Ext. A11: clasificación ABC, conteo cíclico, indicadores
apps/kits/         Ext. A4: combos virtuales y kits armados
apps/envases/      Ext. A5: envases retornables y saldos por tercero
apps/series/       Ext. A3: números de serie, garantía y service
apps/consumo/      Ext. A8/A9: centros de costo, pedidos internos, nivel par, bienes en circulación
apps/consignacion/ Ext. A7: consignación recibida y entregada, liquidaciones
apps/elaboracion/  Fase 4: recetas versionadas, órdenes de elaboración, precarga de supermercado, cierre mensual y
                   rentabilidad (las piezas identificadas y los costos de proceso viven en el motor de producción)
apps/trazabilidad/ Fase 5: grafo de lotes (WITH RECURSIVE), consultas atrás/interna/adelante, informe PDF, retiros
apps/cadena_frio/  Fase 5: perfiles de conservación, cámaras, registros de temperatura, acción correctiva
apps/calidad/      Fase 5: plantillas e inspecciones, no conformidades, limpieza POES, documentos, alertas periódicas
apps/clientes/     Clientes (usados por envases, series y consignación)
tests/             pytest: un archivo por fase + vistas; test_ext_* para la Extensión A
```

## Convenciones transversales implementadas

| Convención | Dónde |
| --- | --- |
| Multiempresa: todo modelo hereda de `ModeloEmpresa`; sin empresa activa, las consultas lanzan error | `apps/nucleo/models.py`, `middleware.py` |
| Decimales con precisión fija, `ROUND_HALF_UP`, prorrateo con el redondeo en la línea mayor | `apps/nucleo/decimales.py` |
| Kardex inmutable: el stock solo cambia por `MovimientoStock` confirmado; corrección por contramovimiento | `apps/inventario/services.py` |
| `select_for_update` en orden fijo sobre Stock y costo (sin carreras ni deadlocks) | `_aplicar()` en inventario |
| Máquinas de estado explícitas y numeración correlativa con bloqueo | `apps/nucleo/estados.py`, `services.py` |
| Lógica solo en `services.py`; vistas, API y tareas llaman a los mismos servicios | todas las apps |
| Auditoría con motivo obligatorio; historial de maestros con `django-simple-history` | `apps/auditoria` |
| Módulos activables por empresa (menú, vistas y API) | `ModuloEmpresa`, `requiere_modulo` |
| UTC en base, `America/Argentina/Buenos_Aires` y formato 1.234,567 en pantalla | settings, filtros `num`, `kg`, `pesos` |

Tareas programadas (Celery Beat): lotes vencidos (00:05) y conciliación de stock contra Kardex (03:00).

## Decisiones tomadas al implementar

- **Costo promedio:** por producto y empresa para productos sin lote; para productos con lote, cada lote tiene su
  propio costo (la recepción lo fija y las devoluciones e inventarios salen a ese costo).
- **Revertir una entrada** retira su valor del promedio, así el costo vuelve exactamente al anterior.
- **Merma en el desposte:** las categorías con imputación «absorbe» (goteo, limpieza, huesos y grasa descartados)
  más los residuos forman la merma real; lo que excede el estándar de la plantilla es merma anormal y va a pérdida
  del período al costo medio de entrada. Las categorías «pérdida» (contaminado, vencimiento) van completas a pérdida.
  «Pendiente de revisión» impide aprobar.
- **Doble firma:** si algún corte sale del rendimiento estándar ± tolerancia, la aprobación exige comentario y la
  firma de un segundo supervisor (distinto del primero); recién ahí se actualiza el stock.
- **Reversión de desposte:** se rechaza si algún lote resultante tuvo cualquier movimiento posterior (venta, consumo
  o transferencia) o tiene reservas; en ese caso corresponde un ajuste autorizado.
- **Plantillas:** cada edición de rendimientos o precios crea una versión nueva; solo la última está activa.
- **Recepción:** la orden de compra queda «Recibida» cuando se completan las unidades pedidas (o los kg, si la línea
  no tiene unidades).

### Fase 4 (general, estilo supermercado)

- **Piezas identificadas (`UnidadOrigen`):** cualquier pieza de un lote (media res, res porcina, horma) se registra
  con identificador y peso. En el despiece se elige la pieza; las plantillas con «genera pieza» (cuarteo) crean piezas
  hijas que siguen a la etapa siguiente. El rendimiento se mide por pieza raíz a lo largo de todas sus etapas y cada
  lote resultante conoce su pieza de origen, aunque pase por una receta.
- **Elaboración:** usa el mismo motor (`OrdenTransformacion` tipo elaboración) con la receta congelada en la orden.
  Propone teórico × tandas; se carga lo real, se permite el sustituto que define la receta y un desvío fuera de
  tolerancia exige comentario. Los insumos salen por FEFO y el lote elaborado registra todos los lotes usados.
  El costo se reparte entre producto y subproducto por valor de venta.
- **Costos de proceso:** conceptos directos o indirectos con tarifa por orden, kg, hora o unidad, por sucursal y tipo
  de transformación; se toma la vigente más específica y se suma al costo a repartir. Los empaques con stock entran
  como insumos de la orden (descuentan stock, no entran al balance de peso). El cierre mensual compara indirectos
  tarifados contra reales sin recalcular órdenes.
- **Rentabilidad:** margen bruto por producto contra el precio de lista sin IVA ni impuesto interno, con alerta bajo
  el umbral de la empresa (`umbral_margen_bajo`).
- **Precarga «supermercado»:** vacuno en dos etapas, porcino, fraccionado de horma, recetas de pan, milanesas, chorizo
  y queso rallado, conceptos de costo y categorías de merma. El rubro supermercado/carnicería/restaurante activa el
  módulo Producción.

### Fase 5

- **Trazabilidad:** los lotes son nodos y `LoteOrigen` las aristas; se recorre con `WITH RECURSIVE` filtrando por
  empresa. Hacia atrás muestra proveedor, recepción, remito, temperatura y órdenes; interna, entradas, salidas y mermas
  de cada proceso; hacia adelante, cada derivado con su stock por sucursal y depósito y sus movimientos. Informe PDF.
  Se prueba con 510 derivados en 3 niveles por debajo de 2 segundos.
- **Retiros:** iniciar un retiro bloquea en una transacción el lote y todos sus derivados en todas las sucursales;
  ninguna salida posterior puede usarlos (solo el descarte por retiro o la devolución al proveedor). Las acciones
  (descarte, devolución, liberación por Calidad con motivo, notas) quedan registradas, y el retiro solo se cierra
  cuando no queda stock retenido. Un lote en retiro no se libera por fuera del retiro.
- **Cadena de frío:** los perfiles se precargan solo con las referencias citadas (ANMAT/SENASA) y quedan «sin
  confirmar» hasta que la empresa los revisa. Un registro fuera de rango crea alerta, exige acción correctiva y, si la
  empresa lo activa (`bloquear_lotes_por_temperatura`), bloquea los lotes de la cámara.
- **Alertas** (`apps/nucleo/alertas.py`): sin duplicados por hecho, en el tablero y por email a quien tenga
  `calidad.ver`. Celery Beat revisa cada hora cámaras sin registro, documentos que vencen en 30 días y lotes
  bloqueados o vencidos con stock.
- **Calidad:** un ítem crítico que falla hace la inspección no conforme y abre una no conformidad con los lotes del
  objeto inspeccionado; una NC no se cierra sin acción correctiva y verificación. La limpieza la verifica otra persona.
- **Actividades declaradas (5.5):** si la empresa declara actividades, el menú oculta despiece o elaboración cuando no
  corresponden, y con «despiece de aves» cada orden exige la temperatura del sector (queda registrada y se compara con
  el perfil de sala). El sistema nunca muestra a la empresa como habilitada o certificada.
- Los rubros de alimentos activan por defecto los módulos Trazabilidad, Calidad y Cadena de frío.

## Pendiente o fuera de lo implementado

- **Row Level Security de PostgreSQL** (segunda capa recomendada, convención 1.1): no está implementada; el
  aislamiento se apoya en `EmpresaManager`, el middleware y las pruebas de acceso cruzado por pantalla y API.
- Lectura directa de balanza: la interfaz `FuentePeso` está lista (`apps/produccion/services.py`); el agente local
  es parte de la Fase 7.
- Extensión A: la recepción por escaneo de bultos y las pantallas de compra/transferencia/conteo en matriz
  talle × color (A2) usan las pantallas generales; la matriz se ve en Variantes del producto.
- Fase 4: la carga de empaques como insumo de un despiece existe en el servicio (`agregar_entrada(..., es_insumo=True)`)
  pero todavía no tiene botón en la pantalla de la orden de despiece; las tarifas no se editan (se agregan nuevas).
- Fase 5: la trazabilidad llega hasta sucursal y depósito; las ventas a cada cliente se completan con la Fase 6.
  Los sensores automáticos de temperatura y los avisos por WhatsApp son de la Fase 7.
- Fases 6 a 8 (ventas, integraciones y SaaS) no empezaron.
- Preguntas abiertas del anexo B (modo sin conexión, pollerías piloto, ARCA, hosting) siguen sin responder.
# Impulso_Inventario
