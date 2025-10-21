# 🐳 Docker Setup - Aivus Backend

## ✅ Запущенные сервисы

### Основные сервисы:
```
✅ Django (Backend API)      → http://localhost:8000
✅ PostgreSQL (Database)     → postgres://postgres:postgres@localhost:5432/aivus_backend
✅ Redis (Cache & Queue)     → redis://localhost:6379/0
✅ Mailpit (Email Testing)   → http://localhost:8025
✅ Flower (Celery Monitor)   → http://localhost:5555
✅ Celery Worker             → Фоновые задачи
✅ Celery Beat               → Периодические задачи
```

## 🔐 Учетные данные

### Суперпользователь Django:
```
Email:    admin@aivus.com
Password: admin123
ID:       16811946-8818-4ed4-b4b0-4cfe6eaf8c8f (UUID)
```

### PostgreSQL:
```
Host:     localhost (или postgres внутри Docker)
Port:     5432
Database: aivus_backend
User:     postgres
Password: postgres
```

### Flower (Celery Monitor):
```
URL:      http://localhost:5555
User:     admin
Password: admin123
```

## 📦 Структура контейнеров

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Services                       │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   Django     │  │  Postgres    │  │    Redis     │ │
│  │   :8000      │  │   :5432      │  │    :6379     │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘ │
│         │                  │                  │         │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌──────▼───────┐ │
│  │ Celery Worker│  │   Mailpit    │  │    Flower    │ │
│  │              │  │   :8025      │  │    :5555     │ │
│  └──────────────┘  └──────────────┘  └──────────────┘ │
│         │                                               │
│  ┌──────▼───────┐                                      │
│  │ Celery Beat  │                                      │
│  │              │                                      │
│  └──────────────┘                                      │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## 🚀 Команды управления

### Основные команды:

```bash
# Перейти в директорию проекта
cd /Users/ipolotsky/Develop/Aivus/Backend/aivus_backend

# Запустить все сервисы
docker compose -f docker-compose.local.yml up -d

# Остановить все сервисы
docker compose -f docker-compose.local.yml down

# Перезапустить все сервисы
docker compose -f docker-compose.local.yml restart

# Просмотр статуса контейнеров
docker compose -f docker-compose.local.yml ps

# Просмотр логов
docker compose -f docker-compose.local.yml logs -f

# Просмотр логов конкретного сервиса
docker compose -f docker-compose.local.yml logs -f django
docker compose -f docker-compose.local.yml logs -f postgres
docker compose -f docker-compose.local.yml logs -f celeryworker
```

### Django команды:

```bash
# Выполнить любую Django команду
docker compose -f docker-compose.local.yml exec django python manage.py <command>

# Применить миграции
docker compose -f docker-compose.local.yml exec django python manage.py migrate

# Создать миграции
docker compose -f docker-compose.local.yml exec django python manage.py makemigrations

# Создать суперпользователя
docker compose -f docker-compose.local.yml exec django python manage.py createsuperuser

# Django shell
docker compose -f docker-compose.local.yml exec django python manage.py shell

# Запустить тесты
docker compose -f docker-compose.local.yml exec django pytest

# Собрать статику
docker compose -f docker-compose.local.yml exec django python manage.py collectstatic --noinput
```

### Работа с базой данных:

```bash
# Подключиться к PostgreSQL
docker compose -f docker-compose.local.yml exec postgres psql -U postgres -d aivus_backend

# Создать дамп базы данных
docker compose -f docker-compose.local.yml exec postgres pg_dump -U postgres aivus_backend > backup.sql

# Восстановить базу данных из дампа
docker compose -f docker-compose.local.yml exec -T postgres psql -U postgres aivus_backend < backup.sql

# Просмотр таблиц
docker compose -f docker-compose.local.yml exec postgres psql -U postgres -d aivus_backend -c "\dt"
```

### Отладка:

```bash
# Войти в контейнер Django (bash)
docker compose -f docker-compose.local.yml exec django bash

# Войти в контейнер PostgreSQL
docker compose -f docker-compose.local.yml exec postgres bash

# Перезапустить только Django (быстрая перезагрузка кода)
docker compose -f docker-compose.local.yml restart django

# Пересобрать образы (после изменения requirements)
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml up -d
```

## 🔧 Переменные окружения

### `.envs/.local/.django`
```bash
USE_DOCKER=yes
IPYTHONDIR=/app/.ipython
REDIS_URL=redis://redis:6379/0
CELERY_FLOWER_USER=admin
CELERY_FLOWER_PASSWORD=admin123
```

