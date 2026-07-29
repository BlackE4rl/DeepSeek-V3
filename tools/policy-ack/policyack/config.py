"""Konfiguration laden (TOML + Umgebungsvariablen)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.toml"


@dataclass
class SmtpConfig:
    dry_run: bool = True
    spool_dir: str = "spool"
    host: str = "localhost"
    port: int = 587
    starttls: bool = True
    username: str = ""
    password: str = ""
    from_address: str = "policy@localhost"
    from_name: str = "Policy"


@dataclass
class Config:
    base_url: str = "http://localhost:8080"
    database: str = "policyack.db"
    pepper: str = ""
    token_ttl_days: int = 30
    max_failed_attempts: int = 5
    lockout_minutes: int = 15
    organisation: str = "Organisation"
    contact: str = ""
    store_ip: bool = True
    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    root: Path = field(default_factory=Path.cwd)

    @property
    def db_path(self) -> Path:
        return self._resolve(self.database)

    @property
    def spool_path(self) -> Path:
        return self._resolve(self.smtp.spool_dir)

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def confirm_url(self, token: str) -> str:
        return f"{self.base_url.rstrip('/')}/c/{token}"


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """Konfiguration lesen. Fehlt die Datei, gelten die Defaults.

    Geheimnisse aus der Umgebung haben Vorrang vor der Datei:
    POLICYACK_PEPPER, POLICYACK_SMTP_PASSWORD, POLICYACK_DB, POLICYACK_BASE_URL.
    """
    cfg_path = Path(path) if path else Path(DEFAULT_CONFIG_NAME)
    data: dict = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as handle:
            data = tomllib.load(handle)

    app = dict(data.get("app", {}))
    smtp = dict(data.get("smtp", {}))

    cfg = Config(
        base_url=app.get("base_url", Config.base_url),
        database=app.get("database", Config.database),
        pepper=app.get("pepper", ""),
        token_ttl_days=int(app.get("token_ttl_days", Config.token_ttl_days)),
        max_failed_attempts=int(app.get("max_failed_attempts", Config.max_failed_attempts)),
        lockout_minutes=int(app.get("lockout_minutes", Config.lockout_minutes)),
        organisation=app.get("organisation", Config.organisation),
        contact=app.get("contact", ""),
        store_ip=bool(app.get("store_ip", True)),
        smtp=SmtpConfig(
            dry_run=bool(smtp.get("dry_run", True)),
            spool_dir=smtp.get("spool_dir", SmtpConfig.spool_dir),
            host=smtp.get("host", SmtpConfig.host),
            port=int(smtp.get("port", SmtpConfig.port)),
            starttls=bool(smtp.get("starttls", True)),
            username=smtp.get("username", ""),
            password=smtp.get("password", ""),
            from_address=smtp.get("from_address", SmtpConfig.from_address),
            from_name=smtp.get("from_name", SmtpConfig.from_name),
        ),
        root=cfg_path.resolve().parent if cfg_path.exists() else Path.cwd(),
    )

    cfg.pepper = os.environ.get("POLICYACK_PEPPER", cfg.pepper)
    cfg.smtp.password = os.environ.get("POLICYACK_SMTP_PASSWORD", cfg.smtp.password)
    cfg.database = os.environ.get("POLICYACK_DB", cfg.database)
    cfg.base_url = os.environ.get("POLICYACK_BASE_URL", cfg.base_url)
    return cfg


def warnings_for(cfg: Config) -> list[str]:
    """Betriebsrelevante Konfigurationsmängel, die beim Start gemeldet werden."""
    issues: list[str] = []
    if not cfg.pepper or cfg.pepper.startswith("BITTE-ERSETZEN"):
        issues.append(
            "Kein eigener Pepper gesetzt (app.pepper bzw. POLICYACK_PEPPER). "
            "Token-Hashes sind damit nicht organisationsspezifisch."
        )
    local = any(host in cfg.base_url for host in ("localhost", "127.0.0.1", "[::1]"))
    if not cfg.base_url.startswith("https://") and not local:
        issues.append(
            f"base_url ist nicht HTTPS ({cfg.base_url}). Bestätigungstoken würden "
            "unverschlüsselt übertragen."
        )
    return issues
