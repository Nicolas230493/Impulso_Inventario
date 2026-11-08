# Impulso Inventario — Especificaciones por fases

Nov 7, 2026 · @Nicolás

## 0. Alcance y decisiones asumidas

El proyecto se construye en 9 fases (0 a 8): primero un inventario sólido con lotes, después el desposte de pollo como primer proceso productivo, y recién entonces vacuno, porcino y gastronomía sobre el mismo motor de transformación.

Cada fase se especifica con el mismo formato: objetivo, alcance (incluye / no incluye), modelos de datos, reglas de negocio, servicios, API, pantallas, permisos, criterios de aceptación y pruebas. Una fase se da por terminada solo cuando pasan todos sus criterios de aceptación.

### Decisiones tomadas (del punto 22 de la investigación)

| Decisión | Valor adoptado | Origen |
| --- | --- | --- |
| Mercado | Núcleo universal con especialización alimentaria | Recomendación del documento |
| Procesos de transformación | Desposte de pollo, vacuno y porcino; producción gastronómica | Casillas marcadas |
| Fuera del alcance inicial | Fraccionamiento y envasado; producción industrial | Casillas sin marcar (el motor genérico los admite más adelante) |
| Relación con Impulso Smart | Producto independiente de la familia Impulso, con integración opcional | Recomendación del documento |
| Dónde funciona | 100% nube (SaaS) + agente local liviano solo para balanzas | **Asumido**: la pregunta quedó sin responder |

### Cambios respecto del plan de etapas original

1. **Los lotes pasan a la Fase 2.** Una orden de desposte debe vincular lotes consumidos con lotes resultantes; sin lotes desde la recepción, ese vínculo no existe. La Fase 5 amplía la trazabilidad, pero la base nace con las compras.
2. **La Fase 3 cubre solo pollo.** El motor se valida con un caso real antes de sumar vacuno (que tiene etapas intermedias: media res → cuartos → cortes), porcino y recetas.
3. **El modelo de transformación es genérico desde el día uno.** Se programa una sola `OrdenTransformacion` con N entradas y N salidas; el desposte de pollo es su primera configuración, no un módulo aparte.
4. **El costo promedio ponderado entra en la Fase 1.** Sin costo de stock no hay costo de desposte.
5. **Se agrega una Fase 0 técnica** (repositorio, Docker, CI, multiempresa, usuarios) para que las fases funcionales no arrastren deuda.
6. **Merma normal vs. anormal.** En el ejemplo de costos del punto 5.1, los $30.000 de pérdida quedan fuera de los productos. La especificación distingue: la merma dentro del estándar se absorbe en el costo de los cortes; la que excede el estándar se imputa como pérdida del período (detalle en Fase 3).

## 1. Convenciones transversales (aplican a todas las fases)

Estas reglas se implementan en la Fase 0 y ninguna fase posterior puede romperlas.

### 1.1 Multiempresa

- Base única PostgreSQL, esquema compartido. Todo modelo de negocio hereda de `ModeloEmpresa` (FK `empresa`, `creado_en`, `creado_por`, `actualizado_en`).
- Un middleware fija la empresa activa del usuario; un `EmpresaManager` filtra todas las consultas por esa empresa. Acceder sin empresa activa lanza error, nunca devuelve todo.
- Segunda capa recomendada: Row Level Security de PostgreSQL con `SET app.empresa_id` por conexión.
- Prueba obligatoria en cada módulo: un usuario de la empresa A no puede leer, listar ni modificar datos de la empresa B (ni por vista, ni por API, ni por ID adivinado).

### 1.2 Cantidades, dinero y redondeo

| Tipo de dato | Campo Django | Precisión |
| --- | --- | --- |
| Peso / cantidad | `DecimalField(max_digits=14, decimal_places=3)` | Hasta el gramo |
| Importe | `DecimalField(max_digits=18, decimal_places=2)` | Centavos |
| Costo unitario | `DecimalField(max_digits=18, decimal_places=4)` | Evita arrastre en prorrateos |
| Porcentaje | `DecimalField(max_digits=7, decimal_places=4)` | 0,0001 % |

- Prohibido `float` en cálculos. Redondeo `ROUND_HALF_UP`.
- En todo prorrateo, la diferencia de redondeo se asigna a la línea de mayor importe, para que la suma cierre exacta.

### 1.3 Kardex inmutable

- El stock solo cambia mediante un `MovimientoStock` confirmado. Cabecera (tipo, fecha, documento de origen, usuario, motivo) + líneas (producto, lote, depósito, cantidad con signo, costo unitario).
- La tabla `Stock` (producto × depósito × lote) es un saldo materializado que se actualiza en la misma transacción, con `select_for_update()` sobre las filas afectadas.
- Un movimiento confirmado no se edita ni se borra. Se corrige con un contramovimiento que referencia al original.
- Tarea nocturna de conciliación: recalcula saldos desde el Kardex y alerta si difieren del saldo materializado.

### 1.4 Documentos con estado

- Toda orden (compra, recepción, transformación, venta) tiene una máquina de estados explícita. El estado nunca se edita directo: se cambia solo mediante funciones de servicio (`aprobar_orden()`, `cancelar_orden()`), que validan la transición y registran quién la hizo.
- Numeración correlativa por empresa, sucursal y tipo de documento, tomada con bloqueo para que no se repita con usuarios simultáneos.

### 1.5 Capa de servicios

- La lógica de negocio vive en `services.py` de cada app. Vistas web, API y tareas Celery llaman a los mismos servicios. Los modelos no tienen efectos colaterales en `save()`.
- Cada servicio que mueve stock corre dentro de `transaction.atomic()`.

### 1.6 Auditoría y permisos

- Maestros (productos, precios, plantillas, costos estándar) con historial de versiones (`django-simple-history`).
- Operaciones sensibles (ajustes, reversiones, aprobaciones, desbloqueos) generan un `EventoAuditoria` con usuario, IP, antes/después y motivo obligatorio.
- Permisos = rol por empresa + alcance por sucursal y depósito. Roles base: Dueño, Administrador, Encargado de sucursal, Supervisor de producción, Operario, Vendedor/Cajero, Calidad, Solo lectura.

### 1.7 Módulos activables

- Tabla `ModuloEmpresa` con los módulos activos por empresa (inventario, compras, desposte, producción, trazabilidad, calidad, cadena de frío, ventas, IA). Menú, vistas y API respetan esa tabla.

### 1.8 Fecha y hora

- Se guarda en UTC y se muestra en `America/Argentina/Buenos_Aires`. Formato de números argentino en pantalla (1.234,567 kg).

### 1.9 Calidad de código

- `pytest-django` + `factory_boy`. Cobertura mínima del 85% en `services.py`. `ruff` como linter. CI en GitHub Actions bloquea el merge si falla algo.

## 2. Mapa de fases

&#91;embedded content: mapa de fases · 9 fases, orden de ejecución\]

Al cerrar la Fase 3 se prueba con una o dos pollerías piloto antes de seguir. Las Fases 5 y 6 dependen de los lotes de la Fase 2, no de la 4, así que pueden desarrollarse en paralelo si hay dos personas.

## Fase 0 — Fundaciones técnicas

**Objetivo:** un esqueleto desplegable donde un usuario inicia sesión, elige su empresa y sucursal, y ve un menú que respeta sus permisos y módulos activos. No hay funcionalidad de negocio todavía.

### Alcance

**Incluye:** repositorio, Docker, CI, configuración por entornos, apps `empresas`, `usuarios`, `auditoria`, `nucleo` (clases base), plantilla visual base, login con MFA opcional.

**No incluye:** productos, stock ni ningún módulo funcional.

### Stack y estructura

- Python 3.12, Django 5.x, Django REST Framework, PostgreSQL 16, Redis, Celery + Celery Beat.
- Front: plantillas Django + Bootstrap 5 + **HTMX** (recomendado) para formularios dinámicos sin armar un SPA. JavaScript propio solo donde haga falta (pantalla de pesaje).
- `docker-compose` con servicios `web`, `db`, `redis`, `worker`, `beat`. Settings separados: `base.py`, `dev.py`, `prod.py`, variables por `.env`.

```
impulso_inventario/
├── config/            # settings, urls, celery
├── apps/
│   ├── nucleo/        # ModeloEmpresa, numeradores, utilidades decimales
│   ├── empresas/
│   ├── usuarios/
│   └── auditoria/
├── templates/
├── static/
└── tests/
```

### Modelos

| Modelo | Campos clave |
| --- | --- |
| `Empresa` | razón social, nombre de fantasía, CUIT, condición IVA, rubro, perfil de negocio, plan, activa |
| `Sucursal` | empresa, nombre, dirección, localidad, provincia, activa |
| `ConfiguracionEmpresa` | empresa, método de costeo, método de distribución de desposte, permite stock negativo (no por defecto), tolerancias |
| `ModuloEmpresa` | empresa, módulo, activo, desde |
| `Usuario` | modelo propio (`AbstractUser`) con email único, MFA activo |
| `Membresia` | usuario, empresa, rol, sucursales permitidas, activa |
| `Rol` | empresa (nulo = rol del sistema), nombre, permisos |
| `Numerador` | empresa, sucursal, tipo de documento, próximo número |
| `EventoAuditoria` | empresa, usuario, acción, objeto, datos antes/después (JSON), motivo, IP, fecha |

### Reglas de negocio

1. Un usuario puede pertenecer a varias empresas; la empresa activa se elige al iniciar sesión y se guarda en la sesión.
2. El CUIT se valida con dígito verificador.
3. Los roles de Dueño y Administrador exigen MFA a partir de la Fase 8 (opcional antes).
4. Ningún registro se borra físicamente desde la interfaz: se desactiva.

### Pantallas

- Login, selección de empresa/sucursal, recuperar contraseña, activar MFA.
- Layout con menú lateral del punto 13 de la investigación; los ítems de módulos inactivos no se muestran.
- Administración de empresa: datos, sucursales, usuarios, roles, módulos.

### Criterios de aceptación

- [ ] `docker compose up` levanta todo en una máquina limpia con un solo comando.
- [ ] CI corre linter y tests en cada push; un test fallido bloquea el merge.
- [ ] Un usuario sin membresía en la empresa B recibe 404 al pedir cualquier URL de la empresa B.
- [ ] Desactivar el módulo Desposte oculta el menú y devuelve 403 en sus URLs.
- [ ] Cambiar el rol de un usuario queda en `EventoAuditoria`.
- [ ] Dos solicitudes simultáneas al mismo `Numerador` nunca obtienen el mismo número (test con hilos).

## Fase 1 — Núcleo de inventario

