# Sistema de Categorías de Ahorro por Usuario

## Descripción

El módulo de savings ha sido actualizado para que cada usuario tenga sus propias categorías de ahorro personalizadas, en lugar de categorías globales compartidas. Esto permite que cada usuario:

- Tenga sus propias categorías predefinidas al registrarse
- Pueda crear categorías personalizadas adicionales
- No vea categorías de otros usuarios
- Mantenga la privacidad y personalización de sus metas de ahorro

## Cambios Implementados

### 1. Modelo SavingsCategory Actualizado

```python
class SavingsCategory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)  # NUEVO
    name = models.CharField(max_length=100)  # Removido unique=True
    is_default = models.BooleanField(default=False)  # NUEVO
    # ... otros campos existentes

    class Meta:
        unique_together = ['user', 'name']  # NUEVO: Un usuario no puede tener categorías duplicadas
```

### 2. Vistas Actualizadas

Las vistas ahora filtran automáticamente por usuario:

```python
class SavingsCategoryViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return SavingsCategory.objects.filter(user=self.request.user, is_active=True)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
```

### 3. Comandos de Gestión

#### Crear categorías para un usuario específico:

```bash
python manage.py create_user_savings_categories --username [username]
python manage.py create_user_savings_categories --user-id [user_id]
python manage.py create_user_savings_categories --all-users
```

#### Crear plantillas para un usuario específico:

```bash
python manage.py create_user_savings_templates --username [username]
python manage.py create_user_savings_templates --user-id [user_id]
python manage.py create_user_savings_templates --all-users
```

### 4. Categorías Predefinidas

Cada usuario nuevo recibe automáticamente estas 10 categorías:

1. **Fondo de Emergencia** (#FF6B6B) - Reserva para emergencias
2. **Vacaciones** (#4ECDC4) - Ahorro para viajes
3. **Compra de Casa** (#45B7D1) - Ahorro para vivienda
4. **Compra de Vehículo** (#96CEB4) - Ahorro para automóvil
5. **Educación** (#FFEAA7) - Ahorro para estudios
6. **Jubilación** (#DDA0DD) - Ahorro para retiro
7. **Inversiones** (#74B9FF) - Capital para inversiones
8. **Salud** (#55A3FF) - Gastos médicos
9. **Tecnología** (#636E72) - Equipos tecnológicos
10. **Eventos Especiales** (#FD79A8) - Bodas, celebraciones

### 5. Plantillas Predefinidas

Cada usuario recibe 10 plantillas expertas correspondientes a sus categorías:

- **Fondo de Emergencia Básico** - 3-10M COP en 12 meses
- **Vacaciones Familiares** - 2-8M COP en 12 meses
- **Cuota Inicial de Casa** - 30-100M COP en 60 meses
- **Vehículo Usado** - 15-40M COP en 24 meses
- **Curso de Especialización** - 3-15M COP en 18 meses
- **Fondo de Pensiones Voluntarias** - 5-50M COP en 120 meses
- **Capital de Inversión** - 2-20M COP en 24 meses
- **Seguro Médico Privado** - 1-5M COP en 12 meses
- **Equipo de Trabajo** - 3-12M COP en 18 meses
- **Boda** - 10-50M COP en 24 meses

### 6. Señales Automáticas (WIP)

Se implementaron señales para crear automáticamente categorías y plantillas cuando se registra un nuevo usuario:

```python
@receiver(post_save, sender=User)
def create_user_savings_setup(sender, instance, created, **kwargs):
    if created:
        create_default_categories_for_user(instance)
        create_default_templates_for_user(instance)
```

## API Endpoints

### Categorías

- `GET /api/savings/categories/` - Lista categorías del usuario autenticado
- `POST /api/savings/categories/` - Crea nueva categoría para el usuario
- `GET /api/savings/categories/with_goals_count/` - Categorías con conteo de metas

### Plantillas

- `GET /api/savings/templates/` - Lista plantillas basadas en categorías del usuario
- `GET /api/savings/templates/by_category/` - Plantillas agrupadas por categoría del usuario

## Migración de Datos Existentes

Para sistemas existentes con datos globales:

1. **Hacer backup de la base de datos**
2. **Ejecutar migración de categorías a usuarios específicos:**
   ```bash
   python manage.py create_user_savings_categories --all-users
   ```
3. **Crear plantillas para usuarios existentes:**
   ```bash
   python manage.py create_user_savings_templates --all-users
   ```

## Administración

El panel de administración Django ha sido actualizado para mostrar:

- Usuario propietario de cada categoría
- Filtros por usuario
- Indicador de categorías predefinidas vs personalizadas
- Búsqueda por username del usuario

## Funcionalidades para Usuarios

### Crear Categorías Personalizadas

Los usuarios pueden crear sus propias categorías a través del API:

```json
POST /api/savings/categories/
{
    "name": "Mi Categoría Personal",
    "description": "Descripción personalizada",
    "color": "#FF5733",
    "icon": "custom_icon"
}
```

### Privacidad

- Cada usuario solo ve sus propias categorías
- Las metas de ahorro solo pueden asociarse a categorías del mismo usuario
- No hay interferencia entre usuarios

## Ventajas del Sistema

1. **Personalización completa** - Cada usuario tiene control total sobre sus categorías
2. **Privacidad** - No se comparten datos entre usuarios
3. **Escalabilidad** - Permite crecimiento ilimitado de usuarios
4. **Flexibilidad** - Usuarios pueden adaptar el sistema a sus necesidades
5. **Facilidad de uso** - Configuración automática para nuevos usuarios

## Próximos Pasos

1. Completar la implementación de señales automáticas
2. Agregar endpoint para duplicar categorías entre usuarios (opcional)
3. Implementar importación/exportación de configuraciones de categorías
4. Agregar métricas de uso por categoría por usuario
