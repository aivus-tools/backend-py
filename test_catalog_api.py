#!/usr/bin/env python
"""Test catalog API endpoints."""

import hashlib
import hmac
import json
import os
import time

import requests

# Configuration
API_URL = "http://localhost:8000"
HMAC_SECRET = os.getenv("HMAC_SECRET", "your-secret-key-here")

# Test user (admin@aivus.com from seed data)
USER_ID = "16811946-8818-4ed4-b4b0-4cfe6eaf8c8f"
USER_GROUP = "VENDOR"


def create_hmac_signature(method: str, path: str, timestamp: str, user_id: str, user_group: str) -> str:
    """Create HMAC signature."""
    message = f"{method}:{path}:{timestamp}:{user_id}:{user_group}"
    signature = hmac.new(
        HMAC_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return signature


def make_authenticated_request(method: str, path: str, query_params: str = ""):
    """Make authenticated request to API."""
    # Include query params in path for HMAC signature
    path_with_query = path
    if query_params:
        path_with_query = f"{path}?{query_params}"

    timestamp = str(int(time.time()))
    signature = create_hmac_signature(method, path_with_query, timestamp, USER_ID, USER_GROUP)

    headers = {
        "x-timestamp": timestamp,
        "x-user-id": USER_ID,
        "x-user-group": USER_GROUP,
        "x-signature": signature,
    }

    url = f"{API_URL}{path_with_query}"
    response = requests.request(method, url, headers=headers)
    return response


def test_get_categories():
    """Test GET /api/v1/categories."""
    print("\n" + "=" * 60)
    print("Testing GET /api/v1/categories")
    print("=" * 60)

    response = make_authenticated_request("GET", "/api/v1/categories")

    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)[:500]}...")


def test_get_entries():
    """Test GET /api/v1/entries."""
    print("\n" + "=" * 60)
    print("Testing GET /api/v1/entries")
    print("=" * 60)

    response = make_authenticated_request("GET", "/api/v1/entries")

    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Total entries: {len(data.get('entries', []))}")
    if data.get("entries"):
        print(f"First entry: {json.dumps(data['entries'][0], indent=2, ensure_ascii=False)}")


def test_get_entries_full():
    """Test GET /api/v1/entries?full=true."""
    print("\n" + "=" * 60)
    print("Testing GET /api/v1/entries?full=true")
    print("=" * 60)

    response = make_authenticated_request("GET", "/api/v1/entries", query_params="full=true")

    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Total entries: {len(data.get('entries', []))}")
    if data.get("entries"):
        print(f"First entry with units: {json.dumps(data['entries'][0], indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    print("🚀 Testing Catalog API")
    print(f"API URL: {API_URL}")
    print(f"User ID: {USER_ID}")
    print(f"User Group: {USER_GROUP}")

    try:
        test_get_categories()
        test_get_entries()
        test_get_entries_full()
        print("\n✅ All tests completed!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()

