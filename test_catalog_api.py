#!/usr/bin/env python
"""Test catalog API endpoints."""

import hashlib
import hmac
import os
import time

import requests

# Configuration
API_URL = "http://localhost:8000"
HMAC_SECRET = os.getenv("HMAC_SECRET", "your-secret-key-here")

# Test user (admin@aivus.com from seed data)
USER_ID = "16811946-8818-4ed4-b4b0-4cfe6eaf8c8f"
USER_GROUP = "VENDOR"


def create_hmac_signature(
    method: str, path: str, timestamp: str, user_id: str, user_group: str
) -> str:
    """Create HMAC signature."""
    message = f"{method}:{path}:{timestamp}:{user_id}:{user_group}"
    return hmac.new(
        HMAC_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def make_authenticated_request(method: str, path: str, query_params: str = ""):
    """Make authenticated request to API."""
    # Include query params in path for HMAC signature
    path_with_query = path
    if query_params:
        path_with_query = f"{path}?{query_params}"

    timestamp = str(int(time.time()))
    signature = create_hmac_signature(
        method, path_with_query, timestamp, USER_ID, USER_GROUP
    )

    headers = {
        "x-timestamp": timestamp,
        "x-user-id": USER_ID,
        "x-user-group": USER_GROUP,
        "x-signature": signature,
    }

    url = f"{API_URL}{path_with_query}"
    return requests.request(method, url, headers=headers)


def test_get_categories():
    """Test GET /api/v1/categories."""

    make_authenticated_request("GET", "/api/v1/categories")



def test_get_entries():
    """Test GET /api/v1/entries."""

    response = make_authenticated_request("GET", "/api/v1/entries")

    data = response.json()
    if data.get("entries"):
        pass


def test_get_entries_full():
    """Test GET /api/v1/entries?full=true."""

    response = make_authenticated_request(
        "GET", "/api/v1/entries", query_params="full=true"
    )

    data = response.json()
    if data.get("entries"):
        pass


if __name__ == "__main__":

    try:
        test_get_categories()
        test_get_entries()
        test_get_entries_full()
    except Exception:
        import traceback

        traceback.print_exc()
