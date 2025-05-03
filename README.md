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
