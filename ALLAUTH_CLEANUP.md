# Как удалить Allauth (если не нужен)

## Если вам нужен только REST API без веб-форм:

### 1. Удалить из INSTALLED_APPS

В `config/settings/base.py`:

```python
THIRD_PARTY_APPS = [
    "crispy_forms",
    "crispy_bootstrap5",
    # "allauth",              # ❌ Удалить
    # "allauth.account",      # ❌ Удалить
    # "allauth.mfa",          # ❌ Удалить  
    # "allauth.socialaccount", # ❌ Удалить
    "django_celery_beat",
]
```

### 2. Удалить из AUTHENTICATION_BACKENDS

```python
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    # "allauth.account.auth_backends.AuthenticationBackend",  # ❌ Удалить
]
```

### 3. Удалить URL patterns

В `config/urls.py` удалить:
```python
# path("accounts/", include("allauth.urls")),  # ❌ Удалить
```

### 4. Откатить миграции

```bash
docker compose -f docker-compose.local.yml exec django python manage.py migrate allauth zero
docker compose -f docker-compose.local.yml exec django python manage.py migrate socialaccount zero
docker compose -f docker-compose.local.yml exec django python manage.py migrate account zero
docker compose -f docker-compose.local.yml exec django python manage.py migrate mfa zero
```

### 5. Удалить пакет

В `pyproject.toml` удалить:
```toml
# django-allauth = "*"  # ❌ Удалить
```

Пересобрать:
```bash
docker compose -f docker-compose.local.yml build django
docker compose -f docker-compose.local.yml up -d django
```

---

## Альтернатива: Оставить только account (без social)

Если нужна email аутентификация, но не нужны соцсети:

```python
THIRD_PARTY_APPS = [
    "allauth",              # ✅ Оставить
    "allauth.account",      # ✅ Оставить (email подтверждение)
    # "allauth.mfa",        # ❌ Удалить если не нужна 2FA
    # "allauth.socialaccount", # ❌ Удалить соцсети
]
```

Тогда останется только базовая аутентификация через email.

