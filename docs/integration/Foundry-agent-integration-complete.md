# Foundry 旅遊分鏡 Agent：應用程式整合指南

版本日期：2026-11-20

## 1. 這份文件適用的服務

其他應用程式傳入 UTF-8 行程文字，由已部署於 Microsoft Foundry 的 `travel-image-agent` 產生手寫風格旅遊分鏡，直接取得 PNG 圖片。

此公開範本的內部圖片部署預設為 `MAI-Image-2.7-Flash`。請使用自己的 Foundry 專案與租用戶設定；本文件沒有提供可共用的服務或帳號。

應用程式 → Entra 驗證 → Foundry Hosted Agent → 行程提示整理 → MAI 產圖 → PNG 回傳。

此模式不使用 Blob、公開圖片網址、SAS 或 API key。圖片回傳後，由呼叫端決定儲存、顯示或下載位置。

這是目前已實作的自訂 Invocations 合約，不是通用 Responses API 範例。請使用下面完整的 agent 呼叫網址；不要把請求送到專案首頁或直接送到 MAI 模型端點。

## 2. 連線設定與請求合約

Project endpoint：

```text
https://<account>.services.ai.azure.com/api/projects/<project>
```

完整呼叫網址（單一 URL）：

```text
https://<account>.services.ai.azure.com/api/projects/<project>/agents/travel-image-agent/endpoint/protocols/invocations?api-version=v1
```

HTTP 方法：`POST`

Entra token scope：`https://ai.azure.com/.default`

請求標頭：

```http
Authorization: Bearer <由 Azure Identity 取得的 token>
Content-Type: application/json
Accept: image/png
```

請求內容：

```json
{
  "description": "虛構城市 3 天 2 夜。第 1 天河岸公園；第 2 天美術館與花園；第 3 天市集與返程。",
  "response_format": "image"
}
```

`description`：必要字串，不能空白，最多 12,000 個字元。可以包含每日活動、交通、住宿及旅遊偏好。

`response_format`：本整合固定傳 `"image"`。省略時，agent 預設會走 `"url"` 私人 Blob 流程，需要額外的儲存設定、權限與網路連線。

輸出由 agent 的既有提示決定：依提供的日程產生手寫、墨線與水彩風格分鏡；這個呼叫合約沒有額外的 `model`、尺寸或風格參數。模型選擇由 agent 部署管理，呼叫端無須直接呼叫 MAI。

## 3. 成功回應：直接接收圖片

成功狀態為 HTTP 200，`Content-Type` 為 `image/png`，body 是 PNG 二進位資料。

本 agent 額外回傳：

- `x-image-sha256`：PNG 的 SHA-256，用來檢查下載完整性。
- `x-agent-invocation-id`：追蹤這次執行的識別碼。
- `Content-Disposition`：建議的圖片檔名。

請使用 `response.content`、串流或 `arrayBuffer()` 讀取二進位資料。不要對成功回應呼叫 `response.json()`，也不需要 Base64 解碼。

目前 agent 產生 1024 × 1024 PNG。下方範例的 30 MiB 限制是呼叫端保護設定，不是聲稱 Foundry 的通用傳輸上限。

## 4. 驗證與權限

本機開發：安裝 Azure CLI，登入有權呼叫這個 agent 的租用戶。需要 MFA 時，請完成登入流程。

```powershell
$env:AZURE_TENANT_ID = "<tenant-id>"
$env:FOUNDRY_AGENT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>/agents/travel-image-agent/endpoint/protocols/invocations?api-version=v1"
az login --tenant $env:AZURE_TENANT_ID
```

Python 範例使用 `AzureCliCredential` 讀取這次登入，不會把 token 寫入檔案、HTML 或瀏覽器儲存空間。

部署到 Azure：為 App Service、Functions、Container Apps 或其他支援的運算環境啟用 Managed Identity，並讓專案管理員授予該 identity 呼叫 agent 的權限。系統指派 identity 不需要 client ID；使用者指派 identity 則傳入它的 client ID。

呼叫此端點可使用內建角色 `Foundry Agent Consumer`，其 data action 為 `Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action`。請管理員在此 Foundry 專案的適當最小範圍授予實際呼叫者；無須為了呼叫而授予模型部署或資源管理權限。

呼叫者權限與 agent 執行身分是兩層：其他應用程式需要 agent invocation 權限；agent 自己的 Managed Identity 負責呼叫對話模型與 MAI。請勿把既有 agent 的 identity 當作其他應用程式已自動取得的權限。

本機的 `az login` 不會替另一台伺服器或另一個 Managed Identity 授權。請管理員在正確租用戶與專案範圍核對實際角色，再由該應用程式測試一次呼叫。

若採用自訂 `AZURE_CONFIG_DIR` 登入，執行範例的同一個程序也必須設定相同目錄，否則它會讀取另一個 Azure CLI 登入快取。本整合範例不會自行改動使用者的 CLI 預設設定。

