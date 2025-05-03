## Instalación y configuración local

### Usando Docker (recomendado)

1. Clona el repositorio:

   ```
   git clone git@github.com:Jjat00/cashbot-api.git
   cd cashbot-api
   ```

2. Crea un archivo `.env` en la raíz del proyecto con:

   ```
   DATABASE_URL=postgresql://cashbot:cashbot@localhost:5432/cashbot
   DEBUG=True
   ```

3. Inicia los servicios con Docker Compose:

   ```
   docker-compose -f docker-compose.dev.yml up -d
   ```

   Esto iniciará tanto la base de datos PostgreSQL como la aplicación Django.

4. La aplicación estará disponible en [http://localhost:8000](http://localhost:8000)

5. Para crear un superusuario:

   ```
   docker-compose -f docker-compose.dev.yml exec web python manage.py createsuperuser
   ```

6. Para ver los logs:
   ```
   docker-compose -f docker-compose.dev.yml logs -f
   ```

### Ejecutando solo la base de datos

Si deseas iniciar únicamente la base de datos PostgreSQL sin la API, puedes hacerlo con:

```
docker-compose -f docker-compose.dev.yml up -d db
```

# Sistema de Suscripciones CashBot

## Planes de Suscripción

El sistema incluye tres planes de suscripción:

1. **Plan Básico** (Gratis)

   - Registro de ingresos y gastos
   - Estadísticas básicas
   - Interacción por texto

2. **Plan Premium** ($5/mes o $50/año)

   - Todo lo del Plan Básico
   - Registro de deudas y ahorros
   - Estadísticas detalladas
   - Sin límite de registros
   - Planificación de deudas
   - Seguimiento de metas de ahorro
   - Exportación de datos
   - Interacción por voz

3. **Plan Empresas** ($200/usuario al año)
   - Todo lo del Plan Premium
   - Acceso multiusuario
   - Informes personalizados
   - Control centralizado

## Funcionamiento del Sistema

### Usuarios Nuevos

- Cada usuario nuevo recibe automáticamente **un mes de prueba gratuita del Plan Premium**
- Esto se implementa a través de señales Django en `users/signals.py`

### Expiración del Periodo de Prueba

- Al finalizar el mes de prueba, los usuarios son cambiados automáticamente al Plan Básico
- Esto ocurre mediante un comando de gestión: `python manage.py check_expired_trials`
- Se recomienda ejecutar este comando diariamente mediante un CRON job

### Actualización Manual

Para ejecutar la verificación de suscripciones expiradas manualmente:

```bash
python manage.py check_expired_trials
```

### Carga de Planes

Los planes están precargados en el sistema mediante fixtures. Si necesita recargarlos:

```bash
python manage.py loaddata users/fixtures/initial_subscription_plans.json
```

## Gestión del Plan Empresas

El sistema incluye soporte completo para organizaciones con el plan Empresas:

### Creación de Organizaciones

Para crear una organización:

1. Un usuario inicia el proceso de creación
2. Se establece como administrador de la organización
3. Se configura el número máximo de miembros (licencias)
4. La organización obtiene una suscripción anual

### Gestión de Miembros

Los administradores de la organización pueden:

- Invitar nuevos miembros por email
- Añadir miembros directamente
- Remover miembros existentes
- Gestionar roles (Administrador/Miembro)

### Sistema de Invitaciones

- Las invitaciones tienen un token único de acceso
- Expiran después de 7 días
- Pueden ser aceptadas, rechazadas o canceladas
- Permiten mensajes personalizados

### Límite de Miembros

- Cada organización tiene un límite de miembros según las licencias adquiridas
- Los administradores no pueden añadir miembros si se ha alcanzado el límite
- Se puede incrementar el límite adquiriendo más licencias

### Endpoints API para Organizaciones

```
/api/organizations/                      # Listar/Crear organizaciones
/api/organizations/{id}/                 # Ver detalles de organización
/api/organizations/{id}/members/         # Listar miembros
/api/organizations/{id}/add_member/      # Añadir miembro directamente
/api/organizations/{id}/remove_member/   # Remover miembro
/api/organizations/{id}/invite_member/   # Invitar miembro por email
/api/organization-invitations/           # Gestionar invitaciones
```