**Objetivo:** un inventario funcional y valorizado. Cualquier comercio (incluso sin producción) puede cargar productos, depósitos, registrar entradas y salidas, transferir y consultar su Kardex con costo promedio.

### Alcance

**Incluye:** apps `productos`, `depositos`, `inventario`; costo promedio ponderado; dashboard básico; alertas de stock mínimo.

**No incluye:** compras formales con proveedor (Fase 2), lotes y vencimientos (Fase 2), inventario físico (Fase 2).

### Modelos

| Modelo | Campos clave |
| --- | --- |
| `Categoria` | nombre, padre (árbol: categoría → subcategoría) |
| `Marca` | nombre |
| `UnidadMedida` | código (kg, g, un, l, cajón), tipo (peso, volumen, unidad), es sistema |
| `Producto` | código interno, SKU, nombre, categoría, marca, **tipo** (simple / transformable), **clase** (materia prima, resultante, terminado, subproducto, empaque, insumo, residuo), unidad base, unidad de compra, unidad de venta, peso de referencia, controla lote, controla vencimiento, perfil de conservación (texto en Fase 1, FK en Fase 5), stock mínimo, stock máximo, precio de venta, activo |
| `ConversionUnidad` | producto, unidad origen, unidad destino, factor (ej.: 1 cajón = 20 kg) |
| `CodigoBarras` | producto, código, tipo (EAN-13, interno, peso variable) |
| `Deposito` | sucursal, nombre, tipo (general, cámara refrigerada, cámara congelados, sala de desposte, envases, mostrador), permite venta, permite producción, activo |
| `Stock` | producto, depósito, lote (nulo hasta Fase 2), cantidad, cantidad reservada, costo promedio |
| `TipoMovimiento` | código, nombre, signo (+/−/±), afecta costo, requiere motivo, requiere autorización |
| `MovimientoStock` | número, tipo, fecha, sucursal, documento origen (genérico), estado (borrador / confirmado / revertido), revierte a, usuario, autorizado por, motivo |
| `LineaMovimiento` | movimiento, producto, depósito, lote, cantidad (con signo, en unidad base), costo unitario, costo total |

Tipos de movimiento iniciales: entrada manual, salida manual, transferencia (sale + entra), consumo interno, merma simple, ajuste positivo, ajuste negativo, devolución de cliente (manual hasta Fase 6).

### Reglas de negocio

1. **Costo promedio ponderado** por producto y empresa. En cada entrada: nuevo costo = (stock actual × costo actual + cantidad entrada × costo entrada) ÷ (stock actual + cantidad entrada). Las salidas salen al costo promedio vigente y no lo modifican.
2. Toda cantidad se convierte a la unidad base del producto antes de grabar.
3. Stock negativo prohibido salvo que `ConfiguracionEmpresa.permite_stock_negativo` esté activo y el usuario tenga el permiso; en ese caso queda auditado.
4. Una transferencia es un solo `MovimientoStock` con dos líneas por producto y conserva el costo de origen.
5. Ajustes y mermas exigen motivo; los ajustes mayores a un umbral configurable ($ o %) requieren autorización de un rol superior.
6. Un producto con movimientos no se elimina; se desactiva. No se puede cambiar su unidad base una vez que tiene movimientos.

### Servicios principales

- `confirmar_movimiento(movimiento, usuario)` — valida, bloquea filas de `Stock`, aplica, recalcula costo, numera.
- `revertir_movimiento(movimiento, usuario, motivo)` — genera el contramovimiento.
- `transferir(origen, destino, lineas, usuario)`.
- `kardex(producto, deposito=None, desde, hasta)` — saldo inicial, movimientos con saldo acumulado y costo.

### API (DRF)

`/api/v1/productos/`, `/api/v1/depositos/`, `/api/v1/stock/` (solo lectura, filtros por depósito y producto), `/api/v1/movimientos/` (crear borrador + acción `confirmar` + acción `revertir`), `/api/v1/kardex/{producto_id}/`.

### Pantallas

Productos (listado con filtros, alta/edición, importación CSV) · Depósitos · Stock por depósito · Entradas · Salidas · Transferencias · Kardex por producto (exportable a Excel/CSV) · Dashboard: valor del inventario, productos sin stock, stock crítico, últimos movimientos.

### Criterios de aceptación

- [ ] Entrada de 100 kg a $3.000 y luego 50 kg a $3.300 → costo promedio $3.100,0000/kg, valor $465.000,00.
- [ ] Salida de 30 kg → stock 120 kg, costo sigue en $3.100/kg.
- [ ] Intentar sacar 200 kg con stock 120 kg → rechazado con mensaje claro.
- [ ] Una transferencia descuenta en origen y suma en destino en la misma transacción; si falla una línea, no se aplica ninguna.
- [ ] Revertir un movimiento deja el saldo y el costo como antes, y ambos movimientos se ven en el Kardex.
- [ ] Dos usuarios sacan a la vez los últimos 10 kg → uno lo logra y el otro recibe error de stock insuficiente.
- [ ] La conciliación nocturna no reporta diferencias tras 1.000 movimientos aleatorios de prueba.

## Fase 2 — Compras, recepción y lotes

**Objetivo:** que toda mercadería entre por una recepción pesada, con proveedor, lote, vencimiento y costo real (incluidos flete y otros gastos). Al cerrar esta fase, cada kilo en stock sabe de dónde vino.

### Alcance

**Incluye:** apps `proveedores`, `compras`, `lotes` (base); recepciones parciales; costos adicionales prorrateados; devoluciones a proveedor; inventario físico y ajustes con autorización; bloqueo de lotes vencidos.

**No incluye:** solicitud y comparación de proveedores (se deja como mejora posterior), factura fiscal de compra con ARCA, trazabilidad hacia adelante y retiros (Fase 5).

### Modelos

| Modelo | Campos clave |
| --- | --- |
| `Proveedor` | razón social, CUIT, condición IVA, contactos, dirección, habilitación sanitaria (número y vencimiento), calificación, activo |
| `ProductoProveedor` | proveedor, producto, código del proveedor, unidad de compra, último precio |
| `OrdenCompra` | número, proveedor, sucursal, depósito destino, fecha, fecha esperada, estado, observaciones |
| `LineaOrdenCompra` | orden, producto, cantidad pedida, unidad, precio unitario, cantidad recibida (calculada) |
| `Recepcion` | número, orden (opcional), proveedor, depósito, fecha y hora, remito del proveedor, temperatura de recepción, recibido por, estado |
| `LineaRecepcion` | recepción, producto, unidades (ej.: 100 pollos), peso declarado, peso recibido, precio unitario, resultado de control (aceptado / observado / rechazado), lote generado |
| `CostoAdicional` | recepción, concepto (flete, descarga, impuesto no recuperable, otro), importe, criterio de prorrateo (peso / valor / unidades) |
| `Lote` | código, producto, proveedor, recepción de origen u orden de transformación de origen, fecha de recepción, fecha de elaboración, fecha de vencimiento, cantidad inicial, estado (disponible / bloqueado / vencido / agotado), costo unitario de origen |
| `ReservaStock` | producto, depósito, lote, cantidad, documento que reserva, vigente |
| `DevolucionProveedor` | proveedor, recepción de origen, líneas, motivo, estado |
| `InventarioFisico` | depósito, fecha, tipo (total / parcial / cíclico), estado, responsable |
| `LineaInventarioFisico` | inventario, producto, lote, cantidad sistema (congelada al iniciar), cantidad contada, diferencia, motivo |

Estados de la orden de compra: Borrador → Enviada → Parcialmente recibida → Recibida → Cerrada (o Cancelada).

### Reglas de negocio

1. Se recibe y se valoriza **por el peso recibido**, no por el declarado. La diferencia declarado/recibido queda registrada y alimenta el reporte de diferencias por proveedor.
2. Una línea rechazada no entra a stock; una observada entra en un lote **bloqueado** hasta que alguien con rol Calidad o Encargado lo libere.
3. Costo de entrada del lote = (precio × peso + parte prorrateada de costos adicionales) ÷ peso recibido. Ejemplo: 250 kg a $3.000 + flete $25.000 → $3.100/kg.
4. Código de lote automático configurable (ej.: `POL-20261107-001`) o el lote del proveedor, a elección de la empresa.
5. Si el producto controla lote, todas sus salidas deben indicar lote. El sistema sugiere el lote por **FEFO** (primero en vencer, primero en salir).
6. Celery Beat marca como vencidos los lotes al pasar su fecha y los bloquea para venta y producción.
7. Inventario físico: al iniciar se congelan las cantidades del sistema; las diferencias generan un único movimiento de ajuste al aprobarse. Las diferencias valorizadas que superan el umbral requieren autorización.
8. Una devolución al proveedor sale al costo del lote original.

### Pantallas

Proveedores (ficha + historial) · Órdenes de compra · **Recepción** (pantalla optimizada para tablet: producto, unidades, peso, temperatura, control, foto del remito opcional) · Lotes (listado con vencimiento y estado) · Liberar/bloquear lote · Inventario físico (conteo por depósito, carga desde tablet) · Ajustes pendientes de autorización.

### Criterios de aceptación

- [ ] Orden de 100 pollos / 250 kg; se reciben 60 pollos / 151,200 kg → orden queda Parcialmente recibida, lote creado con 151,200 kg.
- [ ] Recepción de 250 kg a $3.000 con flete de $25.000 → lote con costo $3.100,0000/kg y stock valorizado en $775.000,00.
- [ ] Un lote observado no aparece como disponible para salidas hasta su liberación, que queda auditada.
- [ ] Con dos lotes que vencen el 10/11 y el 15/11, la salida sugiere el del 10/11.
- [ ] Un lote vencido no puede usarse en ninguna salida (salvo la de descarte por vencimiento).
- [ ] Inventario físico con faltante de 3,500 kg genera un ajuste negativo valorizado al costo del lote, con motivo y autorizante.

## Fase 3 — Desposte de pollo (MVP productivo)

**Objetivo:** una pollería desposta pollos enteros, pesa entrada y cortes, clasifica cada gramo de diferencia, y obtiene stock de cortes con lote propio y costo real por kilo. Es la fase que define el producto.

### Alcance

**Incluye:** apps `produccion` (motor genérico) y `desposte` (configuración y pantallas); plantillas de desposte de pollo; pesajes manuales individuales y agrupados; clasificación de mermas; distribución de costos por peso (método A) y por valor de venta (método B); lotes resultantes vinculados al origen; reversión; dashboard y reportes de rendimiento.

