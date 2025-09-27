# 🔗 Sistema de Links de Referidos - Documentación

## 📋 Descripción General

El sistema de links de referidos permite rastrear de dónde llegan los nuevos usuarios a través de enlaces únicos generados para empresas, campañas o partners externos. Cada enlace tiene un código único que se embebe en mensajes de WhatsApp para identificar el origen de los usuarios.

## 🏗️ Arquitectura del Sistema

### Modelos Principales

#### 1. `TrackingLink`

Almacena información de enlaces de seguimiento para empresas/partners.

```python
class TrackingLink(models.Model):
    code = models.CharField(max_length=50, unique=True)  # Código único
    name = models.CharField(max_length=200)              # Nombre descriptivo
    description = models.TextField(blank=True)           # Descripción opcional
    is_active = models.BooleanField(default=True)        # Estado activo/inactivo
    expires_at = models.DateTimeField(null=True)         # Fecha de expiración
    total_registrations = models.PositiveIntegerField(default=0)  # Contador automático
    created_at = models.DateTimeField(auto_now_add=True)
```

#### 2. Campo en `User`

Cada usuario puede tener asociado un enlace de origen.

```python
class User(models.Model):
    # ... otros campos ...
    source_tracking_link = models.ForeignKey(
        TrackingLink,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users'
    )
```

## 🚀 Uso del Sistema

### 1. Crear Enlaces de Referido

#### Desde Django Admin

1. Ve a **Admin Panel** → **Enlaces de Seguimiento**
2. Clic en **"Agregar Enlace de Seguimiento"**
3. Completa los campos:
   - **Nombre**: "Campaña Banco XYZ"
   - **Código**: Se genera automáticamente o puedes especificar uno
   - **Descripción**: Información adicional (opcional)
   - **Activo**: ✅ Marcado
   - **Expira en**: Fecha opcional

#### Desde API

```bash
POST /api/tracking-links/
Content-Type: application/json

{
    "name": "Campaña Banco XYZ",
    "description": "Campaña de marketing con Banco XYZ para Q1 2024",
    "code": "banco_xyz_q1_2024",  // Opcional, se genera automáticamente
    "is_active": true,
    "expires_at": "2024-12-31T23:59:59Z"  // Opcional
}
```

### 2. Obtener Enlaces Generados

#### Desde Django Admin

- Al crear/editar un enlace, verás la sección **"Enlaces Generados"**
- Muestra el enlace de WhatsApp listo para compartir
- Incluye el código de referido embebido

#### Desde API

```bash
GET /api/tracking-links/{id}/generate_links/

# Respuesta:
{
    "tracking_link": {
        "id": 1,
        "code": "banco_xyz_q1_2024",
        "name": "Campaña Banco XYZ",
        "whatsapp_link": "https://wa.me/573001234567?text=Hola,%20vengo%20de%20BANCO_XYZ_Q1_2024"
    },
    "generated_links": {
        "whatsapp": "https://wa.me/573001234567?text=Hola,%20vengo%20de%20BANCO_XYZ_Q1_2024",
        "telegram": "https://t.me/tu_bot?start=banco_xyz_q1_2024",
        "direct_code": "banco_xyz_q1_2024"
    },
    "instructions": {
        "whatsapp": "Comparte este enlace para que los usuarios lleguen directamente a WhatsApp con el código",
        "telegram": "Comparte este enlace para que los usuarios lleguen directamente a Telegram con el código",
        "direct_code": "Los usuarios pueden usar este código manualmente durante el registro"
    }
}
```

## 📊 Analytics y Estadísticas

### Métricas Disponibles

#### Resumen General

```bash
GET /api/tracking-links/summary/

# Respuesta:
{
    "summary": {
        "total_links": 15,
        "active_links": 12,
        "inactive_links": 3,
        "total_registrations": 1247
    },
    "top_performing_links": [
        {
            "id": 1,
            "code": "banco_xyz_q1_2024",
            "name": "Campaña Banco XYZ",
            "total_registrations": 342
        }
    ]
}
```

#### Estadísticas Detalladas por Enlace

```bash
GET /api/tracking-links/{id}/stats/

# Respuesta:
{
    "id": 1,
    "code": "banco_xyz_q1_2024",
    "name": "Campaña Banco XYZ",
    "total_registrations": 342,
    "conversion_rate": 15.2,
    "recent_registrations": [
        {
            "id": 1001,
            "name": "Juan Pérez",
            "platform": "WHATSAPP",
            "registered_at": "2024-01-15T10:30:00Z"
        }
    ]
}
```

### Filtros Disponibles

```bash
# Filtrar por estado
GET /api/tracking-links/?is_active=true

# Buscar por código
GET /api/tracking-links/?code=banco_xyz

# Buscar por nombre
GET /api/tracking-links/?name=campaña
```

## 🔧 Configuración

### Variables de Configuración

En tu `settings.py`:

```python
# Número del bot de WhatsApp (requerido)
WHATSAPP_BOT_NUMBER = "573001234567"  # Cambia por tu número real

# Opcional: Configuraciones adicionales
REFERRAL_LINKS_DEFAULT_EXPIRY_DAYS = 365  # Días por defecto para expiración
REFERRAL_LINKS_MAX_PER_USER = 10          # Máximo de enlaces por usuario admin
```