### `.envs/.local/.postgres`
```bash
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=aivus_backend
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/aivus_backend
```

## 📊 Проверка работы

### Проверить UUID и Soft Delete:
```bash
docker compose -f docker-compose.local.yml exec django python manage.py shell -c "
from aivus_backend.users.models import User
print(f'Пользователей: {User.objects.count()}')
print(f'ID админа: {User.objects.first().id}')
print(f'Тип ID: {type(User.objects.first().id).__name__}')
"
```

### Проверить подключение к PostgreSQL:
```bash
docker compose -f docker-compose.local.yml exec postgres psql -U postgres -d aivus_backend -c "SELECT version();"
```

### Проверить Redis:
```bash
docker compose -f docker-compose.local.yml exec redis redis-cli ping
```

## 🌐 Доступ к сервисам

### Django Admin:
```
URL: http://localhost:8000/admin/
Email: admin@aivus.com
Password: admin123
```

### Django REST Framework API:
```
URL: http://localhost:8000/api/
Документация: http://localhost:8000/api/docs/ (когда настроите)
```

### Mailpit (Email Testing):
```
URL: http://localhost:8025
- Просмотр всех отправленных email
- Тестирование email шаблонов
```

### Flower (Celery Monitoring):
```
URL: http://localhost:5555
User: admin
Password: admin123
- Мониторинг задач Celery
- Просмотр воркеров
- Статистика выполнения
```

## 🧪 Тестирование

### Запустить все тесты:
```bash
docker compose -f docker-compose.local.yml exec django pytest
```

### Запустить тесты с покрытием:
```bash
docker compose -f docker-compose.local.yml exec django pytest --cov
```

### Запустить конкретный тест:
```bash
docker compose -f docker-compose.local.yml exec django pytest aivus_backend/users/tests/
```

## 🔄 Обновление кода

После изменения кода:
```bash
# Код обновляется автоматически (volumes монтированы)
# Django автоматически перезагружается при изменениях

# Если нужно перезапустить вручную:
docker compose -f docker-compose.local.yml restart django
```

После изменения зависимостей (pyproject.toml):
```bash
docker compose -f docker-compose.local.yml build django
docker compose -f docker-compose.local.yml up -d django
```

После изменения моделей:
```bash
docker compose -f docker-compose.local.yml exec django python manage.py makemigrations
docker compose -f docker-compose.local.yml exec django python manage.py migrate
```

## 🗑️ Очистка

### Удалить контейнеры (данные сохраняются):
```bash
docker compose -f docker-compose.local.yml down
```

### Удалить контейнеры И данные (volumes):
```bash
docker compose -f docker-compose.local.yml down -v
```

### Удалить все (контейнеры, volumes, образы):
```bash
docker compose -f docker-compose.local.yml down -v --rmi all
```

## 🔥 Полный перезапуск

Если что-то пошло не так:
```bash
# 1. Остановить и удалить все
docker compose -f docker-compose.local.yml down -v

# 2. Пересобрать образы
docker compose -f docker-compose.local.yml build --no-cache

# 3. Запустить заново
docker compose -f docker-compose.local.yml up -d

# 4. Применить миграции
docker compose -f docker-compose.local.yml exec django python manage.py migrate

# 5. Создать суперпользователя
docker compose -f docker-compose.local.yml exec django python manage.py createsuperuser
```

## 🎯 Следующие шаги

1. **Открыть админку**: http://localhost:8000/admin/
2. **Создать модели** для остальных сущностей (Client, Vendor, Brief, Offer, Entry, Rate, Category)
3. **Настроить DRF** для API endpoints
4. **Добавить HMAC аутентификацию**
5. **Установить Unfold Admin** для красивой админки
6. **Мигрировать бизнес-логику** из NestJS

## 📝 Полезные ссылки

- Django Admin: http://localhost:8000/admin/
- Mailpit: http://localhost:8025
- Flower: http://localhost:5555
- Django Docs: https://docs.djangoproject.com/
- DRF Docs: https://www.django-rest-framework.org/
- Cookiecutter Django Docs: https://cookiecutter-django.readthedocs.io/

---

## ✅ Что уже работает:

- ✅ PostgreSQL с UUID primary keys
- ✅ Soft delete функционал
- ✅ Django Admin с суперпользователем
- ✅ Celery для фоновых задач
- ✅ Redis для кэширования и очередей
- ✅ Mailpit для тестирования email
- ✅ Flower для мониторинга Celery
- ✅ Auto-reload при изменении кода
- ✅ Миграции применены
- ✅ Тесты работают

**Проект полностью готов к разработке!** 🚀