**No incluye:** vacuno, porcino y recetas (Fase 4); costos de mano de obra y energía (Fase 4); lectura directa de balanza (Fase 7, pero la interfaz se deja preparada).

### Modelos (motor genérico de transformación)

| Modelo | Campos clave |
| --- | --- |
| `PlantillaTransformacion` | nombre ("Desposte pollo estándar"), tipo (desposte / elaboración), producto de entrada, versión, vigente desde, activa |
| `LineaPlantilla` | plantilla, producto resultante, **tipo de resultado** (corte, subproducto, merma normal, residuo), rendimiento estándar %, tolerancia ± %, precio de referencia (método B), **criterio de costo** (participa / valor de recupero fijo / costo cero), genera lote, depósito destino por defecto, orden en pantalla |
| `OrdenTransformacion` | número, tipo, plantilla y versión (congeladas), sucursal, depósito origen, depósito destino, operario, supervisor, estado, inicio, fin, temperatura del sector, método de distribución (congelado al aprobar), observaciones |
| `EntradaTransformacion` | orden, producto, lote, unidades (cantidad de pollos), peso neto, costo unitario del lote |
| `Pesaje` | orden, línea (entrada o salida), secuencia, unidades, peso bruto, tara, peso neto, fuente (manual / balanza), dispositivo, usuario, hora |
| `SalidaTransformacion` | orden, producto, tipo de resultado, unidades, peso neto (suma de pesajes), lote generado, costo asignado, costo/kg |
| `CategoriaMerma` | nombre, aprovechable (sí/no), imputación (absorbe en productos / pérdida del período / pendiente) |
| `DiferenciaClasificada` | orden, categoría, peso, observación |
| `LoteOrigen` | lote resultante, lote de origen, orden, peso aportado |
| `ResultadoOrden` | snapshot al aprobar: rendimientos, mermas, diferencia, costos por línea, real vs estándar |

Categorías de merma iniciales (de la investigación): huesos no comercializables, grasa descartada, pérdida por limpieza, goteo, producto contaminado, diferencia de pesaje pendiente de revisión, vencimiento. Recortes, huesos y grasa aprovechables **no** son merma: se cargan como salidas de tipo subproducto.

Catálogo inicial de cortes (se precarga como productos de clase "resultante"): pechuga entera, pechuga deshuesada, filet de pechuga, pechuga con piel, muslo, pata, pata-muslo, pata-muslo deshuesado, ala entera, punta, ala segmentada, carcasa, espinazo, cuello, menudos, recortes. Cada empresa puede agregar los suyos.

### Estados de la orden

| Estado | Qué se hace | Efecto en stock |
| --- | --- | --- |
| Borrador | Se elige plantilla, lotes de origen, operario | Ninguno |
| Materia prima reservada | Se confirma cantidad y peso a usar | Crea `ReservaStock` sobre los lotes |
| En proceso | Se registran los pesajes de entrada | Ninguno (sigue reservado) |
| Pendiente de pesaje final | Se pesan los cortes obtenidos | Ninguno |
| Pendiente de control | Diferencia clasificada; el supervisor revisa | Ninguno |
| Aprobada | Supervisor aprueba (doble firma si excede tolerancias) | Se congelan costos y método |
| Stock actualizado | Un único `MovimientoStock` de transformación | Sale el lote de origen; entran los lotes resultantes; se registran mermas |
| Cerrada | Período consolidado | Solo reversible con permiso de Administrador |

"Aprobada" y "Stock actualizado" se ejecutan en la misma transacción cuando no se requiere doble firma. Desde cualquier estado anterior a Aprobada se puede **cancelar**: libera la reserva y queda registrado.

### Reglas de negocio

1. El peso de entrada no puede superar el disponible no reservado del lote.
2. **Balance exacto:** suma de salidas + suma de diferencias clasificadas = peso de entrada, al gramo. Sin eso no se puede enviar a control.
3. Una diferencia clasificada como "pendiente de revisión" impide aprobar. El sistema nunca asigna la diferencia solo.
4. Una orden puede consumir varios lotes; cada lote resultante guarda en `LoteOrigen` qué lotes y cuántos kilos lo formaron (proporcional al peso aportado).
5. Vencimiento del lote resultante = el menor entre el vencimiento del lote de origen y la fecha del proceso + vida útil del corte.
6. Si un corte sale fuera de su rendimiento estándar ± tolerancia, la orden muestra alerta y exige comentario del supervisor (no bloquea).
7. Una orden aprobada no se edita. Se revierte (contramovimiento completo) y se crea una nueva. La reversión se bloquea si algún lote resultante ya fue consumido o vendido; en ese caso solo cabe un ajuste autorizado.
8. La plantilla se versiona: cambiar rendimientos estándar o precios de referencia crea una versión nueva, y las órdenes anteriores conservan la suya.

### Indicadores

```latex
R = \frac{\text{Peso aprovechable}}{\text{Peso inicial}} \times 100
```

```latex
M = \frac{\text{Peso descartado}}{\text{Peso inicial}} \times 100
```

```latex
D = \text{Peso inicial} - \sum \text{Salidas} - \sum \text{Diferencias clasificadas} = 0
```

Peso aprovechable = cortes + subproductos. También por orden: rendimiento por pollo (kg aprovechables ÷ unidades), rendimiento por línea real vs estándar. Agregados: por lote, proveedor, operario, rango de peso del pollo y mes.

### Distribución de costos

Paso previo común a ambos métodos:

1. Costo conjunto = Σ (peso de entrada × costo unitario del lote).
2. **Merma anormal** = merma real − merma estándar (si es positiva). Su costo se imputa como pérdida del período y se descuenta del costo conjunto. La merma normal se absorbe.
3. Las líneas con criterio "valor de recupero fijo" toman ese valor y se descuentan; las de "costo cero" no reciben costo.
4. El resto se distribuye entre las líneas que participan, por el método configurado.

**Ejemplo** (datos del punto 5.1 de la investigación): 100 pollos, 250 kg, $3.000/kg, costo conjunto $750.000. Salidas aprovechables 240 kg; merma 10 kg (4%). Si el estándar es 3% (7,5 kg), la merma anormal es 2,5 kg → $7.500 a pérdida. Quedan **$742.500** a distribuir.

**Método A — por peso:** $742.500 ÷ 240 kg = $3.093,75/kg para todos los cortes.

**Método B — por valor relativo de venta:**

```latex
Costo_i = C_{distribuible} \times \frac{peso_i \times precio_i}{\sum_j peso_j \times precio_j}
```

| Corte | Peso (kg) | Precio ref. ($/kg) | Valor de venta ($) | Costo asignado ($) | Costo/kg ($) |
| --- | --- | --- | --- | --- | --- |
| Pechuga | 65 | 5.500 | 357.500 | 262.815,60 | 4.043,32 |
| Patamuslo | 55 | 4.000 | 220.000 | 161.732,67 | 2.940,59 |
| Muslo | 45 | 4.200 | 189.000 | 138.943,07 | 3.087,62 |
| Carcasa | 40 | 3.000 | 120.000 | 88.217,82 | 2.205,45 |
| Alas | 20 | 4.800 | 96.000 | 70.574,26 | 3.528,71 |
| Menudos | 10 | 2.000 | 20.000 | 14.702,97 | 1.470,30 |
| Otros aprovechables | 5 | 1.500 | 7.500 | 5.513,61 | 1.102,72 |
| **Total** | **240** |  | **1.010.000** | **742.500,00** |  |

Los precios de patamuslo, menudos y otros son ficticios para el ejemplo. El centavo de redondeo se asignó a la pechuga (línea de mayor importe). El método queda congelado en cada orden; cambiarlo afecta solo órdenes futuras.

### Servicios

`crear_orden()` · `reservar_materia_prima()` · `registrar_pesaje(orden, linea, peso, fuente)` · `clasificar_diferencia()` · `enviar_a_control()` · `aprobar_orden()` (calcula, distribuye costos, genera lotes y movimiento en una transacción) · `cancelar_orden()` · `revertir_orden()`. Para la balanza se define la interfaz `FuentePeso` (`leer_peso() -> Decimal`) con una implementación manual; la de balanza llega en Fase 7.

### Pantallas

- **Operario (tablet, botones grandes):** 1) elegir plantilla y escanear o elegir lote; 2) pesajes de entrada con contador de unidades y total; 3) una tarjeta por corte con su total acumulado y botón "Agregar pesaje"; 4) balance: barra entrada vs. salidas y diferencia a clasificar; 5) enviar a control.
- **Supervisor:** bandeja de órdenes pendientes, detalle con real vs. estándar resaltado, aprobar / devolver con comentario.
- **Configuración:** plantillas y sus versiones, catálogo de cortes, categorías de merma.
- **Dashboard de desposte** (punto 11.2): kg ingresados, kg obtenidos, rendimiento %, merma %, costo/kg de origen y por corte, órdenes del período, rendimiento por operario, proveedor y lote.
- **Reportes:** rendimiento por pollo, por lote, por proveedor, por operario; mermas por categoría; pérdidas económicas; stock de cortes. Exportables a Excel, PDF y CSV.

### Permisos

Operario: crear y pesar. Supervisor: clasificar, aprobar, cancelar. Administrador: revertir órdenes cerradas, editar plantillas y método de costeo.

### Criterios de aceptación

- [ ] 5 pollos, 12,600 kg de entrada, 11,750 kg de salidas → la orden muestra D = 0,850 kg y no deja enviar a control hasta clasificarla.
- [ ] Clasificando 0,600 kg como goteo y 0,250 kg como pérdida por limpieza → R = 93,2540 %, M = 6,7460 %, D = 0.
- [ ] Una diferencia "pendiente de revisión" impide aprobar.
- [ ] Al aprobar: el lote de pollo baja 12,600 kg; se crean 7 lotes de cortes vinculados al lote de origen; el Kardex muestra un solo movimiento de transformación.
- [ ] El ejemplo de 250 kg da exactamente los importes de la tabla del método B, y la suma de costos asignados + pérdida = $750.000,00.
- [ ] Cancelar en "En proceso" libera la reserva completa.
- [ ] Revertir una orden cuyos cortes no se usaron devuelve el pollo con su costo original y anula los lotes de cortes.
- [ ] Revertir con un corte ya vendido es rechazado con explicación.
- [ ] Dos operarios no pueden reservar el mismo kilo del mismo lote.

## Extensión A — Mercadería general multirubro

**Objetivo:** que el mismo núcleo de inventario sirva para almacenes, distribuidoras, ferreterías, tiendas de ropa, casas de electrónica, restaurantes y hoteles, activando por producto solo los controles que ese producto necesita. Se apoya en las Fases 1 y 2, no en el desposte, así que puede construirse **ahora**, mientras se esperan las pollerías piloto.

