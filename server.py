import os
import time
import threading
import pickle
import smtplib
import ssl
from datetime import datetime, date, timedelta
from flask import Flask, jsonify, request

DATA_FILE = "coin_data.pkl"
STATE_FILE = "email_state.pkl"
CHECK_INTERVAL_SEC = 30

SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_USER     = os.getenv("SMTP_USER") #studyhard9024@gmail.com
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") #ofrx gxom ksfp lzrs
EMAIL_TO      = os.getenv("EMAIL_TO") #misa.s250211@ggh.goe.go.kr
UPLOAD_TOKEN  = os.getenv("UPLOAD_TOKEN") #예: supersecret123

def fmt_hms(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:02}"

def load_timer_state():
    if not os.path.exists(DATA_FILE):
        return None
    try:
        with open(DATA_FILE, "rb") as f:
            data = pickle.load(f)
        return data
    except Exception as e:
        print("[load_timer_state] 에러:", e)
        return None

def save_timer_state(data: dict):
    try:
        with open(DATA_FILE, "wb") as f:
            pickle.dump(data, f)
    except Exception as e:
        print("[save_timer_state] 에러:", e)

def load_last_sent_date():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "rb") as f:
            state = pickle.load(f)
        return state.get("last_sent_for_date")
    except Exception as e:
        print("[load_last_sent_date] 에러:", e)
        return None

def save_last_sent_date(d: str):
    try:
        with open(STATE_FILE, "wb") as f:
            pickle.dump({"last_sent_for_date": d}, f)
    except Exception as e:
        print("[save_last_sent_date] 에러:", e)

def send_daily_email(summary_date: str, secs: int, coins: int):
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        print("[메일 SKIP] SMTP 설정이 비어 있음")
        return

    subject = f"[학습 타이머] {summary_date} 공부 보고서"
    body_lines = [
        f"📅 날짜: {summary_date}",
        f"⏱ 공부 가동 시간: {fmt_hms(secs)} ({secs}초)",
        f"💰 적립 코인: {coins}",
        "",
        "오늘도 수고했어요.",
    ]
    body = "\n".join(body_lines)

    msg = (
        f"Subject: {subject}\n"
        f"To: {EMAIL_TO}\n"
        f"From: {SMTP_USER}\n\n"
        f"{body}"
    )

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, EMAIL_TO, msg)

    print(f"[메일 전송 완료] {summary_date}: {fmt_hms(secs)} / {coins}코인")

def watcher_loop(app):
    # Render는 프로세스가 재시작할 수 있으니까 Flask context 안에서 돌리자
    print("[watcher] 시작")
    with app.app_context():
        while True:
            data = load_timer_state()
            if data is not None:
                coins = int(data.get("coins", 0))
                today_on_seconds = int(data.get("today_on_seconds", 0))

                today = date.today()
                yesterday = today - timedelta(days=1)
                yesterday_str = yesterday.isoformat()

                last_sent = load_last_sent_date()
                if last_sent != yesterday_str:
                    try:
                        send_daily_email(
                            summary_date=yesterday_str,
                            secs=today_on_seconds,
                            coins=coins,
                        )
                        save_last_sent_date(yesterday_str)
                    except Exception as e:
                        print("[watcher] 메일 전송 실패:", e)

            time.sleep(30)

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    data = load_timer_state()
    last_sent = load_last_sent_date()
    return jsonify({
        "status": "ok",
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "timer_state": data,
        "last_sent_for_date": last_sent,
    })

@app.route("/upload_state", methods=["POST"])
def upload_state():
    client_token = request.json.get("token")
    if UPLOAD_TOKEN and client_token != UPLOAD_TOKEN:
        return jsonify({"ok": False, "error": "invalid token"}), 403

    timer_data = request.json.get("data")
    if not isinstance(timer_data, dict):
        return jsonify({"ok": False, "error": "data must be dict"}), 400

    required_keys = ["date", "today_on_seconds", "coins"]
    missing = [k for k in required_keys if k not in timer_data]
    if missing:
        return jsonify({"ok": False, "error": f"missing keys: {missing}"}), 400

    save_timer_state(timer_data)
    print("[upload_state] 업로드 수신:", timer_data.get("date"), timer_data.get("today_on_seconds"))

    return jsonify({"ok": True})

# Render에서 gunicorn이 이 객체를 찾음
def start_watcher():
    t = threading.Thread(target=watcher_loop, args=(app,), daemon=True)
    t.start()

start_watcher()




