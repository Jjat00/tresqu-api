# Guía de Mensajes Masivos para WhatsApp

Esta guía te ayudará a enviar mensajes masivos a todos los usuarios registrados de WhatsApp en tu bot de Tresqu.

## 🚀 Métodos Disponibles

### 1. Comando de Django (Recomendado)

#### Envío Básico

```bash
# Mensaje personalizado
python manage.py send_mass_message --message "¡Hola! Recuerda registrar tus gastos de hoy 💰"

# Usando template predefinido
python manage.py send_mass_message --template reminder

# Vista previa sin enviar
python manage.py send_mass_message --template reminder --dry-run
```

#### Opciones Avanzadas

```bash
# Enviar solo a usuarios de WhatsApp con delay personalizado
python manage.py send_mass_message \
  --message "Mensaje personalizado" \
  --platform WHATSAPP \
  --delay 3

# Enviar a todas las plataformas
python manage.py send_mass_message \
  --message "Mensaje para todos" \
  --platform ALL
```

### 2. Recordatorios Automáticos

#### Recordatorios Diarios

```bash
# Recordatorio diario básico
python manage.py send_daily_reminders

# Con template específico
python manage.py send_daily_reminders --template daily_summary

# Solo usuarios activos en los últimos 3 días
python manage.py send_daily_reminders --active-days 3

# Vista previa
python manage.py send_daily_reminders --dry-run
```

#### Programar con Cron

```bash
# Editar crontab
crontab -e

# Agregar línea para recordatorio diario a las 9:00 AM
0 9 * * * cd /ruta/a/tu/proyecto && python manage.py send_daily_reminders --template daily_summary

# Recordatorio semanal los lunes a las 10:00 AM
0 10 * * 1 cd /ruta/a/tu/proyecto && python manage.py send_daily_reminders --template weekly_reminder --active-days 14
```

### 3. API Endpoints

#### Envío Masivo via API

```bash
curl -X POST "https://tresqu.com/whatsapp/send-mass-message/" \
  -H "Authorization: Bearer tu_admin_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "¡Hola! Recuerda registrar tus gastos 💰",
    "platform": "WHATSAPP"
  }'
```

#### Vista Previa via API

```bash
curl -X POST "https://tresqu.com/whatsapp/send-mass-message/" \
  -H "Authorization: Bearer tu_admin_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "template": "reminder",
    "platform": "WHATSAPP",
    "dry_run": true
  }'
```

#### Programar Recordatorios

```bash
curl -X POST "https://tresqu.com/whatsapp/schedule-reminders/" \
  -H "Authorization: Bearer tu_admin_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "daily",
    "template": "daily_summary"
  }'
```

## 📝 Templates Disponibles

### `reminder` - Recordatorio General

```
🔔 *Recordatorio de Tresqu*

¡Hola! 👋 Esperamos que tengas un excelente día.

💰 Recuerda registrar tus gastos e ingresos de hoy para mantener
un control perfecto de tus finanzas.

Simplemente envíame un mensaje como:
• "Gasté 25000 en almuerzo"
• "Compré café por 5000"
• "Gané 50000 en mi negocio"

📊 Revisa tu dashboard en https://tresqu.com

¡Tus finanzas bajo control! 💪
```

### `welcome` - Mensaje de Bienvenida

```
🎉 *¡Bienvenido a Tresqu!*

Gracias por unirte a nuestra comunidad de control financiero.
Estamos aquí para ayudarte a gestionar tus gastos e ingresos de manera inteligente.

💡 Puedes empezar registrando tus movimientos enviándome mensajes como:
• "Gasté 30000 en supermercado"
• "Recibí 100000 de mi trabajo"

¡Comencemos este viaje financiero juntos! 🚀
```

### `daily_summary` - Resumen Diario

```
📊 *Resumen Diario de Tresqu*

¡Hola {name}! 👋

Es un buen momento para revisar tus finanzas del día:

💰 ¿Registraste todos tus gastos de hoy?
💵 ¿Anotaste tus ingresos?

📱 Revisa tu dashboard: https://tresqu.com

¡Mantén el control de tus finanzas! 🎯
```

### `weekly_reminder` - Recordatorio Semanal

```
📅 *Recordatorio Semanal de Tresqu*

¡Hola {name}! 👋

Es momento de revisar tu semana financiera:

📈 ¿Cómo van tus gastos esta semana?
💡 ¿Hay algún patrón que notes?
🎯 ¿Estás cumpliendo tus metas?

📊 Revisa tu resumen semanal: https://tresqu.com

¡Sigue así! 💪
```

## ⚙️ Configuración

### Variables de Entorno

```bash
# En tu archivo .env
ADMIN_API_KEY=tu_clave_secreta_aqui
META_WHATSAPP_ACCESS_TOKEN=tu_token_de_meta
META_WHATSAPP_PHONE_NUMBER_ID=tu_phone_number_id
```

### Límites y Consideraciones

- **Rate Limiting**: Se incluye un delay de 2-3 segundos entre mensajes
- **Usuarios Activos**: Por defecto, solo se envía a usuarios activos en los últimos 7 días
- **Máximo por Ejecución**: 100 usuarios por defecto (configurable)
- **Personalización**: Los mensajes pueden incluir `{name}` para personalizar

## 📊 Monitoreo

### Ver Logs

```bash
# Logs en tiempo real
tail -f debug.log | grep "Meta"

# Logs de mensajes masivos
tail -f debug.log | grep "masivo"
```

### Verificar Usuarios

```bash
# Ver cuántos usuarios de WhatsApp tienes
python manage.py shell -c "
from users.models import User
whatsapp_users = User.objects.filter(platform__in=['WHATSAPP', 'MULTIPLTAFORMA'], phone_number__isnull=False).exclude(phone_number='')
print(f'Total usuarios WhatsApp: {whatsapp_users.count()}')
"
```

## 🛡️ Seguridad

- Usa una `ADMIN_API_KEY` fuerte y única
- Los endpoints requieren autenticación
- Los comandos de Django requieren acceso al servidor
- Siempre usa `--dry-run` para probar antes de enviar

## 📱 Ejemplos de Uso Práctico

### Recordatorio Matutino

```bash
# Cron job para las 9:00 AM todos los días
0 9 * * * cd /home/usuario/cashbot-api && python manage.py send_daily_reminders --template daily_summary --active-days 7
```

### Recordatorio de Fin de Semana

```bash
# Viernes a las 6:00 PM
0 18 * * 5 cd /home/usuario/cashbot-api && python manage.py send_mass_message --template weekly_reminder
```

### Mensaje de Emergencia

```bash
# Envío inmediato a todos los usuarios
python manage.py send_mass_message --message "🚨 Mantenimiento programado hoy de 2-4 PM. El bot estará temporalmente fuera de servicio." --platform ALL
```

## 🆘 Solución de Problemas

### Error: "No se encontraron usuarios"

- Verifica que tienes usuarios registrados con números de WhatsApp
- Usa `--platform ALL` para incluir todas las plataformas

### Error: "No autorizado" en API

- Verifica que `ADMIN_API_KEY` esté configurada correctamente
- Asegúrate de enviar el header `Authorization: Bearer tu_api_key`

### Mensajes no se envían

- Verifica la configuración de Meta WhatsApp API
- Revisa los logs para errores específicos
- Prueba con `--dry-run` primero

### Rate Limiting

- Aumenta el `--delay` entre mensajes
- Reduce `--max-users` por ejecución
- Programa envíos en horarios de menor tráfico