呼叫 Foundry agent 使用 `https://ai.azure.com/.default`。agent 內部直接呼叫 MAI 使用的 Cognitive Services token scope 屬於另一層，不要混用。

## 5. 可直接執行的 Python 範例

隨附 `generate_travel_image.py` 是獨立應用程式範例，不需複製整個 agent 專案，也不需先啟動本機 HTML 預覽服務。

安裝：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

將行程以 UTF-8 儲存為 `itinerary.txt`，然後執行：

```powershell
.\.venv\Scripts\python.exe generate_travel_image.py --input itinerary.txt --output travel-storyboard.png --auth cli
```

Azure 系統指派 Managed Identity：

```powershell
python generate_travel_image.py --input itinerary.txt --output travel-storyboard.png --auth managed-identity
```

Azure 使用者指派 Managed Identity：

```powershell
python generate_travel_image.py --input itinerary.txt --output travel-storyboard.png --auth managed-identity --client-id <identity-client-id>
```

範例使用明確的驗證方式，不會在 Managed Identity 失敗時改用另一個登入身分。請在有 Managed Identity 端點的 Azure 執行環境測試該模式；一般筆電使用 `--auth cli`。

### 在既有 Python 後端呼叫

把隨附範例放進你的後端專案後：

```python
from pathlib import Path
from generate_travel_image import generate_image

itinerary = "虛構城市三天兩夜，依序安排河岸公園、美術館、花園、市集與返程。"
png_bytes, evidence = generate_image(
    itinerary,
    auth="managed-identity",
)
Path("storyboard.png").write_bytes(png_bytes)
print(evidence["invocation_id"])
```

Web 後端也可以直接把 `png_bytes` 以 HTTP `Content-Type: image/png` 回傳前端，而不寫入 Blob。請保留你原本的使用者登入、請求限制與授權檢查。

## 6. 其他語言的實作對照

HTTP 合約與程式語言無關。取得正確的 Entra bearer token 後：

- JavaScript / Node.js：`fetch()` 傳送 JSON；用 `await response.arrayBuffer()` 取得 PNG。
- C#：`HttpClient.PostAsync()` 傳送 JSON；用 `ReadAsByteArrayAsync()` 取得 PNG。
- Python：隨附範例以 `httpx` 串流接收 PNG，檢查狀態、內容類型、PNG 簽名與 SHA-256，再寫入檔案。

一般瀏覽器網頁應呼叫自己的已驗證後端，再由後端使用 Managed Identity 呼叫 Foundry。不要把伺服器的 bearer token 或 CLI token 放進 HTML。瀏覽器直接登入 Entra 是另一個需要 app registration、權限與 CORS 設定的設計，本範例未實作。

## 7. 逾時、重試與錯誤處理

圖片生成是長時間操作。範例使用 600 秒的 HTTP timeout；你的 Web 後端、反向代理與前端等待設定也需要容許實際的產圖時間。正式系統若有短 HTTP 限制，可另行設計背景工作，但不要把本合約當成已提供工作佇列 API。

範例沒有自動重試產圖 POST。網路逾時後，伺服器可能仍在產圖；直接重送可能產生第二張圖片及額外用量費用。由使用者確認後重試，並記錄 invocation ID。

常見診斷：

- 401：檢查 token 是否過期、租用戶與 scope 是否正確。
- 403：檢查呼叫者的 agent 權限及網路規則。
- 404：檢查完整 URL、agent 名稱及部署狀態。
- 422：檢查 `description` 長度及 `response_format` 值。
- 429：可能受到服務速率或容量限制；先檢查回應與服務狀態。
- 502：本 agent 以 JSON `error` 回報內部流程失敗；請記錄錯誤及 invocation ID，檢查對話模型／MAI 驗證與部署。

不要把非 200 的 JSON／HTML 錯誤儲存為 `.png`，不要記錄 Authorization 標頭或大量圖片 Base64。

## 8. 部署管理與交接

這個 URL 使用 agent endpoint，沒有把特定版本寫進路徑。模型或提示更新後，應由服務擁有者確認 endpoint 實際路由到的 agent 版本，再通知整合方。

由服務擁有者確認目前 ACTIVE 版本與 endpoint 的流量路由。整合程式不應把管理版本頁面的 `/versions/<version>` 網址當成呼叫端點。

整合方不需把 `MAI-Image-2.7` 或 `MAI-Image-2.7-Flash` 放進此 agent 的 JSON 請求；那是 agent 內部的部署設定。

交接清單：確認租用戶與專案、授予應用程式 identity invocation 權限、準備 UTF-8 行程檔、執行隨附 Python 範例、確認 PNG 可開啟、保留 invocation ID，最後再接上自己的前端與授權流程。

這個文件的 Python CLI 範例與 Managed Identity 分支使用相同 HTTP 合約。以實際測試紀錄區分已完成的 CLI 呼叫與尚未在其他應用程式 identity 下執行的環境設定。

### 驗證範圍與正式交接

