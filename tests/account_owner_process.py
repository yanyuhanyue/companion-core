"""Independent synthetic Bridge worker; never invokes any account provider."""

import json
import sys
from pathlib import Path

import httpx


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    assert config["url"].startswith("http://127.0.0.1:")
    with httpx.Client(base_url=config["url"], trust_env=False, headers={
        "Authorization": "Bearer " + config["token"]}) as client:
        response = client.post("/internal/v1/deliveries/lease", json={
            "worker_id": "independent-account-test", "authority": config["authority"],
            "delivery_lane": "qq_account",
            "account_actions_authorization_sha256": config["authorization_sha256"]})
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["authority"] == config["authority"]
        response = client.post(f"/internal/v1/deliveries/{item['message_id']}/begin-send",
                               json={"lease_token": item["lease_token"]})
        assert response.status_code == 200, response.text
        print("BEFORE_SEND_CHECK", flush=True)
        assert sys.stdin.readline().strip() == "continue"
        response = client.post("/internal/v1/account-actions/send-check", json={
            **{key: item[key] for key in ("authority", "message_id", "lease_token",
                                         "content_sha256")},
            "payload_sha256": config["payload_sha256"]})
        print(json.dumps({"status": response.status_code, "body": response.json()}), flush=True)


if __name__ == "__main__":
    main()
