# Core Application

Базовые модели и менеджеры для проекта Aivus Backend.

## JournalizeModel

Абстрактная базовая модель с UUID первичным ключом и функционалом soft delete.

### Особенности:

- **UUID как primary key** вместо числовых ID для безопасности
- **Soft delete** - удаление записей через установку `deleted_at` вместо физического удаления
- **Автоматические timestamps** - `created_at`, `updated_at`
- **Менеджер с фильтрацией** - по умолчанию исключает удаленные записи

### Поля:

- `id` - UUID (primary key)
- `created_at` - дата создания
- `updated_at` - дата обновления
- `deleted_at` - дата удаления (null если активна)

### Методы:

- `delete()` - soft delete (устанавливает `deleted_at`)
- `hard_delete()` - физическое удаление из БД
- `restore()` - восстановление удаленной записи
- `is_deleted` - property, проверяет удалена ли запись

### Использование:

```python
from aivus_backend.core.models import JournalizeModel

class MyModel(JournalizeModel):
    name = models.CharField(max_length=255)
    # ... другие поля
```

### Работа с менеджером:

```python
# Получить только активные записи (по умолчанию)
MyModel.objects.all()

# Получить все записи включая удаленные
MyModel.objects.all_with_deleted()

# Получить только удаленные записи
MyModel.objects.deleted_only()

# Soft delete
instance.delete()  # Устанавливает deleted_at

# Проверка удалена ли запись
if instance.is_deleted:
    print("Запись удалена")

# Восстановление
instance.restore()  # Сбрасывает deleted_at

# Физическое удаление (необратимо!)
instance.hard_delete()
```

### Пример с User:

Модель `User` использует этот функционал:

```python
from aivus_backend.users.models import User

# Создание
user = User.objects.create_user(email='test@example.com', password='pass123')
print(user.id)  # UUID: 48298639-3816-49af-bfcb-e9d7035aa842

# Soft delete
user.delete()

# Пользователь больше не появится в обычных запросах
User.objects.filter(email='test@example.com').exists()  # False

# Но его можно получить через all_with_deleted
User.objects.all_with_deleted().filter(email='test@example.com').exists()  # True

# Восстановить
user.restore()
User.objects.filter(email='test@example.com').exists()  # True
```

## JournalizeManager

Кастомный менеджер для работы с soft delete.

### Методы QuerySet:

- `delete()` - soft delete для всех записей в QuerySet
- `hard_delete()` - физическое удаление всех записей
- `alive()` - фильтр только активных записей
- `deleted()` - фильтр только удаленных записей

### Пример:

```python
# Soft delete всех пользователей с определенным условием
User.objects.filter(email__endswith='@spam.com').delete()

# Физическое удаление (будьте осторожны!)
User.objects.filter(email__endswith='@spam.com').hard_delete()
```

## Расширение для других моделей

Для добавления функционала в другие модели:

### Вариант 1: Наследование от JournalizeModel

```python
from aivus_backend.core.models import JournalizeModel

class Category(JournalizeModel):
    name = models.CharField(max_length=255)
    description = models.TextField()
```

### Вариант 2: Использование JournalizeManager с кастомным менеджером

```python
from django.contrib.auth.models import UserManager as DjangoUserManager
from aivus_backend.core.managers import JournalizeQuerySet

class CustomManager(DjangoUserManager):
    def get_queryset(self):
        return JournalizeQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)

    def all_with_deleted(self):
        return JournalizeQuerySet(self.model, using=self._db)

    def deleted_only(self):
        return JournalizeQuerySet(self.model, using=self._db).filter(deleted_at__isnull=False)
```

## Важные замечания

1. **Soft delete не каскадируется** - при удалении родительской записи дочерние не удаляются автоматически
2. **Unique constraints** - удаленные записи все еще участвуют в проверках уникальности
3. **Foreign keys** - будьте осторожны с `on_delete=CASCADE` - он может не сработать как ожидается
4. **Производительность** - deleted_at должен быть проиндексирован для больших таблиц

## Миграция существующих моделей

При миграции существующей модели на JournalizeModel:

1. Добавьте поля в модель
2. Создайте миграцию: `python manage.py makemigrations`
3. **Важно**: Существующие записи получат UUID при миграции
4. Обновите все Foreign Keys на эту модель

```python
# Было
class Order(models.Model):
    id = models.AutoField(primary_key=True)  # По умолчанию

# Стало
class Order(JournalizeModel):
    # id теперь UUID автоматически
    pass
```