原始部署與獨立 CLI 範例已完成實際 Foundry → MAI → PNG 呼叫與圖片檢視。此公開版本移除了原始端點、私人行程、執行識別碼與產圖檔案，改用可設定的環境變數；沒有為公開整理再觸發付費產圖。

請先設定自己的端點與租用戶，再執行一次經授權的實際呼叫。Managed Identity 呼叫端需要在自己的 Azure 運算環境及權限下驗證。可先執行專案測試，檢查直接圖片合約與文件中的程式同步；這些測試不會產圖。

## 9. 官方參考

- Invoke a hosted agent: https://learn.microsoft.com/azure/foundry/agents/how-to/invoke-hosted-agent
- Azure Identity for Python: https://learn.microsoft.com/python/api/overview/azure/identity-readme
- Managed identities: https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/overview
- MAI image models: https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-mai-image

## 10. 完整 Python 程式（與隨附檔案一致）

將下列內容儲存為 generate_travel_image.py；依第 5 節安裝依賴及執行。

端點可由 `FOUNDRY_AGENT_ENDPOINT` 或 `--endpoint` 提供；CLI 租用戶可由 `AZURE_TENANT_ID` 或 `--tenant-id` 提供。程式不會自動載入 `.env`。

```python
"""Call the deployed Foundry travel agent and save its direct PNG response."""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import AzureCliCredential, ManagedIdentityCredential


SCOPE = "https://ai.azure.com/.default"
MAX_BYTES = 30 * 1024 * 1024


def generate_image(description, *, endpoint=None, auth="cli", client_id=None, tenant_id=None):
    """Return verified PNG bytes and evidence; never upload to Blob Storage."""
    if not description.strip() or len(description) > 12000:
        raise ValueError("Provide 1 to 12000 characters of itinerary text.")
    endpoint = endpoint or os.environ.get("FOUNDRY_AGENT_ENDPOINT", "").strip()
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError("Set FOUNDRY_AGENT_ENDPOINT or --endpoint to your HTTPS Invocations URL.")
    if auth not in ("cli", "managed-identity"):
        raise ValueError("auth must be cli or managed-identity.")
    if client_id and auth != "managed-identity":
        raise ValueError("client_id applies only to managed-identity authentication.")
    tenant_id = tenant_id or os.environ.get("AZURE_TENANT_ID", "").strip()
    if auth == "cli" and not tenant_id:
        raise ValueError("Set AZURE_TENANT_ID or --tenant-id for Azure CLI authentication.")

    started = time.perf_counter()
    credential = (
        AzureCliCredential(tenant_id=tenant_id, process_timeout=60)
        if auth == "cli"
        else ManagedIdentityCredential(**({"client_id": client_id} if client_id else {}))
    )
    with credential, httpx.Client(timeout=httpx.Timeout(600, connect=20)) as client:
        token = credential.get_token(SCOPE).token
        with client.stream(
            "POST",
            endpoint,
            headers={"Authorization": f"Bearer {token}", "Accept": "image/png"},
            json={"description": description, "response_format": "image"},
        ) as response:
            response.raise_for_status()
            if response.headers.get("content-type", "").split(";")[0] != "image/png":
                raise ValueError("Expected image/png, not JSON or HTML.")
            image = bytearray()
            for chunk in response.iter_bytes():
                if len(image) + len(chunk) > MAX_BYTES:
                    raise ValueError("Response exceeds this client's 30 MiB limit.")
                image.extend(chunk)
            digest = hashlib.sha256(image).hexdigest()
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Response does not have a PNG signature.")
            if response.headers.get("x-image-sha256") != digest:
                raise ValueError("PNG integrity verification failed.")
            evidence = {
                "http_status": response.status_code,
                "content_type": "image/png",
                "image_bytes": len(image),
                "sha256": digest,
                "invocation_id": response.headers.get("x-agent-invocation-id"),
                "elapsed_seconds": round(time.perf_counter() - started, 2),
            }
    return bytes(image), evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 itinerary file")
    parser.add_argument("--output", type=Path, default=Path("travel-storyboard.png"))
    parser.add_argument("--auth", choices=("cli", "managed-identity"), default="cli")
    parser.add_argument("--client-id", help="User-assigned managed identity client ID")
    parser.add_argument("--endpoint", help="HTTPS Invocations URL; defaults to FOUNDRY_AGENT_ENDPOINT")
    parser.add_argument("--tenant-id", help="CLI tenant; defaults to AZURE_TENANT_ID")
    args = parser.parse_args()
    try:
        image, evidence = generate_image(
            args.input.read_text(encoding="utf-8-sig"),
            endpoint=args.endpoint,
            auth=args.auth,
            client_id=args.client_id,
            tenant_id=args.tenant_id,
        )
        args.output.write_bytes(image)
    except (ClientAuthenticationError, httpx.HTTPError, OSError, ValueError) as exc:
        print(f"Image generation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({**evidence, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