### Idea central: el perfil de control

No se crean "tipos de producto" separados ni un sistema por rubro. Cada producto tiene un **perfil de control** con indicadores que se combinan. El Kardex, los movimientos y los costos siguen siendo los de la Fase 1; cada indicador agrega validaciones y datos a las líneas de movimiento.

| Indicador | Qué exige en cada movimiento | Compatible con |
| --- | --- | --- |
| Controla lote | Código de lote (ya existe, Fase 2) | Vencimiento, variantes |
| Controla vencimiento | Fecha; si no hay lote, partida interna (A1) | Lote, variantes, medida |
| Tiene variantes | Elegir talle, color u otro eje (A2) | Lote, serie |
| Controla serie | Un número de serie por unidad (A3) | Variantes. Unidad base siempre "un" |
| Es kit | Nada propio: mueve sus componentes (A4) | Solo componentes sin serie en el kit virtual |
| Es retornable | Saldo de envases por cliente o proveedor (A5) | Ninguno de los anteriores |
| Vende por medida | Cantidad decimal en m, kg o l (A6) | Lote, vencimiento |
| Es de consignación | Dueño de la mercadería distinto de la empresa (A7) | Todos menos retornable |
| Uso interno | Centro de costo en cada salida (A8) | Lote, vencimiento |
| Bien en circulación | Estado y responsable por unidad o grupo (A9) | Serie |

El sistema rechaza combinaciones sin sentido (por ejemplo, serie y venta por medida a la vez). Un indicador no se puede apagar si el producto ya tiene stock que depende de él.

### Qué usa cada rubro

| Rubro | Variantes | Serie | Kits | Retornables | Por medida | Consignación | Uso interno | Circulación |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Almacén / autoservicio | — | — | Sí | Sí | Sí | Sí | — | — |
| Distribuidora de bebidas | — | — | Sí | Sí | — | — | — | — |
| Ferretería / corralón | — | Sí | Sí | — | Sí | — | — | Sí |
| Indumentaria / calzado | Sí | — | Sí | — | — | Sí | — | — |
| Electrónica / electrodomésticos | Sí | Sí | Sí | — | — | Sí | — | — |
| Restaurante | — | — | Sí | Sí | Sí | — | Sí | Sí |
| Hotel | — | — | Sí | — | — | — | Sí | Sí |
| Pollería / carnicería | — | — | Sí | Sí | Sí | — | Sí | — |

Quedan fuera: farmacias y venta de medicamentos (tienen trazabilidad regulada propia) y activos fijos contables (amortizaciones); el inventario de bienes en circulación de A9 es operativo, no contable.

### A1. Núcleo común de mercadería

Lo que necesita cualquier comercio que revende: comprar por bulto, vender por unidad, mantener precios al día y cargar miles de productos rápido.

**Modelos**

| Modelo | Campos clave |
| --- | --- |
| `Producto` (agrega) | perfil de control (indicadores de arriba), contenido neto + unidad (500 g, 1,5 l), alícuota de IVA (21 / 10,5 / 27 / 0 %), impuesto interno, costo de reposición, precio manual (sí/no), ubicación habitual |
| `Presentacion` | producto, nombre (unidad, pack x6, bulto x12), factor en unidades base, código de barras (EAN-13 o DUN-14), se compra, se vende, precio propio (opcional) |
| `Ubicacion` | depósito, código (G03-E2 = góndola 3, estante 2), descripción |
| `ListaPreciosProveedor` / `LineaListaProveedor` | proveedor, vigencia, archivo; por línea: código del proveedor, presentación, precio de lista, bonificaciones ("10+5"), producto vinculado |
| `ReglaPrecio` | alcance (empresa, categoría, proveedor, marca, producto), markup %, redondeo (a $1 / $10 / $50 / $100 hacia arriba), prioridad |
| `ActualizacionPrecios` | tipo (costo o venta), filtro, % o importe, productos afectados, vista previa, aplicada por, deshecha |
| `HistorialPrecio` | producto, presentación, costo y precio anterior/nuevo, origen (manual, recepción, lista, masiva) |
| `EtiquetaPendiente` | producto, presentación, motivo, impresa |
| `PartidaVencimiento` | producto, depósito, fecha, cantidad. Lote interno que el usuario no ve: solo ve fechas |

**Reglas**

1. **Stock siempre en unidad base.** 10 bultos x12 suman 120 unidades; vender un pack x6 descuenta 6. Escanear cualquier código identifica producto y presentación.
2. **Dos costos:** el promedio ponderado (Fase 1) valoriza inventario y Kardex; el **costo de reposición** (último costo neto de compra o de lista) forma el precio. Así una suba del proveedor llega al precio sin esperar a que se agote el stock viejo.
3. Costo neto = precio de lista × (1 − bonif. 1) × (1 − bonif. 2)… ÷ factor de la presentación.
4. Precio sugerido = costo de reposición × (1 + markup) × (1 + IVA) + impuesto interno, redondeado. Gana la regla más específica: producto > marca > proveedor > categoría > empresa.
5. Un precio manual no cambia en actualizaciones masivas salvo que se lo incluya explícitamente.
6. Al recibir más caro, el sistema propone el precio nuevo y genera etiqueta pendiente; solo lo aplica solo si la empresa activó "actualizar precio al recibir".
7. **Actualización masiva:** filtro + porcentaje + vista previa (anterior, nuevo, margen) + confirmar, en una transacción, con deshacer en bloque.
8. **Vencimiento sin lote:** la recepción pide solo la fecha; las salidas consumen la partida que vence primero sin preguntar. Alertas a 30, 15 y 7 días.
9. **Sugerencia de pedido** por proveedor: stock máximo − stock actual − pedidos en curso, redondeado hacia arriba a bultos enteros. Con la Fase 6 pasa a usar consumo promedio diario × días de cobertura.
10. Códigos de barras únicos por empresa (incluidas presentaciones y variantes).

**Ejemplo de precio:** bulto de 12 gaseosas a $18.000 de lista, bonificación 10+5 → costo neto del bulto $15.390,00 → costo de reposición $1.282,50 por unidad → markup 35 % = $1.731,38 → IVA 21 % = $2.094,96 → redondeo a $10 = **$2.100**.

**Pantallas:** alta rápida por escaneo (si el código no existe, formulario mínimo con precio sugerido), búsqueda instantánea, importar lista de proveedor (mapeo de columnas recordado por proveedor), actualización masiva, recepción por escaneo de bultos, cola de etiquetas de góndola (PDF A4 o térmica, con precio por kg o litro), vencimientos próximos, sugerencia de pedido convertible en orden de compra.

**Criterios de aceptación**

- [ ] El ejemplo de la gaseosa da $1.282,50 y $2.100.
- [ ] Con 50 unidades a $1.100 y una compra de 120 a $1.282,50, el Kardex valoriza a $1.228,82 y el precio sugerido usa $1.282,50.
- [ ] +8 % a un proveedor con 300 productos: vista previa, una transacción, 300 etiquetas pendientes, deshacer completo.
- [ ] Una lista de 5.000 filas con 20 códigos nuevos actualiza los conocidos y deja los 20 para vincular.
- [ ] Un yogur con partidas que vencen el 12/11 y el 20/11 descuenta primero la del 12/11.
- [ ] Stock 8, máximo 40, bulto x12 → sugiere 3 bultos.

### A2. Variantes (talle, color y otros ejes)

Para ropa, calzado, artículos con tamaños o sabores. Cada combinación es un SKU con stock propio, pero se carga, compra y consulta como un solo artículo.

| Modelo | Campos clave |
| --- | --- |
| `EjeVariante` | empresa, nombre (talle, color, número, sabor), valores ordenados (S, M, L, XL / 38 a 44) |
| `Producto` (agrega) | producto padre (nulo si no es variante), ejes usados; valores de cada eje en la variante |
| `CurvaTalles` | nombre, eje, cantidad por valor (ej.: 38-1, 39-2, 40-3, 41-2, 42-1) |

**Reglas**

1. El padre no tiene stock: es la ficha común (nombre, categoría, marca, precio base, foto). Las variantes heredan y pueden pisar precio y código de barras.
2. Al crear el padre se generan las combinaciones elegidas de una vez, cada una con su SKU (`REM-001-AZ-M`) y código de barras interno si no tiene EAN.
3. Compra por curva: "2 curvas de la 38 a la 42" carga 2, 4, 6, 4, 2 pares.
4. Pantallas de compra, stock, transferencia y conteo en **matriz** (talle × color), con totales por fila y columna.
5. Reportes por padre (cuántas remeras vendí) y por variante (qué talle se agota primero).

**Criterios**

- [ ] Una remera en 3 colores × 5 talles genera 15 SKU con código de barras único cada uno.
- [ ] Comprar 2 curvas 38–42 de un zapato carga 18 pares distribuidos 2-4-6-4-2.
- [ ] La matriz de stock muestra el total por color, por talle y general, y coincide con la suma del Kardex.

### A3. Números de serie y garantía

Para electrónica, electrodomésticos, herramientas eléctricas, celulares (IMEI). Cada unidad se identifica sola, de la compra a la venta y a la garantía.

| Modelo | Campos clave |
| --- | --- |
| `NumeroSerie` | producto, serie (o IMEI), estado (en stock, reservado, vendido, en reparación, devuelto, baja), depósito, recepción de origen, costo de compra, venta y cliente, garantía hasta |
| `Producto` (agrega) | meses de garantía, formato de serie esperado (regex opcional) |
| `EventoSerie` | serie, tipo (ingreso, transferencia, venta, devolución, envío a service, regreso de service, baja), fecha, documento, usuario |

**Reglas**

1. La unidad base es siempre "un". En cada movimiento, la cantidad debe igualar la cantidad de series escaneadas o tipeadas.
2. Serie única por producto y empresa; escanear una serie que ya está en stock o vendida se rechaza con su historial a la vista.
3. Solo se puede vender, transferir o devolver una serie que está en el depósito y estado correctos.
4. El inventario se valoriza a promedio como el resto, pero cada serie guarda su costo de compra para ver el margen real de esa unidad.
5. Garantía = fecha de venta + meses del producto. Buscar una serie muestra todo su recorrido y si está en garantía.
6. Envío a service: la serie sale a un depósito virtual "En reparación" y vuelve con su mismo número.

**Criterios**

- [ ] Recibir 5 televisores exige 5 series distintas; con 4 no deja confirmar.
- [ ] Vender una serie que figura vendida es rechazado y muestra a quién y cuándo se vendió.
- [ ] Una heladera vendida el 10/11/2026 con 12 meses de garantía figura en garantía hasta el 10/11/2027.

