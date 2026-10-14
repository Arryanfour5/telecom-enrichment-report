"""Runtime settings. Connections come from env; schema comes from the mapping file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mapping_file: Path = Path("config/report_mapping.py")
    # The IPDR report (GET /api/v1/ipdr/report/...): its own mapping file, same contract as the CDR one.
    ipdr_mapping_file: Path = Path("config/ipdr_report_mapping.py")

    # `facts` connection - fact_call_detail, tac_db, cell towers, sms senders.
    # Queries in the mapping file use bare table names, so the database is set
    # on the connection: change CH_FACTS_DATABASE / CH_DIMS_DATABASE for a
    # deployment whose tables live under a different database name, instead of
    # editing every query.
    ch_facts_url: str = "http://192.168.1.13:8123"
    ch_facts_user: str = "user"
    ch_facts_password: str = ""
    ch_facts_database: str = "analytics"

    # `dims` connection - the LEA dim_* tables. May be a different server, which
    # is why sections declare a source instead of assuming one database.
    ch_dims_url: str = "http://192.168.1.13:8123"
    ch_dims_database: str = "analytics"
    ch_dims_user: str = "user"
    ch_dims_password: str = ""

    query_timeout_seconds: int = 30
    default_limit: int = 10

    api_title: str = "Telecom Enrichment Report"
    api_root_path: str = ""


settings = Settings()
