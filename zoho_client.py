import os
import time
import threading
from typing import Iterator

import requests


ZOHO_API_DOMAIN = os.getenv('ZOHO_API_DOMAIN', 'https://www.zohoapis.sa')
ZOHO_ACCOUNTS_DOMAIN = os.getenv('ZOHO_ACCOUNTS_DOMAIN', 'https://accounts.zoho.sa')

_token_lock = threading.Lock()
_token_cache = {'access_token': None, 'expires_at': 0.0}


class ZohoAuthError(RuntimeError):
    pass


class ZohoAPIError(RuntimeError):
    pass


def _get_access_token() -> str:
    with _token_lock:
        if _token_cache['access_token'] and _token_cache['expires_at'] > time.time() + 60:
            return _token_cache['access_token']

        client_id = os.getenv('ZOHO_CLIENT_ID')
        client_secret = os.getenv('ZOHO_CLIENT_SECRET')
        refresh_token = os.getenv('ZOHO_REFRESH_TOKEN')
        if not all([client_id, client_secret, refresh_token]):
            raise ZohoAuthError(
                'ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, and ZOHO_REFRESH_TOKEN must be set'
            )

        resp = requests.post(
            f'{ZOHO_ACCOUNTS_DOMAIN}/oauth/v2/token',
            data={
                'refresh_token': refresh_token,
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'refresh_token',
            },
            timeout=30,
        )
        if not resp.ok:
            raise ZohoAuthError(f'Token refresh failed: {resp.status_code} {resp.text}')
        data = resp.json()
        access_token = data.get('access_token')
        if not access_token:
            raise ZohoAuthError(f'No access_token in token response: {data}')
        _token_cache['access_token'] = access_token
        _token_cache['expires_at'] = time.time() + int(data.get('expires_in', 3600))
        return access_token


def _coql(select_query: str) -> dict:
    token = _get_access_token()
    resp = requests.post(
        f'{ZOHO_API_DOMAIN}/crm/v8/coql',
        headers={'Authorization': f'Zoho-oauthtoken {token}'},
        json={'select_query': select_query},
        timeout=60,
    )
    if resp.status_code == 204:
        return {'data': [], 'info': {'more_records': False}}
    if resp.status_code == 401:
        # Token may have been revoked mid-flight — force refresh and retry once
        _token_cache['access_token'] = None
        token = _get_access_token()
        resp = requests.post(
            f'{ZOHO_API_DOMAIN}/crm/v8/coql',
            headers={'Authorization': f'Zoho-oauthtoken {token}'},
            json={'select_query': select_query},
            timeout=60,
        )
        if resp.status_code == 204:
            return {'data': [], 'info': {'more_records': False}}
    if not resp.ok:
        raise ZohoAPIError(f'COQL failed: {resp.status_code} {resp.text}\nQuery: {select_query}')
    return resp.json()


def coql_paginated(base_query: str, page_size: int = 200) -> Iterator[dict]:
    """Run a COQL query, paging through all results. `base_query` must NOT include LIMIT."""
    offset = 0
    while True:
        body = _coql(f'{base_query} LIMIT {offset},{page_size}')
        records = body.get('data') or []
        for r in records:
            yield r
        if not records or not body.get('info', {}).get('more_records'):
            break
        offset += page_size