### A4. Kits, combos y canastas

Un producto formado por otros. Hay dos maneras de manejarlo, y la empresa elige por producto:

| Tipo | Cuándo se usa | Stock | Qué descuenta |
| --- | --- | --- | --- |
| **Kit virtual** (combo) | Se arma al venderlo: "combo mate" = yerba + azúcar | No tiene stock propio; el disponible es el mínimo que permiten los componentes | Al vender, descuenta cada componente |
| **Kit armado** | Se prepara antes y se guarda: canastas navideñas, cajas regalo, kits de bienvenida de hotel | Stock propio, con lote | Al armarlo (orden de armado) descuenta componentes y suma kits |

| Modelo | Campos clave |
| --- | --- |
| `ComponenteKit` | kit, componente, cantidad, sustituible por |

**Reglas**

1. El kit armado usa el motor de transformación de la Fase 3 con una plantilla de "armado" (N entradas → 1 salida). Desarmar es la orden inversa.
2. Costo del kit = suma del costo de sus componentes (más empaque, si es un componente con stock).
3. Disponible del kit virtual = mínimo, entre componentes, de (stock del componente ÷ cantidad requerida), redondeado hacia abajo.
4. Un componente con serie no puede ir en un kit virtual (exigiría escanear series en el combo); sí en uno armado.
5. Los componentes de un kit armado quedan en `LoteOrigen`, así la trazabilidad de la Fase 5 funciona igual.

**Criterios**

- [ ] Con 10 yerbas y 4 azúcares, el combo mate (1 + 1) muestra 4 disponibles; venderlo descuenta 1 de cada uno.
- [ ] Armar 50 canastas de 6 productos descuenta 300 unidades en total, crea 50 canastas con costo igual a la suma y deja el vínculo a los lotes usados.
- [ ] Desarmar 10 canastas devuelve sus componentes con el costo original.

### A5. Envases retornables y comodatos

Cajones de gaseosa, botellones de agua de 20 l, garrafas, barriles de cerveza, pallets, y los cajones plásticos en los que llega el pollo. Valen plata y se pierden si no se controlan.

| Modelo | Campos clave |
| --- | --- |
| `Producto` (envase) | indicador retornable, valor de depósito o reposición |
| `EnvaseAsociado` | producto o presentación que lo usa, envase, cantidad (1 cajetón = 1 cajón + 12 botellas) |
| `SaldoEnvase` | tercero (cliente o proveedor), envase, cantidad que nos debe (+) o que debemos (−) |
| `MovimientoEnvase` | tercero, envase, cantidad, tipo (entrega con producto, devolución de vacíos, cobro de depósito, pérdida), documento de origen |

**Reglas**

1. Al vender o despachar un producto con envase asociado, se suma automáticamente a la deuda de envases del cliente. Al recibir de un proveedor, se suma a lo que la empresa le debe.
2. El stock de envases vacíos vive en un depósito "Envases"; los llenos no se cuentan aparte (van dentro del producto).
3. El cliente puede devolver vacíos o pagar el depósito; ambas opciones bajan su saldo.
4. Alerta por cliente con saldo de envases mayor a un límite o sin movimiento en N días.
5. **Comodatos** (heladera, dispenser, chopera prestados a un cliente): se registran como bien en circulación (A9) asignado a ese cliente, con fecha y contrato adjunto.

**Criterios**

- [ ] Despachar 20 cajones de gaseosa a un kiosco deja su saldo en +20 cajones; devolver 15 lo deja en +5.
- [ ] Recibir 100 pollos en 10 cajones del proveedor deja a la empresa debiéndole 10 cajones hasta que los devuelva.
- [ ] El reporte de envases muestra, por cliente y por tipo, cuántos tiene en la calle y su valor.

### A6. Venta por medida y a granel

Cables, cadenas, mangueras y telas por metro; pintura y líquidos sueltos por litro; legumbres, fiambres y alimento balanceado por kilo.

| Modelo | Campos clave |
| --- | --- |
| `Producto` (agrega) | vende por medida, unidad (m, kg, l), venta mínima, múltiplo de venta (de a 0,5 m), controla piezas |
| `Pieza` | producto, identificador (rollo 3), medida inicial, medida restante, depósito, estado (entera, abierta, retazo, agotada) |

**Reglas**

1. Se compra por presentación (rollo de 100 m, bolsa de 25 kg) y se vende en la unidad base con decimales.
2. Si el producto controla piezas, cada venta indica de qué rollo sale; así el conteo físico se hace midiendo rollos, no adivinando metros.
3. Una pieza con menos de una medida configurable pasa a **retazo**: se puede vender con descuento o dar de baja como merma.
4. Fiambres y sueltos por kilo leen la etiqueta de balanza de peso variable (EAN-13 con prefijo 2; formato configurable por empresa).
5. La diferencia entre lo cortado y lo vendido (despuntes, recortes de fiambre) se registra como merma de corte, para que el rendimiento real por pieza sea visible.

**Criterios**

- [ ] Comprar 3 rollos de 100 m de cable crea 3 piezas; vender 12,5 m del rollo 2 lo deja en 87,5 m.
- [ ] Vender 0,3 m con múltiplo de 0,5 m es rechazado.
- [ ] Un rollo que queda con 1,8 m (umbral 2 m) pasa a retazo.
- [ ] Una etiqueta de 0,350 kg de queso a $12.000/kg se interpreta como $4.200 y descuenta 0,350 kg.

### A7. Consignación

Dos situaciones distintas que se modelan distinto:

| Caso | Ejemplo | De quién es el stock | Cómo se modela |
| --- | --- | --- | --- |
| **Consignación recibida** | Un proveedor de golosinas o de ropa deja mercadería y cobra solo lo vendido | Del proveedor | Stock con dueño = proveedor; no suma al valor del inventario propio |
| **Consignación entregada** | La distribuidora deja mercadería en un kiosco y cobra lo que el kiosco vende | De la empresa | Depósito virtual por cliente; el stock sigue siendo propio |

| Modelo | Campos clave |
| --- | --- |
| `Stock` / `LineaMovimiento` (agregan) | dueño (nulo = propio, o un proveedor) |
| `AcuerdoConsignacion` | tercero, sentido (recibida / entregada), precio pactado por producto, frecuencia de liquidación |
| `LiquidacionConsignacion` | acuerdo, período, líneas (vendido, devuelto, faltante), importe, comprobante generado |

**Reglas**

1. El stock en consignación recibida aparece en existencias físicas pero se separa en todos los reportes de valorización.
2. Al venderlo, se genera la deuda con el proveedor al precio pactado; la liquidación periódica agrupa lo vendido y genera la cuenta corriente.
3. Lo no vendido se devuelve con un movimiento de devolución de consignación.
4. En la entregada, la liquidación compara lo dejado con lo que el cliente devuelve o informa; lo que falta se factura como vendido.

**Criterios**

- [ ] Recibir 100 alfajores en consignación no cambia el valor del inventario propio.
- [ ] Vender 60, devolver 30 y declarar 10 faltantes genera una liquidación por 70 al precio pactado.
- [ ] Dejar 48 gaseosas en un kiosco las pasa a su depósito virtual; al liquidar 40 vendidas, se facturan 40 y quedan 8 en el kiosco.

### A8. Consumo interno por centro de costo y niveles par

Para lo que se compra para usar, no para vender: limpieza, descartables, amenities de hotel, insumos de oficina, gas, uniformes.

| Modelo | Campos clave |
| --- | --- |
| `CentroCosto` | sucursal, nombre (cocina, salón, barra, habitaciones, lavandería, administración), responsable |
| `NivelPar` | producto, depósito o ubicación de uso, cantidad que siempre debe haber |
| `PedidoInterno` | centro de costo, solicitante, líneas, estado (pedido, aprobado, entregado) |

**Reglas**

1. Toda salida de un producto de uso interno exige centro de costo; queda como gasto de ese centro al costo promedio.
2. **Nivel par:** cada sector (barra, office de piso, cocina) tiene un stock fijo. El sistema calcula la reposición (par − existencia) y genera la transferencia desde el depósito central.
3. El sector puede hacer un pedido interno; si supera un monto, requiere aprobación.
4. Reportes de consumo por centro, por mes y por producto, con variación contra el mes anterior. Con la integración con Impulso Hotel, consumo por habitación ocupada.

**Criterios**

- [ ] Sacar 5 l de lavandina sin centro de costo es rechazado.
- [ ] La barra con par de 12 botellas de agua y 4 en existencia genera una transferencia sugerida de 8.
- [ ] El reporte de octubre muestra el gasto de limpieza por centro y el total coincide con el Kardex.

### A9. Bienes en circulación (blancos, vajilla, herramientas, equipos prestados)

Cosas que no se venden ni se consumen de una vez: circulan entre estados y se pierden de a poco. Sábanas y toallas de hotel, manteles, uniformes, vajilla, herramientas que se prestan al personal, heladeras o dispensers en comodato.

| Modelo | Campos clave |
| --- | --- |
| `BienCirculante` | producto, modo (por cantidad / por unidad con serie), valor de reposición, vida útil estimada (usos o lavados) |
| `SaldoCirculacion` | bien, estado (disponible, en uso, en lavandería, en reparación, prestado, dañado), ubicación, responsable o cliente, cantidad |
| `MovimientoCirculacion` | bien, cantidad o serie, estado origen y destino, responsable, fecha de devolución esperada, documento |

**Reglas**

1. El total de un bien solo cambia por alta (compra) o baja (rotura, pérdida, fin de vida útil, con motivo). Los movimientos entre estados no cambian el total.
2. Envío a lavandería y regreso se cuentan: lo que no vuelve queda como faltante de lavandería, visible por proveedor de lavado.
3. Préstamo de herramienta: responsable y fecha de devolución; alerta al vencer.
4. Comodatos a clientes (A5) quedan con el cliente como responsable y contrato adjunto.
5. Estos bienes no se mezclan con el stock vendible: tienen su propio reporte de valor (bienes operativos), y las bajas se reportan como costo de reposición del período.

**Criterios**

- [ ] Un hotel con 400 sábanas manda 120 a lavandería y vuelven 117 → quedan 3 faltantes de lavandería y el total sigue en 400 hasta que se den de baja.
- [ ] Un taladro prestado con devolución el viernes aparece en alertas el sábado si no se devolvió.
- [ ] Dar de baja 6 platos rotos baja el total y suma su valor de reposición al reporte de pérdidas.

### A10. Atributos por categoría y productos equivalentes

