"""【ローカルで一度だけ実行】Gmail 送信用のリフレッシュトークンを取得する。

Gmail API（HTTPS）でメール送信するための OAuth2 リフレッシュトークンを、
ブラウザの同意フロー（loopback）で取得して表示する。標準ライブラリのみ。

前提:
  Google Cloud で「OAuth クライアント ID（種類: デスクトップ アプリ）」を作成済み。
  Gmail API を有効化済み。OAuth 同意画面（Google Auth Platform）を「本番に公開」済み。

使い方（Git Bash）:
  python scripts/get_gmail_token.py
  → クライアント ID とシークレットを貼り付け → ブラウザで自分の Google アカウントを
     選び、「未確認アプリ」警告は 詳細→移動→続行 で許可 → ターミナルに表示される
     GMAIL_REFRESH_TOKEN を控える。
  ※ ブラウザが「127.0.0.1 で接続が拒否」になっても、アドレス欄の URL 全体を
     ターミナルに貼り付ければ復旧できる（このスクリプトが促す）。

取得した値は claude.ai のカスタム環境の環境変数に
  GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN
として登録する（このスクリプトは保存しない）。
"""
import sys
import time
import json
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

SCOPE = "https://www.googleapis.com/auth/gmail.send"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PORT = 8765  # デスクトップ クライアントは loopback の任意ポートを許可
WAIT_SECONDS = 600  # 待ち受け（10分）

_holder = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = (params.get("code") or [None])[0]
        err = (params.get("error") or [None])[0]
        if code or err:  # favicon 等の付随リクエストでは上書きしない
            _holder["code"], _holder["error"] = code, err
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if _holder.get("code"):
            body = "認証が完了しました。ターミナルに戻ってください。"
        elif _holder.get("error"):
            body = "認証に失敗しました。ターミナルに戻ってください。"
        else:
            body = "待機中..."
        self.wfile.write(f"<html><body><h2>{body}</h2></body></html>".encode("utf-8"))

    def log_message(self, *a):
        pass  # サーバログを抑制


def _exchange(code, client_id, client_secret, redirect_uri):
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    client_id = input("GMAIL_CLIENT_ID を貼り付け: ").strip()
    client_secret = input("GMAIL_CLIENT_SECRET を貼り付け: ").strip()
    redirect_uri = f"http://127.0.0.1:{PORT}"

    auth = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })

    server = None
    try:
        server = HTTPServer(("127.0.0.1", PORT), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    except OSError as e:
        print(f"（ローカル受信サーバを起動できませんでした: {e}。URL 貼り付けで進めます）")

    print("\nブラウザを開きます。自分の Google アカウントを選び、")
    print("「未確認アプリ」警告は［詳細］→［（アプリ名）に移動］→［続行］で許可してください。")
    print("（開かない場合は次の URL を手動で開く）\n" + auth + "\n")
    try:
        webbrowser.open(auth)
    except Exception:
        pass

    print(f"認証コードの受信を待っています（最大 {WAIT_SECONDS // 60} 分）...")
    for _ in range(WAIT_SECONDS):
        if _holder:
            break
        time.sleep(1)
    if server:
        try:
            server.shutdown()
        except Exception:
            pass

    # 自動受信できなかった場合の手動復旧（アドレス欄の URL を貼り付け）
    if not _holder.get("code") and not _holder.get("error"):
        print("\n自動で受信できませんでした。ブラウザのアドレス欄の URL 全体")
        print("（http://127.0.0.1:8765/?code=... の形）をコピーして貼り付けてください。")
        pasted = input("URL: ").strip()
        params = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        _holder["code"] = (params.get("code") or [None])[0]
        _holder["error"] = (params.get("error") or [None])[0]

    if _holder.get("error") or not _holder.get("code"):
        sys.exit(f"認証に失敗しました: {_holder.get('error')}")

    tok = _exchange(_holder["code"], client_id, client_secret, redirect_uri)
    rt = tok.get("refresh_token")
    if not rt:
        sys.exit("refresh_token が返りませんでした。"
                 "（既に許可済みの場合は Google アカウントのアクセス権を一度取り消して再実行）")

    print("\n================ これを控える ================")
    print(f"GMAIL_REFRESH_TOKEN={rt}")
    print("=============================================")
    print("claude.ai のカスタム環境の環境変数に登録してください（ここには貼らない）。")


if __name__ == "__main__":
    main()
