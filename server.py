import os
import time
import threading
import pickle
import requests
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, request

# ---------------------- 설정 ----------------------
DATA_FILE = "coin_data.pkl"
STATE_FILE = "email_state.pkl"
CHECK_INTERVAL_SEC = 30

# 환경변수 (Render에서 설정)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
UPLOAD_TOKEN = os.getenv("UPLOAD_TOKEN")

# SendGrid (Render SMTP 차단 대비)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)  # 없으면 SMTP_USER로 대체

# ---------------------- 도우미 함수 ----------------------
def fmt_hms(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:02}"

def load_timer_state():
    """coin_data.pkl 읽기"""
    if not os.path.exists(DATA_FILE):
        return None
    try:
        with open(DATA_FILE, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        print("[load_timer_state] 에러:", e)
        return None

def save_timer_state(data: dict):
    """coin_data.pkl 쓰기"""
    try:
        with open(DATA_FILE, "wb") as f:
            pickle.dump(data, f)
    except Exception as e:
        print("[save_timer_state] 에러:", e)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_sent_for_date": None, "last_snapshot": None}
    try:
        with open(STATE_FILE, "rb") as f:
            st = pickle.load(f)
        if "last_snapshot" not in st:
            st["last_snapshot"] = None
        return st
    except Exception as e:
        print("[load_state] 에러:", e)
        return {"last_sent_for_date": None, "last_snapshot": None}

def save_state(state: dict):
    try:
        with open(STATE_FILE, "wb") as f:
            pickle.dump(state, f)
    except Exception as e:
        print("[save_state] 에러:", e)

# ---------------------- 메일 전송 함수 ----------------------
def send_daily_email(summary_date: str, secs: int, coins: int):
    """메일 전송 (SendGrid 우선, 실패 시 SMTP 백업)"""
    subj = f"[학습 타이머] {summary_date} 공부 보고서"
    body = (
        f"날짜: {summary_date}\n"
        f"공부 가동 시간: {fmt_hms(secs)} ({secs}초)\n"
        f"적립 코인: {coins}\n\n"
        "오늘도 수고했어요."
    )

    # SendGrid API (HTTPS) 시도
    if SENDGRID_API_KEY:
        try:
            resp = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": EMAIL_TO}]}],
                    "from": {"email": EMAIL_FROM},
                    "subject": subj,
                    "content": [{"type": "text/plain", "value": body}],
                },
                timeout=15,
            )
            if resp.status_code in (200, 202):
                print(f"[메일 전송 완료: SendGrid] {summary_date} → {EMAIL_TO}")
                return
            else:
                print("[메일 실패: SendGrid]", resp.status_code, resp.text)
        except Exception as e:
            print("[메일 실패: SendGrid 예외]", e)

    # SMTP 백업 (Render 무료 플랜에서는 실패 가능)
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        print("[메일 SKIP] SMTP 환경변수 부족(SMTP_USER/SMTP_PASSWORD/EMAIL_TO)")
        return

    try:
        msg = EmailMessage()
        msg["Subject"] = subj
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO
        msg.set_content(body, subtype="plain", charset="utf-8")

        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            if SMTP_PORT == 587:
                server.starttls(context=context)
                server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"[메일 전송 완료: SMTP] {summary_date} → {EMAIL_TO}")
    except Exception as e:
        print("[메일 실패: SMTP 경로]", e)

# ---------------------- 날짜 변경 감지 ----------------------
def watcher_loop(app):
    print("[watcher] 시작")
    with app.app_context():
        while True:
            data = load_timer_state()
            if data:
                cur = {
                    "date": data.get("date"),
                    "secs": int(data.get("today_on_seconds", 0)),
                    "coins": int(data.get("coins", 0)),
                }

                state = load_state()
                snap = state.get("last_snapshot")
                last_sent = state.get("last_sent_for_date")

                if snap is None:
                    state["last_snapshot"] = cur
                    save_state(state)
                else:
                    if cur["date"] != snap["date"]:
                        if last_sent != snap["date"]:
                            try:
                                send_daily_email(
                                    snap["date"], snap["secs"], snap["coins"]
                                )
                                state["last_sent_for_date"] = snap["date"]
                            except Exception as e:
                                print("[watcher] 메일 전송 실패:", e)
                        state["last_snapshot"] = cur
                        save_state(state)
                    else:
                        if (
                            cur["secs"] != snap["secs"]
                            or cur["coins"] != snap["coins"]
                        ):
                            state["last_snapshot"] = cur
                            save_state(state)

            time.sleep(CHECK_INTERVAL_SEC)

# ---------------------- Flask 서버 ----------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    data = load_timer_state()
    last_sent = load_state().get("last_sent_for_date")
    return jsonify({
        "status": "ok",
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "timer_state": data,
        "last_sent_for_date": last_sent,
    })

@app.route("/upload_state", methods=["POST"])
def upload_state():
    body = request.get_json(silent=True) or {}
    client_token = body.get("token")
    if UPLOAD_TOKEN and client_token != UPLOAD_TOKEN:
        return jsonify({"ok": False, "error": "invalid token"}), 403

    timer_data = body.get("data")
    if not isinstance(timer_data, dict):
        return jsonify({"ok": False, "error": "data must be dict"}), 400

    save_timer_state(timer_data)
    print("[upload_state] 업로드 수신:", timer_data.get("date"), timer_data.get("today_on_seconds"))
    return jsonify({"ok": True})

@app.route("/force_send", methods=["POST"])
def force_send():
    data = load_timer_state()
    if not data:
        return jsonify({"ok": False, "error": "no data (coin_data.pkl 없음)"}), 400
    try:
        send_daily_email(
            date.today().isoformat(),
            int(data.get("today_on_seconds", 0)),
            int(data.get("coins", 0)),
        )
        return jsonify({"ok": True, "sent_for": date.today().isoformat()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/env_check", methods=["GET"])
def env_check():
    """환경 변수 유효성 검사용 라우트"""
    return jsonify({
        "SMTP_USER_set": bool(SMTP_USER),
        "SMTP_PASSWORD_set": bool(SMTP_PASSWORD),
        "EMAIL_TO_set": bool(EMAIL_TO),
        "SENDGRID_API_KEY_set": bool(SENDGRID_API_KEY),
        "SMTP_HOST": SMTP_HOST,
        "SMTP_PORT": SMTP_PORT,
    })

# ---------------------- 실행 ----------------------
def start_watcher():
    t = threading.Thread(target=watcher_loop, args=(app,), daemon=True)
    t.start()

start_watcher()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