Una ferretería tiene miles de artículos que solo se distinguen por medidas: tornillos, caños, mechas. Buscarlos por nombre no alcanza.

| Modelo | Campos clave |
| --- | --- |
| `AtributoCategoria` | categoría, nombre (diámetro, largo, material, rosca, voltaje), tipo (texto, número + unidad, lista de valores), obligatorio, filtrable |
| `ValorAtributo` | producto, atributo, valor |
| `ProductoEquivalente` | producto, equivalente, tipo (misma cosa de otra marca, sustituto aceptable, mismo producto de otro proveedor) |

**Reglas**

1. Los atributos se heredan por categoría y aparecen como filtros en la búsqueda ("tornillo, 6 mm, acero inoxidable").
2. Al buscar o vender un producto sin stock, se ofrecen sus equivalentes con stock.
3. La sugerencia de pedido puede agrupar equivalentes para no comprar dos marcas de lo mismo.

**Criterios**

- [ ] Filtrar "Tornillos" por diámetro 6 mm y material inoxidable devuelve solo los que cumplen ambos.
- [ ] Un producto sin stock muestra sus 2 equivalentes con stock y su precio.

### A11. Clasificación ABC, conteo cíclico e indicadores de inventario

Con miles de productos no se puede contar todo seguido. Se cuenta más seguido lo que más pesa en plata.

**Reglas**

1. **ABC** semanal por valor de salidas de los últimos 90 días: A = productos que suman el 80 % del valor, B = el siguiente 15 %, C = el 5 % restante. Porcentajes configurables.
2. **Conteo cíclico:** cada día el sistema arma una lista de N productos a contar, priorizando A (mensual), B (trimestral) y C (semestral), y los que tuvieron diferencias o stock negativo. Se cuenta desde el celular, escaneando por góndola.
3. Indicadores: exactitud de inventario (% de productos contados sin diferencia), rotación, días de inventario, **stock inmovilizado** (sin salidas en 90 días, valorizado), quiebres de stock (días sin stock de productos A).
4. Los conteos cíclicos generan ajustes con el mismo circuito de autorización del inventario físico de la Fase 2.

**Criterios**

- [ ] Con 1.000 productos de prueba, la clasificación asigna A a los que suman el 80 % del valor y la suma de las tres clases da el 100 %.
- [ ] La lista diaria de 40 productos incluye todos los A que no se contaron en los últimos 30 días antes que cualquier C.
- [ ] El reporte de stock inmovilizado lista los productos sin salidas en 90 días con su valor total.

### Plantillas de onboarding por rubro

Al elegir rubro, el asistente de la Fase 8 precarga todo editable:

| Rubro | Precarga |
| --- | --- |
| Almacén / autoservicio | Categorías (almacén seco, bebidas, lácteos, fiambres, congelados, limpieza, perfumería, golosinas) con IVA y markup por defecto; depósitos Salón y Depósito; mermas de rotura y vencimiento |
| Distribuidora de bebidas | Envases retornables (cajón, botellón, barril, pallet); depósitos virtuales para consignación entregada |
| Ferretería / corralón | Atributos por categoría (diámetro, largo, material, rosca); productos por metro con piezas; herramientas en préstamo |
| Indumentaria / calzado | Ejes talle y color; curvas de talles estándar |
| Electrónica | Control de serie e IMEI; garantía por defecto de 12 meses; depósito "En reparación" |
| Restaurante | Centros de costo (cocina, salón, barra); niveles par; vajilla y mantelería en circulación |
| Hotel | Centros de costo (habitaciones, lavandería, desayuno); amenities con nivel par por office; blancos en circulación; kit de bienvenida |

### Orden de construcción

| Paso | Bloque | Por qué en este orden |
| --- | --- | --- |
| 1 | A1 Núcleo común + migración del perfil de control | Todos los rubros lo usan. Se agregan ya todos los indicadores y el campo `dueno` en `Stock`, aunque su funcionalidad llegue después, para tocar el modelo central una sola vez |
| 2 | A11 ABC y conteo cíclico | Solo lee el Kardex existente; valor inmediato para cualquier cliente |
| 3 | A4 Kits | Reutiliza el motor de transformación ya probado |
| 4 | A6 Medida y granel | Necesario para almacén (fiambres) y ferretería |
| 5 | A5 Retornables | También sirve a las pollerías piloto (cajones del proveedor) |
| 6 | A2 Variantes | Abre indumentaria y calzado |
| 7 | A3 Series | Abre electrónica; depende de pantallas de escaneo ya hechas en A1 |
| 8 | A8 Consumo interno y A9 Circulación | Abren restaurantes y hoteles; conectan con Impulso Hotel y Gastronomía |
| 9 | A7 Consignación | Toca valorización y cuentas corrientes; conviene tener la Fase 6 cerca |
| 10 | A10 Atributos y equivalentes | Mejora la búsqueda cuando ya hay catálogos grandes cargados |

### Criterios generales de la extensión

- [ ] Los 74 tests de las Fases 0 a 3 siguen pasando sin cambios después de cada bloque.
- [ ] Un producto sin ningún indicador nuevo se comporta exactamente igual que antes de la extensión.
- [ ] Combinaciones inválidas del perfil (serie + venta por medida, retornable + variantes) son rechazadas al guardar.
- [ ] Apagar un indicador con stock que depende de él es rechazado con explicación.
- [ ] Búsqueda en menos de 300 ms con 20.000 productos; importación de 5.000 filas en menos de 60 s en segundo plano; actualización masiva de 3.000 precios en menos de 10 s.
- [ ] Cada modelo nuevo pasa la prueba de aislamiento entre empresas por pantalla, API e ID adivinado.
- [ ] Cobertura de `services.py` de la extensión ≥ 85 %.

## Fase 4 — Motor de transformación: vacuno, porcino, gastronomía y costos de proceso

**Objetivo:** reutilizar el motor de la Fase 3 para carnicerías y restaurantes, sin programar cada producto desde cero, y sumar los costos de proceso para llegar a la rentabilidad real por corte.

### Alcance

**Incluye:** desposte vacuno multi-etapa, desposte porcino, elaboración gastronómica con recetas (BOM), costos de proceso (mano de obra, energía, refrigeración, empaques, etiquetas, limpieza), rentabilidad por corte, reportes comparativos.

**No incluye:** fraccionamiento/envasado y producción industrial (fuera del alcance acordado; el motor los soportará con una plantilla nueva).

### 4.1 Desposte vacuno

El vacuno tiene etapas encadenadas. Cada etapa es una `OrdenTransformacion` cuya salida es la entrada de la siguiente; el lote se hereda en toda la cadena.

1. **Recepción de media res:** cada media res es una unidad identificada (número de tropa, garrón o identificador del proveedor, peso de recepción).
2. **Cuarteo** (opcional): media res → cuarto delantero + cuarto trasero (+ merma).
3. **Desposte:** cada cuarto → cortes comerciales + huesos + grasa + recortes + merma.

Modelo nuevo: `UnidadAnimal` (lote, identificador, tipo: media res / cuarto / res porcina, peso de recepción, estado). Permite medir rendimiento **por media res**, no solo por lote.

Catálogo inicial vacuno: asado, vacío, matambre, nalga, bola de lomo, cuadrada, peceto, lomo, bife de chorizo, costilla, roast beef, paleta, falda, osobuco, huesos, grasa, recortes. Plantillas separadas para cuarto delantero y trasero.

### 4.2 Desposte porcino

Misma mecánica que vacuno (res o media res → cortes). Catálogo inicial: bondiola, costillar, pechito, carré, paleta, jamón, panceta, tocino, codillo, recortes, grasa, huesos. Los recortes y la grasa quedan disponibles como insumo de recetas (chacinados) en 4.3.

### 4.3 Elaboración gastronómica (recetas)

La elaboración es la transformación inversa: **muchas entradas → una o dos salidas.**

| Modelo | Campos clave |
| --- | --- |
| `Receta` | producto resultante, rendimiento teórico (kg y unidades por tanda), versión, vigente desde |
| `LineaReceta` | receta, insumo, cantidad por tanda, unidad, merma teórica %, sustituible por |

Ejemplo de la investigación — milanesas de pollo por tanda: pechuga 10 kg, huevo 2 kg, pan rallado 3 kg, condimentos 0,2 kg.

Reglas:

1. La orden de elaboración propone las cantidades teóricas × número de tandas; el operario carga lo **realmente consumido** y lo producido (kg y unidades).
2. Cada insumo consume lote por FEFO; el lote de milanesas guarda en `LoteOrigen` todos los lotes usados (pechuga, huevo, pan rallado). Así se cumple la cadena pollo → pechuga → milanesa.
3. Costo del producto = Σ consumos reales × costo del lote + costos de proceso asignados. Se informa costo/kg y costo/unidad.
4. Desvío real vs. teórico por insumo, con alerta si supera la tolerancia.

### 4.4 Costos de proceso

| Modelo | Campos clave |
| --- | --- |
| `ConceptoCostoProceso` | nombre (mano de obra, electricidad, refrigeración, bolsas, bandejas, film, etiquetas, limpieza, transporte), tipo (directo / indirecto) |
| `TarifaCostoProceso` | concepto, sucursal, criterio (por orden, por kg procesado, por hora de proceso, por unidad), importe, vigente desde |
| `CostoProcesoOrden` | orden, concepto, base, importe asignado |

- Los **empaques** que son productos con stock (bandejas, film) se consumen como entradas reales de la orden y descuentan stock.
- Los indirectos se asignan por tarifa vigente al aprobar; se suman al costo distribuible antes del reparto por método A o B.
- Opción de cierre mensual: comparar indirectos tarifados vs. reales del mes y reportar la diferencia (sin recalcular órdenes cerradas).

### 4.5 Rentabilidad por corte

Reporte por período: costo asignado/kg promedio, precio de venta/kg vigente, margen bruto/kg y %, kg producidos, contribución total. Alerta para cortes con margen bajo un umbral configurable. Se rotula como **margen bruto**: no incluye gastos generales, impuestos ni comisiones (ver punto 5.3 de la investigación). Los precios de venta reales llegan con la Fase 6; hasta entonces se usa el precio de lista.

### Criterios de aceptación

