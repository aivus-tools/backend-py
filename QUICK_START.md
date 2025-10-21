# 🚀 Быстрый старт - Aivus Backend

## ✅ Проект запущен и работает!

### 🌐 Открыть в браузере:

#### Django Admin (главная админка)
```
http://localhost:8000/admin/

Логин:    admin@aivus.com
Пароль:   admin123
```

#### Mailpit (просмотр email)
```
http://localhost:8025

Здесь будут все отправленные письма
```

#### Flower (мониторинг Celery задач)
```
http://localhost:5555

Логин:    admin
Пароль:   admin123
```

---

## 🐳 Управление Docker

### Основные команды:

```bash
# Перейти в директорию проекта
cd /Users/ipolotsky/Develop/Aivus/Backend/aivus_backend

# Посмотреть статус
docker compose -f docker-compose.local.yml ps

# Посмотреть логи
docker compose -f docker-compose.local.yml logs -f django

# Перезапустить
docker compose -f docker-compose.local.yml restart

# Остановить
docker compose -f docker-compose.local.yml down

# Запустить снова
docker compose -f docker-compose.local.yml up -d
```

---

## 🛠️ Django команды

Все Django команды выполняются так:
```bash
docker compose -f docker-compose.local.yml exec django python manage.py <команда>
```

### Примеры:

```bash
# Django shell
docker compose -f docker-compose.local.yml exec django python manage.py shell

# Создать миграции
docker compose -f docker-compose.local.yml exec django python manage.py makemigrations

# Применить миграции
docker compose -f docker-compose.local.yml exec django python manage.py migrate

# Создать суперпользователя
docker compose -f docker-compose.local.yml exec django python manage.py createsuperuser

# Запустить тесты
docker compose -f docker-compose.local.yml exec django pytest
```

---

## 📝 Что уже работает

✅ **PostgreSQL** с UUID и soft delete  
✅ **Django Admin** готов к использованию  
✅ **Celery** для фоновых задач  
✅ **Redis** для кэша и очередей  
✅ **Mailpit** для тестирования email  
✅ **Базовые модели** (JournalizeModel)  
✅ **Hot-reload** при изменении кода  

---

## 🎯 Следующие шаги

1. **Откройте админку**: http://localhost:8000/admin/
2. **Создайте модели** для остальных сущностей
3. **Настройте DRF** для API
4. **Мигрируйте логику** из NestJS

---

## 📚 Документация

- **`PROJECT_STATUS.md`** - Текущий статус проекта
- **`DOCKER_SETUP.md`** - Подробная документация по Docker
- **`IMPLEMENTATION_SUMMARY.md`** - Техническая документация
- **`aivus_backend/core/README.md`** - Документация по базовым моделям

---

## 🆘 Если что-то не работает

### Перезапустить все:
```bash
cd /Users/ipolotsky/Develop/Aivus/Backend/aivus_backend
docker compose -f docker-compose.local.yml restart
```

### Посмотреть логи ошибок:
```bash
docker compose -f docker-compose.local.yml logs django
```

### Полная перезагрузка:
```bash
docker compose -f docker-compose.local.yml down
docker compose -f docker-compose.local.yml up -d
```

---

## 💡 Полезная информация

### UUID используется везде:
```python
# Все ID теперь UUID, не числа
user.id  # 16811946-8818-4ed4-b4b0-4cfe6eaf8c8f
```

### Soft Delete активирован:
```python
# Удаление не удаляет, а помечает
user.delete()  # Ставит deleted_at
user.restore()  # Восстанавливает
```

### Менеджер фильтрует удаленные:
```python
User.objects.all()              # Только активные
User.objects.all_with_deleted() # Все, включая удаленные
User.objects.deleted_only()     # Только удаленные
```

---

**Проект готов! Приятной разработки! 🚀**

