"""ClickHouse HTTP access, one client per logical source.

Values are never interpolated into SQL. They go over as ClickHouse bind params
(`param_x` on the query string, `{x:Type}` in the SQL), so a phone number that
happens to contain quotes cannot alter the statement.
"""

from typing import Any, Dict, List

import requests

from app.config import settings


class ClickHouseError(RuntimeError):
    pass


class ClickHouseClient:
    def __init__(self, url: str, user: str, password: str, timeout: int, database: str):
        self.url = url.rstrip("/")
        self.auth = (user, password)
        self.timeout = timeout
        self.database = database

    @staticmethod
    def _encode(value: Any) -> str:
        # ClickHouse wants an Array(String) bind as ['a','b'].
        if isinstance(value, (list, tuple)):
            inner = ",".join("'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"
                             for v in value)
            return f"[{inner}]"
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    def query(self, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        query_params = {f"param_{k}": self._encode(v) for k, v in params.items()}
        query_params["default_format"] = "JSON"
        # Sets the database bare table names in the mapping file resolve against.
        query_params["database"] = self.database
        try:
            response = requests.post(
                self.url, params=query_params, data=sql.encode("utf-8"),
                auth=self.auth, timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise ClickHouseError(f"{self.url} unreachable: {e}") from e

        if response.status_code != 200:
            raise ClickHouseError(response.text.strip()[:600])

        body = response.json()
        return body.get("data", [])

    def ping(self) -> bool:
        try:
            self.query("SELECT 1", {})
            return True
        except ClickHouseError:
            return False


_clients: Dict[str, ClickHouseClient] = {}


def get_client(source: str) -> ClickHouseClient:
    """Client for a logical source name used by the mapping file."""
    if source not in _clients:
        if source == "dims":
            cfg = (settings.ch_dims_url, settings.ch_dims_user, settings.ch_dims_password,
                   settings.ch_dims_database)
        else:
            # `facts` and `master` share the facts server by default; point
            # master elsewhere by giving it its own branch here if that changes.
            # A master.* table is written fully qualified in the mapping file,
            # so it resolves regardless of the connection's default database.
            cfg = (settings.ch_facts_url, settings.ch_facts_user, settings.ch_facts_password,
                   settings.ch_facts_database)
        _clients[source] = ClickHouseClient(
            cfg[0], cfg[1], cfg[2], timeout=settings.query_timeout_seconds, database=cfg[3])
    return _clients[source]