- [ ] Una media res de 110 kg pasa por cuarteo y desposte de ambos cuartos; el reporte muestra rendimiento de esa media res y cada corte conserva el identificador de origen.
- [ ] Un desposte porcino genera recortes que luego se consumen en una receta de chorizo, y la trazabilidad muestra res → recorte → chorizo.
- [ ] Una tanda de milanesas con consumo real de 10,4 kg de pechuga (teórico 10 kg) registra el desvío de 4 % y costea con 10,4 kg.
- [ ] La milanesa resultante muestra los lotes de pechuga, huevo y pan rallado usados, y desde la pechuga se llega al lote de pollo entero.
- [ ] Con tarifa de mano de obra $200/kg procesado, una orden de 250 kg suma $50.000 al costo distribuible.
- [ ] Cambiar una receta crea una versión nueva; las órdenes anteriores siguen mostrando la versión con que se hicieron.

## Fase 5 — Trazabilidad completa, calidad y cadena de frío

**Objetivo:** ante un problema en un lote, encontrar en minutos todos sus derivados, bloquearlos y saber dónde están; y llevar los registros de temperatura, limpieza e inspección que exige cada establecimiento.

### Alcance

**Incluye:** consultas de trazabilidad hacia atrás, interna y hacia adelante; retiros de producto; app `cadena_frio` (cámaras, perfiles de conservación, registros manuales, alertas); app `calidad` (inspecciones, no conformidades, POES/BPM, liberaciones); documentación con vencimientos; perfiles de negocio y actividades habilitadas.

**No incluye:** sensores automáticos (Fase 7). Trazabilidad hasta el **cliente** se completa con la Fase 6; en esta fase llega hasta la sucursal y el depósito.

### 5.1 Trazabilidad

La trazabilidad es un grafo: los lotes son nodos y `LoteOrigen` son las aristas. Se consulta con CTE recursivas de PostgreSQL (`WITH RECURSIVE`), sin tablas duplicadas.

| Consulta | Pregunta que responde |
| --- | --- |
| Hacia atrás | ¿De qué proveedor, recepción, remito y temperatura vino este lote? ¿Qué órdenes lo formaron? |
| Interna | ¿Qué se transformó, qué se obtuvo, qué se mezcló, qué mermas hubo, a qué depósito fue? |
| Hacia adelante | ¿Qué lotes derivan de este, dónde está cada kilo hoy, qué se transfirió o vendió? |

- Pantalla de árbol de trazabilidad navegable a partir de un lote, con export a PDF (informe para la autoridad sanitaria).
- Objetivo de rendimiento: la consulta completa de un lote con hasta 3 niveles y 500 derivados responde en menos de 2 segundos.

### 5.2 Retiros de producto (recall)

Modelo `RetiroProducto`: lote de origen, motivo, nivel (preventivo / confirmado), estado (abierto / en curso / cerrado), responsable, acciones registradas.

Flujo (punto 7.5 de la investigación):

1. Buscar el lote.
2. El sistema calcula todos los derivados.
3. Con un botón, **bloquea** todo el stock disponible de esos lotes en todas las sucursales.
4. Lista sucursales y depósitos con existencias o que recibieron mercadería.
5. Lista ventas relacionadas (desde la Fase 6).
6. Genera el informe de trazabilidad.
7. Registra cada acción tomada (descarte, devolución, liberación) hasta cerrar.

### 5.3 Cadena de frío

| Modelo | Campos clave |
| --- | --- |
| `PerfilConservacion` | nombre (refrigerado ave, refrigerado carne, congelado, envasado al vacío), temperatura mínima y máxima por etapa (recepción, almacenamiento, proceso, despacho), fuente normativa (texto) |
| `Camara` | depósito, identificador, tipo, perfil, rango mínimo/máximo, frecuencia de registro esperada, activa |
| `RegistroTemperatura` | cámara o etapa, valor, fecha y hora, fuente (manual / sensor), responsable, fuera de rango (calculado), acción correctiva |

- Los rangos **no vienen inventados por el sistema**: se precargan como plantilla editable con las referencias citadas en la investigación (ANMAT para recepción: pollos −2 a 2 °C; carne fresca hasta 7 °C; supercongelados hasta −18 °C; SENASA para sala de despiece de aves: no superior a 12 °C) y cada empresa los confirma.
- Alertas (Celery Beat): temperatura fuera de rango, cámara sin registro en su frecuencia, lote bloqueado, producto vencido. Se muestran en el dashboard y por email; WhatsApp en Fase 7.
- Un registro fuera de rango exige acción correctiva y puede bloquear los lotes de esa cámara si la empresa lo configura.

### 5.4 Calidad

| Modelo | Campos clave |
| --- | --- |
| `PlantillaControl` | nombre, etapa (recepción, desposte, elaboración, despacho, limpieza), ítems de checklist |
| `Inspeccion` | plantilla, objeto inspeccionado (recepción, orden, cámara, sector), resultado por ítem, resultado general, responsable, fotos |
| `NoConformidad` | origen, descripción, gravedad, lotes afectados, acción correctiva, estado, cierre |
| `RegistroLimpieza` | sector, procedimiento POES, fecha, responsable, verificado por |
| `Documento` | tipo (habilitación, certificado sanitario, documentación de transporte, registro de inspección), objeto (empresa, proveedor, lote, recepción), archivo, vence el |

- Liberar un lote bloqueado exige rol Calidad y deja motivo.
- Documentos con vencimiento generan alerta 30 días antes.
- El sistema registra y da seguimiento a POES, BPM y HACCP; **nunca muestra a una empresa como "habilitada" o "certificada"**.

### 5.5 Perfiles de negocio

`PerfilNegocio` con actividades habilitadas (venta minorista, despiece de aves, despostadero, elaboración, faena). Una pollería minorista no ve opciones de faena; una sala de despiece de aves exige registro de temperatura del sector en cada orden. La empresa declara sus habilitaciones y adjunta la documentación; el sistema no las valida ante el organismo.

### Criterios de aceptación

- [ ] Desde un lote de pollo usado en desposte y luego en milanesas, la consulta hacia adelante muestra pechuga → milanesas con el stock actual de cada uno por depósito.
- [ ] Iniciar un retiro bloquea en una sola acción todo el stock derivado de 3 sucursales; ninguna salida posterior puede usarlo.
- [ ] Un registro de 9 °C en una cámara de pollos con rango −2 a 2 °C dispara alerta y pide acción correctiva.
- [ ] Una cámara con frecuencia de 4 horas sin registro dispara alerta "cámara sin registro".
- [ ] El certificado sanitario de un proveedor que vence en 20 días aparece en alertas.
- [ ] El informe de trazabilidad en PDF incluye proveedor, recepción, temperatura, órdenes, derivados y ubicación actual.

## Fase 6 — Gestión comercial

**Objetivo:** vender desde el mostrador o por pedido, descontando el lote correcto, cobrando, facturando y llevando cuentas corrientes. Con esto la trazabilidad llega hasta el cliente.

### Alcance

**Incluye:** apps `ventas`, `clientes`, `caja`, `cuentas_corrientes`, `facturacion`; mostrador rápido (POS propio simple); pedidos y repartos; devoluciones de clientes; listas de precios por sucursal.

**No incluye:** integración con POS de terceros, tiendas online y Mercado Libre (Fase 7).

### Modelos

| Modelo | Campos clave |
| --- | --- |
| `Cliente` | razón social o nombre, CUIT/DNI, condición IVA, dirección, lista de precios, límite de crédito, activo |
| `ListaPrecios` / `PrecioProducto` | sucursal, producto, precio, vigente desde (con historial) |
| `Pedido` | cliente, fecha de entrega, estado (borrador / confirmado / preparado / despachado / entregado / cancelado), reparto |
| `Venta` | número, sucursal, caja, cliente (opcional en consumidor final), fecha, estado, total, comprobante fiscal (FK opcional) |
| `LineaVenta` | venta, producto, lote, cantidad, precio unitario, descuento, costo unitario (congelado), margen |
| `Caja` / `SesionCaja` | sucursal, usuario, apertura, cierre, saldo inicial, saldo declarado, diferencia |
| `MovimientoCaja` | sesión, tipo (venta, cobro, egreso, retiro), medio de pago, importe |
| `CuentaCorriente` / `MovimientoCC` | cliente o proveedor, debe, haber, saldo, vencimiento, comprobante |
| `ComprobanteFiscal` | tipo (A, B, C, nota de crédito, nota de débito), punto de venta, número, CAE, vencimiento del CAE, estado, respuesta de ARCA |
| `DevolucionCliente` | venta de origen, líneas, motivo, destino (vuelve a stock / descarte), nota de crédito |

### Reglas de negocio

1. **Stock y fiscal separados:** la venta mueve stock al confirmarse; el comprobante fiscal se emite aparte y puede fallar o reintentarse sin tocar el stock.
2. Asignación automática de lote por FEFO; el vendedor puede elegir otro lote disponible.
3. Prohibido vender lotes vencidos o bloqueados (validación en servicio, no solo en pantalla).
4. El costo de cada línea se congela al vender; de ahí salen margen real por corte y por venta.
5. Venta en cuenta corriente: valida límite de crédito; superarlo exige autorización.
6. Una devolución que vuelve a stock reingresa al mismo lote y costo; una que va a descarte se registra como merma por devolución.
7. Cierre de caja: el cajero declara lo contado; la diferencia queda registrada y requiere comentario.
8. Facturación electrónica con ARCA (web service WSFEv1): certificado y punto de venta por empresa, cola de reintentos en Celery, modo homologación para pruebas. Decidir si se implementa directo o con un proveedor intermediario (pregunta abierta).

### Pantallas

- **Mostrador** (pensado para tablet/PC en mostrador): búsqueda por código o nombre, lectura de etiqueta de peso variable, peso manual, total, medio de pago, emitir comprobante. Debe resolver una venta de 3 productos en menos de 30 segundos.
- Pedidos y preparación de reparto · Clientes (ficha, saldo, historial) · Caja (apertura, movimientos, cierre) · Cuentas corrientes · Comprobantes emitidos y pendientes · Devoluciones.
- Dashboard: ventas del período, margen por corte con precio real, ranking de productos rentables.

### Criterios de aceptación

- [ ] Vender 2 kg de pechuga descuenta del lote que vence primero y registra el costo de ese lote.
- [ ] Intentar vender de un lote bloqueado por retiro es rechazado tanto en mostrador como por API.
- [ ] Si ARCA no responde, la venta queda confirmada con comprobante "pendiente" y se reintenta sola; el stock no se duplica.
- [ ] En un retiro, la lista de ventas relacionadas muestra cliente, fecha y kilos de cada venta de lotes derivados.
- [ ] Una nota de crédito por devolución a stock reingresa el producto al lote original.
- [ ] Cierre de caja con faltante de $1.500 queda registrado con la diferencia y el comentario.

## Fase 7 — Integraciones, automatización e IA

