"""上传（monitor_url 可配置；TLS 校验可配置）。"""
import requests


class Uploader:
    def __init__(self, cfg):
        self.base_url = cfg["monitor_url"].rstrip("/")
        self.token = cfg.get("ingest_token", "")
        self.verify = bool(cfg.get("verify_tls", True))
        self.session = requests.Session()

    def upload(self, body):
        url = f"{self.base_url}/api/ingest"
        headers = {"X-Ingest-Token": self.token}
        r = self.session.post(
            url, json=body, headers=headers, timeout=15, verify=self.verify
        )
        r.raise_for_status()
        return r.json()