### Personalización de Mensajes

El mensaje predefinido se puede personalizar modificando el método `get_whatsapp_link()` en el modelo `TrackingLink`:

```python
# En users/models.py
def get_whatsapp_link(self, bot_phone_number):
    # Personalizar el mensaje aquí
    message = f"Hola, vengo de {self.code.upper()}"
    # O usar mensajes más específicos:
    # message = f"¡Hola! Me interesa conocer más sobre Tresqu. Vengo de {self.name}"

    return f"https://wa.me/{clean_phone}?text={message.replace(' ', '%20')}"
```

## 🔐 Permisos y Seguridad

### Permisos de API

- **Crear/Editar/Eliminar enlaces**: Requiere autenticación (`IsAuthenticated`)
- **Ver estadísticas**: Requiere autenticación
- **Listar enlaces**: Requiere autenticación

### Validaciones

- **Códigos únicos**: El sistema garantiza que no haya códigos duplicados
- **Códigos seguros**: Se generan usando `secrets.token_hex()` para evitar predicciones
- **Sanitización**: Los códigos se limpian automáticamente (solo alfanuméricos y guiones bajos)

## 📱 Integración con WhatsApp

### Flujo Completo

1. **Empresa recibe enlace**:

   ```
   https://wa.me/573001234567?text=Hola,%20vengo%20de%20BANCO_XYZ_Q1_2024
   ```

2. **Usuario hace clic**: Se abre WhatsApp con mensaje predefinido

3. **Usuario envía mensaje**: "Hola, vengo de BANCO_XYZ_Q1_2024"

4. **Bot detecta código**: Durante el proceso de registro (Paso 2 - próximamente)

5. **Usuario se asocia**: Se vincula automáticamente con el `TrackingLink`

6. **Contador se incrementa**: `total_registrations` aumenta automáticamente

## 🛠️ API Endpoints Completos

### Enlaces de Seguimiento

| Método   | Endpoint                                   | Descripción                    |
| -------- | ------------------------------------------ | ------------------------------ |
| `GET`    | `/api/tracking-links/`                     | Listar todos los enlaces       |
| `POST`   | `/api/tracking-links/`                     | Crear nuevo enlace             |
| `GET`    | `/api/tracking-links/{id}/`                | Obtener enlace específico      |
| `PUT`    | `/api/tracking-links/{id}/`                | Actualizar enlace              |
| `DELETE` | `/api/tracking-links/{id}/`                | Eliminar enlace                |
| `GET`    | `/api/tracking-links/{id}/stats/`          | Estadísticas detalladas        |
| `POST`   | `/api/tracking-links/{id}/toggle_active/`  | Activar/desactivar enlace      |
| `GET`    | `/api/tracking-links/{id}/generate_links/` | Generar enlaces para compartir |
| `GET`    | `/api/tracking-links/summary/`             | Resumen general                |

### Ejemplos de Uso

#### Crear Enlace

```bash
curl -X POST http://localhost:8000/api/tracking-links/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "name": "Campaña Facebook Ads",
    "description": "Campaña publicitaria en Facebook para enero 2024"
  }'
```

#### Obtener Estadísticas

```bash
curl -X GET http://localhost:8000/api/tracking-links/1/stats/ \
  -H "Authorization: Bearer YOUR_TOKEN"
```

#### Activar/Desactivar Enlace

```bash
curl -X POST http://localhost:8000/api/tracking-links/1/toggle_active/ \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 🔄 Próximos Pasos

### Paso 2: Integración con Registro de Usuarios

- Modificar el proceso de registro en WhatsApp para capturar códigos automáticamente
- Asociar usuarios nuevos con sus enlaces de origen
- Incrementar contadores automáticamente

### Mejoras Futuras

- Dashboard web para visualizar estadísticas
- Reportes en PDF/Excel
- Webhooks para notificaciones en tiempo real
- Integración con Google Analytics
- A/B testing de mensajes predefinidos

## 🐛 Troubleshooting

### Problemas Comunes

#### 1. Enlaces no se generan correctamente

**Problema**: El enlace de WhatsApp no funciona
**Solución**: Verificar que `WHATSAPP_BOT_NUMBER` esté configurado correctamente en settings.py

#### 2. Códigos duplicados

**Problema**: Error al crear enlace con código existente
**Solución**: El sistema genera códigos únicos automáticamente. Si especificas uno manual, asegúrate de que sea único.

#### 3. Estadísticas no se actualizan

**Problema**: Los contadores no aumentan
**Solución**: Esto se implementará en el Paso 2. Por ahora, los contadores se pueden actualizar manualmente.

## 📞 Soporte

Para reportar bugs o solicitar nuevas funcionalidades, crear un issue en el repositorio del proyecto.

---

**Versión**: 1.0  
**Última actualización**: Enero 2024  
**Estado**: ✅ Paso 1 Completado - Listo para Paso 2 (Integración con Registro)
