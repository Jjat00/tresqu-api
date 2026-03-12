# Guia de configuracion: Integracion Gmail

Guia paso a paso para configurar la integracion de Gmail en Tresqu. Esta integracion permite detectar correos de compras automaticamente y crear gastos en la app.

---

## Prerequisitos

- Cuenta de Google (Gmail)
- Acceso a [Google Cloud Console](https://console.cloud.google.com/)
- Backend de Tresqu corriendo (local o en produccion)
- Para desarrollo local: [ngrok](https://ngrok.com/) u otro tunel HTTP

---

## Paso 1: Crear o seleccionar un proyecto en Google Cloud

1. Ir a https://console.cloud.google.com/
2. En la barra superior, click en el selector de proyectos
3. Click **"Nuevo Proyecto"**
   - Nombre: `tresqu` (o el que se prefiera)
   - Click **"Crear"**
4. Asegurarse de que el proyecto quede seleccionado en la barra superior
5. Copiar el **Project ID** que aparece debajo del nombre (lo necesitaras para `GOOGLE_CLOUD_PROJECT_ID`)

---

## Paso 2: Habilitar las APIs necesarias

1. Ir a **APIs y servicios > Biblioteca** (o buscar "API Library" en la barra de busqueda)
2. Buscar y habilitar estas dos APIs:
   - **Gmail API** → Click "Habilitar"
   - **Cloud Pub/Sub API** → Click "Habilitar"

---

## Paso 3: Configurar la pantalla de consentimiento OAuth

1. Ir a **APIs y servicios > Pantalla de consentimiento OAuth**
2. Seleccionar **"Externo"** (para que cualquier usuario de Google pueda conectarse)
3. Llenar el formulario:
   - **Nombre de la app**: `Tresqu`
   - **Email de soporte**: tu email de desarrollador
   - **Logo**: (opcional)
   - **Dominio autorizado**: tu dominio de produccion (dejarlo vacio para desarrollo)
   - **Email de contacto del desarrollador**: tu email
4. Click **"Guardar y continuar"**
5. En **Scopes**: click "Agregar o quitar scopes"
   - Buscar y seleccionar: `https://www.googleapis.com/auth/gmail.readonly`
   - Click "Actualizar" y luego "Guardar y continuar"
6. En **Usuarios de prueba**: agregar los emails de Google que usaran la app
   - **Importante**: Mientras el proyecto este en modo "Testing", solo estos emails podran conectarse
7. Click **"Guardar y continuar"**

---

## Paso 4: Crear credenciales OAuth 2.0

1. Ir a **APIs y servicios > Credenciales**
2. Click **"+ Crear credenciales" > "ID de cliente de OAuth"**
3. Tipo de aplicacion: **"Aplicacion web"**
4. Nombre: `Tresqu Web Client`
5. En **URIs de redireccionamiento autorizados**, agregar:
   - `http://localhost:8000/api/gmail/oauth/callback/` (desarrollo)
   - `https://tu-dominio.com/api/gmail/oauth/callback/` (produccion)
6. Click **"Crear"**
7. **Copiar y guardar** el `Client ID` y `Client Secret`

Estos valores son las variables de entorno:
- `Client ID` → `GOOGLE_CLIENT_ID`
- `Client Secret` → `GOOGLE_CLIENT_SECRET`

---

## Paso 5: Crear el topic de Pub/Sub

1. Ir a **Pub/Sub > Temas** (o buscar "Pub/Sub" en la barra de busqueda)
2. Click **"+ Crear tema"**
3. ID del tema: `gmail-notifications`
   - Esto crea el recurso: `projects/{TU_PROJECT_ID}/topics/gmail-notifications`
4. Click **"Crear"**

Este valor es la variable de entorno `GOOGLE_PUBSUB_TOPIC`.

---

## Paso 6: Dar permisos al servicio de Gmail en el topic

Gmail necesita permiso para publicar notificaciones en el topic de Pub/Sub.

1. En la lista de temas, click en `gmail-notifications`
2. En el panel derecho, click en **"Permisos"** (o en la pestana "PERMISOS")
3. Click **"Agregar principal"**
4. En "Principales nuevos" escribir: `gmail-api-push@system.gserviceaccount.com`
5. Rol: **"Pub/Sub Publisher"** (`roles/pubsub.publisher`)
6. Click **"Guardar"**

---

## Paso 7: Crear la suscripcion push

La suscripcion push le dice a Pub/Sub que envie las notificaciones a tu webhook.

1. Ir a **Pub/Sub > Suscripciones**
2. Click **"+ Crear suscripcion"**
3. ID de suscripcion: `gmail-push-subscription`
4. Tema: seleccionar `gmail-notifications`
5. Tipo de entrega: **"Push"**
6. URL del extremo:
   - **Produccion**: `https://tu-dominio.com/gmail/webhook/`
   - **Desarrollo local**: necesitas un tunel publico (ver siguiente seccion)
7. Click **"Crear"**

### Desarrollo local con ngrok

Google Cloud necesita una URL publica para enviar las notificaciones push. En desarrollo local, usa ngrok:

```bash
# Instalar ngrok (si no lo tienes)
# https://ngrok.com/download

# Crear tunel al puerto 8000
ngrok http 8000
```

ngrok genera una URL tipo `https://abc123.ngrok-free.app`. Usa esta URL para la suscripcion push:

```
https://abc123.ngrok-free.app/gmail/webhook/
```

**Nota**: Cada vez que reinicias ngrok, la URL cambia. Tendras que actualizar la suscripcion push en Google Cloud Console.

---

## Paso 8: Generar la clave de cifrado

Los tokens de OAuth se almacenan cifrados en la base de datos. Necesitas generar una clave Fernet:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copia el resultado. Este valor es la variable de entorno `GMAIL_TOKEN_ENCRYPTION_KEY`.

**Importante**: Esta clave es irrecuperable. Si la pierdes, los tokens almacenados no podran descifrarse y los usuarios tendran que reconectar sus cuentas de Gmail.

---

## Paso 9: Configurar las variables de entorno

Agrega estas variables al archivo `.env` del backend (`cashbot-api/.env`):

```bash
# --- Google Gmail Integration ---
GOOGLE_CLIENT_ID=xxxxx.apps.googleusercontent.com         # Del paso 4
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxx                         # Del paso 4
GOOGLE_REDIRECT_URI=http://localhost:8000/api/gmail/oauth/callback/
GOOGLE_PUBSUB_TOPIC=projects/tu-project-id/topics/gmail-notifications  # Del paso 5
GOOGLE_CLOUD_PROJECT_ID=tu-project-id                      # Del paso 1
GMAIL_TOKEN_ENCRYPTION_KEY=tu-clave-fernet-generada        # Del paso 8
FRONTEND_URL=http://localhost:5173
```

### Referencia de variables

| Variable | Origen | Descripcion |
|----------|--------|-------------|
| `GOOGLE_CLIENT_ID` | Paso 4 | Client ID de las credenciales OAuth |
| `GOOGLE_CLIENT_SECRET` | Paso 4 | Client Secret de las credenciales OAuth |
| `GOOGLE_REDIRECT_URI` | Paso 4 | URI de redireccion autorizada en Google Cloud |
| `GOOGLE_PUBSUB_TOPIC` | Paso 5 | Recurso completo del topic Pub/Sub |
| `GOOGLE_CLOUD_PROJECT_ID` | Paso 1 | ID del proyecto en Google Cloud Console |
| `GMAIL_TOKEN_ENCRYPTION_KEY` | Paso 8 | Clave Fernet para cifrar/descifrar tokens OAuth |
| `FRONTEND_URL` | - | URL del frontend (para redireccion despues del OAuth) |

### Variables en Docker Compose

Si usas Docker, las variables ya estan configuradas en `docker-compose.dev.yml` para leerlas desde tu `.env` o del entorno del sistema:

```yaml
environment:
  - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
  - GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-}
  - GOOGLE_REDIRECT_URI=${GOOGLE_REDIRECT_URI:-http://localhost:8000/api/gmail/oauth/callback/}
  - GOOGLE_PUBSUB_TOPIC=${GOOGLE_PUBSUB_TOPIC:-}
  - GOOGLE_CLOUD_PROJECT_ID=${GOOGLE_CLOUD_PROJECT_ID:-}
  - GMAIL_TOKEN_ENCRYPTION_KEY=${GMAIL_TOKEN_ENCRYPTION_KEY:-}
  - FRONTEND_URL=${FRONTEND_URL:-http://localhost:5173}
```

---

## Paso 10: Levantar y probar

```bash
# Levantar el backend (rebuild para instalar nuevas dependencias)
docker-compose -f docker-compose.dev.yml up --build

# En otra terminal, correr migraciones
docker-compose -f docker-compose.dev.yml exec web python manage.py migrate

# Levantar el frontend
cd ../chat-finance-bot
npm run dev
```

### Verificar la conexion

1. Ir a `http://localhost:5173/dashboard/profile`
2. Click en la tab **"Conexiones"**
3. Click en **"Conectar Gmail"**
4. Autorizar en Google (usa un email que este en "Usuarios de prueba" del paso 3)
5. Debe redirigir de vuelta al perfil con un toast de "Gmail conectado exitosamente"
6. La card de Gmail debe mostrar el email conectado y el estado del watch

### Probar la deteccion de compras

1. Enviate un correo simulando una notificacion bancaria:
   - **Asunto**: `Compra aprobada - Almacenes Exito`
   - **Cuerpo**: `Se ha realizado una compra por $150,000 COP en Almacenes Exito el 12/03/2026. Tarjeta terminada en *1234`
2. Verifica en los logs del backend que el webhook recibio la notificacion
3. Verifica que se creo un gasto como "Sin Categorizar"
4. Verifica que llego un mensaje de WhatsApp preguntando la categoria
5. Responde con la categoria (ej: "mercado") y verifica que el gasto se actualizo

---

## Troubleshooting

### "Scope has changed" al conectar Gmail

Google devuelve scopes adicionales (openid, userinfo.email, etc.) ademas de `gmail.readonly`. Esto es normal y ya esta manejado en el codigo (se usa intercambio directo de tokens en vez de la libreria Flow).

### El webhook no recibe notificaciones

- Verifica que ngrok este corriendo y la URL sea correcta en la suscripcion push
- Verifica que el topic tenga permisos para `gmail-api-push@system.gserviceaccount.com`
- Revisa los logs de Pub/Sub en Google Cloud Console para ver si hay errores de entrega

### "relation gmailbot_googleaccount does not exist"

Falta correr las migraciones:

```bash
docker-compose -f docker-compose.dev.yml exec web python manage.py migrate
```

### OAuth redirige con doble slash (//)

Verifica que `FRONTEND_URL` no tenga slash al final. Correcto: `http://localhost:5173`. Incorrecto: `http://localhost:5173/`.

### El usuario no puede conectar su Gmail (modo Testing)

En Google Cloud Console > Pantalla de consentimiento OAuth > Usuarios de prueba, agrega el email de Gmail del usuario. Mientras el proyecto este en modo "Testing", solo los emails registrados ahi pueden autorizar la app.

---

## Produccion

Para produccion, cambia estas variables:

```bash
GOOGLE_REDIRECT_URI=https://api.tresqu.com/api/gmail/oauth/callback/
FRONTEND_URL=https://tresqu.com
```

Y asegurate de:

1. Publicar la app en Google Cloud (salir del modo "Testing") para que cualquier usuario pueda conectarse
2. Usar una URL fija (no ngrok) para la suscripcion push de Pub/Sub
3. Configurar un cron job para renovar los watches cada 6 dias:
   ```bash
   # Ejecutar diariamente
   python manage.py renew_gmail_watches
   ```
