# 🚀 Quick Start - Aivus Backend

## ✅ Project is up and running!

### 🌐 Open in browser:

#### Django Admin (main admin panel)
```
http://localhost:8000/admin/

Email:    admin@aivus.com
Password: admin123
```

#### Mailpit (email viewer)
```
http://localhost:8025

All sent emails will appear here
```

#### Flower (Celery task monitoring)
```
http://localhost:5555

User:     admin
Password: admin123
```

---

## 🐳 Docker Management

### Basic commands:

```bash
# Navigate to project directory
cd /Users/ipolotsky/Develop/Aivus/Backend/aivus_backend

# Check status
docker compose -f docker-compose.local.yml ps

# View logs
docker compose -f docker-compose.local.yml logs -f django

# Restart
docker compose -f docker-compose.local.yml restart

# Stop
docker compose -f docker-compose.local.yml down

# Start again
docker compose -f docker-compose.local.yml up -d
```

---

## 🛠️ Django Commands

All Django commands are executed like this:
```bash
docker compose -f docker-compose.local.yml exec django python manage.py <command>
```

### Examples:

```bash
# Django shell
docker compose -f docker-compose.local.yml exec django python manage.py shell

# Create migrations
docker compose -f docker-compose.local.yml exec django python manage.py makemigrations

# Apply migrations
docker compose -f docker-compose.local.yml exec django python manage.py migrate

# Create superuser
docker compose -f docker-compose.local.yml exec django python manage.py createsuperuser

# Run tests
docker compose -f docker-compose.local.yml exec django pytest
```

---

## 📝 What's Already Working

✅ **PostgreSQL** with UUID and soft delete
✅ **Django Admin** ready to use
✅ **Celery** for background tasks
✅ **Redis** for cache and queues
✅ **Mailpit** for email testing
✅ **Base models** (JournalizeModel)
✅ **Hot-reload** on code changes

---

## 🎯 Next Steps

1. **Open admin panel**: http://localhost:8000/admin/
2. **Create models** for remaining entities
3. **Set up DRF** for API
4. **Migrate logic** from NestJS

---

## 📚 Documentation

- **`PROJECT_STATUS.md`** - Current project status
- **`aivus_backend/core/README.md`** - Base models documentation

---

## 🆘 If Something Doesn't Work

### Restart everything:
```bash
cd /Users/ipolotsky/Develop/Aivus/Backend/aivus_backend
docker compose -f docker-compose.local.yml restart
```

### View error logs:
```bash
docker compose -f docker-compose.local.yml logs django
```

### Complete reload:
```bash
docker compose -f docker-compose.local.yml down
docker compose -f docker-compose.local.yml up -d
```

---

## 💡 Useful Information

### UUID is used everywhere:
```python
# All IDs are now UUIDs, not integers
user.id  # 16811946-8818-4ed4-b4b0-4cfe6eaf8c8f
```

### Soft Delete is active:
```python
# Deletion doesn't delete, it marks
user.delete()   # Sets deleted_at
user.restore()  # Restores
```

### Manager filters deleted records:
```python
User.objects.all()              # Only active
User.objects.all_with_deleted() # All, including deleted
User.objects.deleted_only()     # Only deleted
```

---

**Project is ready! Happy coding! 🚀**