**Objetivo:** eliminar la carga manual de pesos y temperaturas, abrir el sistema a otras plataformas y convertir el historial de despostes en recomendaciones.

### 7.1 Balanzas y etiquetas (prioridad más alta de la fase)

- **Agente local** (programa Python liviano instalado en la PC o mini-PC de la sala) que lee la balanza por puerto serie/USB y envía el peso estable al sistema por WebSocket o HTTPS. Implementa la interfaz `FuentePeso` de la Fase 3.
- Drivers por protocolo, empezando por los modelos que usen los primeros clientes (pregunta abierta: relevar marcas en pollerías piloto).
- El pesaje guarda `fuente = balanza` y el identificador del dispositivo; el operario solo confirma. Peso manual queda permitido pero se marca en reportes.
- Etiquetas con código de barras de **peso variable** (EAN-13 con prefijo 2: producto + peso o importe) e impresión en impresoras térmicas (ZPL/EPL). El mostrador lee esas etiquetas.

### 7.2 Lectores de códigos

Lectores USB tipo teclado y cámara de tablet. Recepción, desposte (escanear lote) y despacho aceptan escaneo en cualquier campo de búsqueda.

### 7.3 Sensores de temperatura

Endpoint de ingesta (`/api/v1/ingesta/temperaturas/`) con token por dispositivo; opcionalmente broker MQTT. Los registros entran como `RegistroTemperatura` con `fuente = sensor` y disparan las mismas alertas de la Fase 5. Alerta adicional de "puerta abierta demasiado tiempo" si el sensor lo informa.

### 7.4 API pública y conectores

| Integración | Alcance |
| --- | --- |
| API REST pública | Tokens por empresa con permisos, límite de solicitudes, documentación OpenAPI (`drf-spectacular`), webhooks de eventos (venta, lote bloqueado, orden aprobada) |
| Impulso Smart | Opcional: compartir catálogo, ventas y stock por API; ninguno de los dos productos depende del otro para funcionar |
| POS de terceros | Recibir ventas para descontar stock |
| Tiendanube / Mercado Libre | Sincronizar productos, stock disponible y pedidos |
| WhatsApp (Cloud API de Meta) | Envío de alertas críticas y consultas simples de stock |
| Power BI | Vistas de reporte de solo lectura (esquema `reportes`) o réplica de lectura; nunca acceso a tablas operativas |

### 7.5 Inteligencia artificial y análisis

Se implementa en este orden, de menor a mayor complejidad:

1. **Detección de anomalías de rendimiento:** gráficos de control por corte (media ± 3 desvíos de los últimos N despostes) por proveedor, operario y rango de peso. Es estadística simple y da valor inmediato.
2. **Predicción de demanda y reposición** por producto y sucursal (modelos de series de tiempo con estacionalidad semanal), con sugerencia de orden de compra.
3. **Predicción de vencimientos:** lotes que no se van a vender antes de vencer al ritmo actual.
4. **Análisis de proveedores:** rendimiento, diferencias de peso, costo y no conformidades combinados.
5. **Asistente de consultas:** preguntas en lenguaje natural respondidas sobre un conjunto cerrado de consultas predefinidas y seguras, siempre filtradas por la empresa del usuario.

Reglas: la IA **sugiere y señala patrones**, no atribuye culpas. Los textos se redactan como "el rendimiento de pechuga bajó X % respecto del período anterior; revisar peso de recepción, proveedor y procedimiento". Ningún modelo modifica datos ni ejecuta acciones por sí solo. Se requiere un mínimo de historia (ej.: 30 órdenes) antes de mostrar una conclusión.

### Criterios de aceptación

- [ ] Con la balanza conectada, un pesaje llega a la pantalla del operario en menos de 1 segundo desde que el peso se estabiliza.
- [ ] Si el agente local pierde conexión, la pantalla avisa y permite pesaje manual marcado como tal.
- [ ] Una etiqueta de peso variable impresa en desposte se lee en el mostrador y carga producto y peso correctos.
- [ ] Un sensor que reporta fuera de rango genera la misma alerta que un registro manual.
- [ ] Un token de API de la empresa A no puede leer nada de la empresa B.
- [ ] Con 60 despostes de prueba donde un operario tiene rendimiento de pechuga 4 puntos menor, el análisis lo señala como desvío a revisar, sin calificarlo.

## Fase 8 — Comercialización SaaS

**Objetivo:** que un cliente nuevo pueda contratar, configurarse por rubro y empezar a operar con mínima intervención, y que el negocio se pueda cobrar, soportar y escalar.

### Alcance

| Componente | Especificación |
| --- | --- |
| Planes | Inicial, Profesional, Productivo, Empresarial, Premium (punto 19). Cada plan = conjunto de `ModuloEmpresa` + límites (sucursales, usuarios, órdenes/mes, almacenamiento) |
| Suscripciones | Alta, cambio de plan, prueba gratuita, suspensión por falta de pago con período de gracia y solo lectura (nunca borrado de datos). Cobro recurrente con una pasarela local (ej.: Mercado Pago) |
| Onboarding por rubro | Asistente que precarga plantillas: pollería (catálogo de cortes de pollo, plantilla de desposte, cámaras), carnicería (vacuno y porcino), restaurante (recetas base), comercio (solo inventario) |
| Importación | Productos, proveedores, clientes y stock inicial desde CSV/Excel, con validación previa y reporte de errores |
| Portal de clientes | Facturas del servicio, plan, usuarios, tickets de soporte, base de conocimiento |
| Administración interna | Panel de empresas, uso por empresa, métricas de negocio (clientes activos, bajas, ingreso mensual recurrente) |
| Operación | Backups diarios con restauración probada, exportación completa de datos por empresa, monitoreo de errores (ej.: Sentry), registros centralizados |
| Legal | Términos y condiciones, política de privacidad conforme a la Ley 25.326 de protección de datos personales, acuerdo de nivel de servicio |
| Seguridad | MFA obligatorio para Dueño y Administrador, revisión de permisos, prueba de penetración básica antes del lanzamiento |

Los precios no se fijan en esta fase de especificación: dependen de la matriz de costos de implementación, alojamiento, soporte y mantenimiento que pide la investigación.

### Criterios de aceptación

- [ ] Una pollería nueva se registra, elige rubro y en menos de 30 minutos tiene catálogo de cortes, plantilla de desposte y depósitos listos.
- [ ] Bajar de plan Productivo a Profesional oculta Desposte pero conserva todo el historial.
- [ ] Una empresa suspendida puede consultar y exportar sus datos, pero no registrar operaciones.
- [ ] Restaurar el backup de una empresa en un entorno de prueba reproduce saldos y Kardex exactos.
- [ ] La importación de un CSV con 3 filas inválidas sobre 500 muestra esas 3 y no carga nada hasta corregirlas o descartarlas.

## Anexos

### A. Matriz de pruebas de punta a punta

Escenarios del punto 18 de la investigación, con la fase en que deben pasar. Se automatizan como tests de integración y se corren en cada release.

| Escenario | Resultado esperado | Fase |
| --- | --- | --- |
| Compra y recepción de 100 pollos | Stock y lote correctos, costo con flete | 2 |
| Desposte de 100 pollos en 7 cortes | Descuento de materia prima, alta de lotes de cortes vinculados | 3 |
| Diferencia de pesaje | No se aprueba hasta clasificarla | 3 |
| Merma de proceso normal y anormal | Normal absorbida, anormal a pérdida, total cuadra al centavo | 3 |
| Reversión de desposte | Saldos y costos vuelven exactos | 3 |
| Pechuga → milanesas | Trazabilidad pollo → pechuga → milanesa | 4 |
| Carcasa → caldo | Subproducto usado como insumo, con costo y lote | 4 |
| Lote contaminado | Retiro bloquea todos los derivados en todas las sucursales | 5 |
| Lote vencido | Ninguna operación lo usa salvo descarte | 2 |
| Venta de pechugas | Descuento del lote FEFO, costo congelado | 6 |
| Transferencia entre sucursales | Descuento y alta en la misma transacción | 1 |
| Dos usuarios operando el mismo lote | Sin duplicación ni stock negativo accidental | 1 y 3 |
| Inventario físico y cierre | Ajuste único, conciliación sin diferencias | 2 |
| Aislamiento entre empresas | Cero acceso cruzado por web, API y token | 0 en adelante |

### B. Preguntas abiertas

1. **Dónde funciona:** se asumió 100% nube con agente local para balanzas. ¿Hace falta modo sin conexión en el mostrador o la sala de desposte?
2. **Clientes piloto:** ¿qué 1 o 2 pollerías probarán la Fase 3? De ellas salen las plantillas reales, los rendimientos estándar y los modelos de balanza.
3. **ARCA:** ¿integración directa con los web services o mediante un proveedor intermediario?
4. **Método de costeo por defecto:** ¿A (peso) o B (valor de venta) para empresas nuevas? Recomendación: B para pollerías, con A disponible.
5. **Hosting:** proveedor de nube y región (latencia desde Argentina y costo en dólares).
6. **Impulso Smart:** ¿qué datos concretos se comparten (catálogo, ventas, clientes) y en qué sentido?
7. **Normativa provincial y municipal:** relevar requisitos de las jurisdicciones de los primeros clientes.
8. **Fraccionamiento (almacén):** ¿se suma ahora el fraccionamiento de sueltos (bolsa de 25 kg → paquetes de 1 kg)? Con el motor de la Fase 3 ya construido, es una plantilla más.
9. **Exhibición de precios:** confirmar qué exige la normativa vigente para las etiquetas de góndola (precio por unidad de medida, formato, tamaño).
10. **Catálogo maestro de códigos:** ¿vale la pena una base compartida de productos por EAN entre empresas para acelerar el alta, o cada empresa carga lo suyo?

### C. Glosario

| Término | Significado en este sistema |
| --- | --- |
| Kardex | Registro cronológico e inmutable de todos los movimientos de un producto, con saldo y costo |
| Lote | Partida identificada de un producto con origen, vencimiento y costo propios |
| FEFO | Primero en vencer, primero en salir |
| Costo conjunto | Costo total de la materia prima de una orden, antes de repartirlo entre los productos |
| Merma normal / anormal | Pérdida dentro / por encima del estándar de la plantilla |
| Subproducto | Resultado aprovechable de menor valor (huesos, grasa, carcasa, recortes) |
| Plantilla de transformación | Definición de qué entra, qué sale y con qué rendimiento esperado |
| Contramovimiento | Movimiento que anula otro sin borrarlo |
| POES / BPM / HACCP | Procedimientos de saneamiento / Buenas prácticas de manufactura / Análisis de peligros y puntos críticos |
