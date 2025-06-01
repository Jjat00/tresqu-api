# Sistema de Mensajería Masiva WhatsApp - Manejo de Errores 131047

## 📋 Resumen del Problema

El error **131047 "Re-engagement message"** ocurre cuando intentas enviar un mensaje de texto normal a un usuario que no ha respondido en más de 24 horas. WhatsApp Business API requiere usar **plantillas de mensaje aprobadas** para contactar usuarios después de este período.

## 🔧 Soluciones Implementadas

### 1. Mejora en el Manejo de Errores

Se ha mejorado la función `process_meta_message_status()` para detectar y registrar específicamente los errores 131047 y otros códigos comunes:

```python
# Errores específicos que ahora se manejan:
- 131047: Re-engagement message (requiere plantilla)
- 131026: Message Undeliverable (número inválido)
- 131048: Spam Rate Limit (demasiados mensajes spam)
- 131049: Ecosystem Health (Meta no entregó)
- 131042: Payment Issue (problema de pago)
- 130429, 131056, 80007: Rate Limits (límites de velocidad)
```

### 2. Soporte para Plantillas de Mensaje

Se ha actualizado `send_meta_whatsapp_message()` para soportar plantillas:

```python
# Envío con plantilla (RECOMENDADO para evitar 131047)
send_meta_whatsapp_message(
    phone_number="573001234567",
    message_text="",
    use_template=True,
    template_name="reminder_daily",
    template_language="es",
    template_params=["Juan"]
)

# Envío con texto normal (puede generar 131047)
send_meta_whatsapp_message(
    phone_number="573001234567",
    message_text="¡Hola! Recuerda registrar tus gastos."
)
```

### 3. API Endpoints Actualizados

#### A. Envío Masivo con Plantillas

**Endpoint:** `POST /whatsapp/send-mass-message/`

```json
{
  "use_template": true,
  "template_name": "reminder_daily",
  "template_language": "es",
  "template_params": ["Usuario"],
  "platform": "WHATSAPP",
  "dry_run": false
}
```

#### B. Envío de Plantillas a Números Específicos

**Endpoint:** `POST /whatsapp/send-template/`

```json
{
  "template_name": "welcome_message",
  "template_language": "es",
  "template_params": ["Juan", "Tresqu"],
  "phone_numbers": ["573001234567", "573007654321"],
  "dry_run": false
}
```

## 📊 Tipos de Plantillas Recomendadas

### 1. Plantilla de Recordatorio Diario

```
Nombre: reminder_daily
Categoría: UTILITY
Contenido: "¡Hola {{1}}! 👋 Recuerda registrar tus gastos de hoy en Tresqu para mantener el control de tus finanzas. 💰"
```

### 2. Plantilla de Bienvenida

```
Nombre: welcome_message
Categoría: UTILITY
Contenido: "¡Bienvenido a {{2}}, {{1}}! 🎉 Estamos aquí para ayudarte a gestionar tus finanzas de manera inteligente."
```

### 3. Plantilla de Resumen Semanal

```
Nombre: weekly_summary
Categoría: UTILITY
Contenido: "📊 Hola {{1}}, es momento de revisar tu resumen semanal en {{2}}. ¿Cómo van tus finanzas esta semana?"
```

## 🚀 Cómo Usar las Nuevas Funcionalidades

### 1. Para Mensajes Masivos (RECOMENDADO)

```bash
curl -X POST http://localhost:8000/whatsapp/send-mass-message/ \
  -H "Authorization: Bearer admin_secret_key" \
  -H "Content-Type: application/json" \
  -d '{
    "use_template": true,
    "template_name": "reminder_daily",
    "template_language": "es",
    "template_params": ["Usuario"],
    "platform": "WHATSAPP",
    "dry_run": false
  }'
```

### 2. Para Vista Previa (Dry Run)

```bash
curl -X POST http://localhost:8000/whatsapp/send-mass-message/ \
  -H "Authorization: Bearer admin_secret_key" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Mensaje de prueba para {name}",
    "platform": "WHATSAPP",
    "dry_run": true
  }'
```

### 3. Para Números Específicos

```bash
curl -X POST http://localhost:8000/whatsapp/send-template/ \
  -H "Authorization: Bearer admin_secret_key" \
  -H "Content-Type: application/json" \
  -d '{
    "template_name": "welcome_message",
    "template_language": "es",
    "template_params": ["Juan", "Tresqu"],
    "phone_numbers": ["573001234567"]
  }'
```

## ⚠️ Mejores Prácticas

### 1. Cuándo Usar Plantillas

- ✅ **Siempre** para mensajes masivos
- ✅ Para usuarios que no han respondido en 24+ horas
- ✅ Para campañas de marketing
- ✅ Para recordatorios automáticos

### 2. Cuándo Usar Texto Normal

- ✅ Respuestas dentro de la ventana de 24 horas
- ✅ Conversaciones activas
- ✅ Mensajes de soporte en tiempo real

### 3. Configuración de Plantillas en Meta

1. Ve a **Meta Business Manager**
2. Selecciona tu **WhatsApp Business Account**
3. Ve a **Message Templates**
4. Crea plantillas con categoría **UTILITY** para mejor aprobación
5. Espera la aprobación (puede tomar 24-48 horas)

## 📈 Monitoreo de Errores

Los logs ahora incluyen información detallada sobre errores:

```
❌ Error en mensaje Meta - ID: wamid.xxx, Para: 573001234567, Código: 131047
🔄 Error 131047 (Re-engagement) para 573001234567: Se requiere plantilla de mensaje para contactar después de 24h
```

## 🔍 Troubleshooting

### Error 131047 - Re-engagement Message

**Causa:** Usuario no ha respondido en 24+ horas
**Solución:** Usar plantilla de mensaje aprobada

### Error 131026 - Message Undeliverable

**Causa:** Número no válido o usuario no acepta términos
**Solución:** Verificar número y pedir al usuario actualizar WhatsApp

### Error 131048 - Spam Rate Limit

**Causa:** Demasiados mensajes marcados como spam
**Solución:** Reducir frecuencia y mejorar calidad del contenido

### Error 131049 - Ecosystem Health

**Causa:** Meta no entregó para mantener ecosistema saludable
**Solución:** Esperar y reintentar con intervalos crecientes

## 📝 Archivos Modificados

1. **`whatsappbot/views.py`**

   - Mejorado `process_meta_message_status()`
   - Actualizado `send_meta_whatsapp_message()`
   - Nuevas funciones para plantillas

2. **`whatsappbot/urls.py`**

   - Agregada ruta `/send-template/`

3. **`whatsappbot/example_usage.py`**
   - Ejemplos de uso completos

## 🎯 Próximos Pasos

1. **Crear plantillas en Meta Business Manager**
2. **Probar con dry_run=true primero**
3. **Monitorear logs para errores**
4. **Ajustar estrategia según resultados**

## 📞 Soporte

Para más información sobre plantillas de WhatsApp:

- [Documentación oficial de Meta](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates)
- [Códigos de error de WhatsApp](https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes/)

---

**Nota:** Las plantillas deben ser aprobadas por Meta antes de poder usarse. El proceso de aprobación puede tomar 24-48 horas.
