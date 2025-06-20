# Guía de Mensajes de Voz - WhatsApp Bot

## 📢 Nueva Funcionalidad: Procesamiento de Mensajes de Voz

El bot de WhatsApp ahora puede procesar mensajes de voz utilizando la API oficial de Meta WhatsApp y OpenAI Whisper para transcripción.

## 🔧 Configuración Requerida

### Variables de Entorno

Asegúrate de tener configuradas las siguientes variables de entorno:

```bash
# Token de acceso de Meta WhatsApp API
META_WHATSAPP_ACCESS_TOKEN=tu_token_aqui

# ID del número de teléfono de WhatsApp Business
META_WHATSAPP_PHONE_NUMBER_ID=tu_phone_id_aqui

# ID de la cuenta de negocio de WhatsApp
META_WHATSAPP_BUSINESS_ACCOUNT_ID=tu_waba_id_aqui

# Token de verificación del webhook (opcional)
META_WHATSAPP_VERIFY_TOKEN=mi_token_secreto

# Clave API de OpenAI para transcripción
OPENAI_API_KEY=tu_openai_api_key_aqui
```

## 🎯 Cómo Funciona

### 1. Recepción del Mensaje de Voz

- El webhook de Meta WhatsApp recibe el mensaje de voz
- Se extrae el `media_id` del archivo de audio
- Se identifica el tipo de mensaje como `voice` o `audio`

### 2. Procesamiento del Audio

1. **Descarga**: Se descarga el archivo de audio usando la Meta Graph API
2. **Transcripción**: Se usa OpenAI Whisper para convertir audio a texto
3. **Procesamiento**: El texto transcrito se procesa como un mensaje normal
4. **Limpieza**: Se elimina el archivo temporal de audio

### 3. Flujo de Usuario

```
Usuario envía mensaje de voz
↓
Bot responde: "🎧 Procesando tu mensaje de voz..."
↓
Descarga y transcripción del audio
↓
Procesamiento del texto transcrito
↓
Respuesta del bot basada en el contenido
```

## 📋 Tipos de Audio Soportados

- **Mensajes de voz**: Grabaciones directas desde WhatsApp
- **Archivos de audio**: Archivos de audio enviados como documentos
- **Formatos**: OGG, MP3, WAV (automáticamente detectados)

## 🔍 Manejo de Errores

### Errores Comunes y Respuestas

1. **Sin acceso al archivo**:

   - Mensaje: "Lo siento, no pude acceder al archivo de audio. Por favor, intenta de nuevo."

2. **Error de configuración**:

   - Mensaje: "Lo siento, hay un problema de configuración. Por favor, contacta al soporte."

3. **Error de descarga**:

   - Mensaje: "Lo siento, no pude descargar el archivo de audio. Por favor, intenta de nuevo."

4. **Transcripción fallida**:
   - Mensaje: "Lo siento, no pude entender el audio. Por favor, intenta de nuevo con un mensaje de texto o un audio más claro."

## 🚀 Implementación Técnica

### Funciones Principales

#### `download_whatsapp_media(media_id, access_token)`

- Descarga archivos de media de WhatsApp usando la Graph API
- Maneja diferentes tipos MIME de audio
- Retorna la ruta del archivo temporal

#### `transcribe_audio(audio_file_path)`

- Usa OpenAI Whisper para transcribir audio a texto
- Maneja errores de transcripción
- Limpia archivos temporales automáticamente

### Flujo en el Código

```python
# En whatsappbot/bot.py
if message_type in ["audio", "voice", "ptt"]:
    # 1. Enviar mensaje de procesamiento
    # 2. Descargar audio usando Meta API
    # 3. Transcribir con OpenAI Whisper
    # 4. Procesar texto transcrito
    # 5. Limpiar archivos temporales
```

## 📊 Logging y Monitoreo

### Logs Importantes

- `"Procesando mensaje de voz de {sender_number}"`
- `"Transcripción exitosa: {transcription}"`
- `"Error procesando mensaje de voz: {error}"`

### Métricas a Monitorear

- Tasa de éxito de transcripción
- Tiempo de procesamiento de audio
- Errores de descarga de media
- Uso de tokens de OpenAI

## 🔒 Consideraciones de Seguridad

1. **Archivos Temporales**: Se eliminan automáticamente después del procesamiento
2. **Tokens de Acceso**: Almacenados como variables de entorno
3. **Validación**: Se valida el media_id antes de procesar

## 💡 Consejos de Uso

### Para Usuarios

- Habla claro y despacio para mejor transcripción
- Evita ruido de fondo
- Mensajes cortos tienen mejor precisión

### Para Desarrolladores

- Monitorea el uso de la API de OpenAI
- Implementa límites de duración de audio si es necesario
- Considera cachear transcripciones para audios similares

## 🆕 Diferencias con Telegram

| Característica  | WhatsApp       | Telegram         |
| --------------- | -------------- | ---------------- |
| API de Descarga | Meta Graph API | Telegram Bot API |
| Transcripción   | OpenAI Whisper | OpenAI Whisper   |
| Formatos        | OGG, MP3, WAV  | OGG, MP3, WAV    |
| Procesamiento   | Asíncrono      | Asíncrono        |

## 🔧 Troubleshooting

### Problema: Token no configurado

```bash
# Verificar variables de entorno
echo $META_WHATSAPP_ACCESS_TOKEN
echo $OPENAI_API_KEY
```

### Problema: Error de permisos de Meta API

- Verificar que el token tenga permisos de `whatsapp_business_messaging`
- Confirmar que el número de teléfono esté verificado

### Problema: Error de transcripción

- Verificar que el archivo de audio sea válido
- Comprobar límites de la API de OpenAI
- Revisar formato de audio soportado

## 📈 Próximas Mejoras

- [ ] Soporte para múltiples idiomas en transcripción
- [ ] Límites de duración de audio
- [ ] Cache de transcripciones
- [ ] Métricas de uso detalladas
- [ ] Soporte para archivos de audio más grandes
