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
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
UPLOAD_TOKEN = os.getenv("UPLOAD_TOKEN")

if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
    print("[메일 SKIP] SMTP_USER/SMTP_PASSWORD/EMAIL_TO 중 누락 있음")
    # return or raise

#포트 자동 전환 옵션
USE_SSL = str(os.getenv("SMTP_SSL", "false")).lower() == "true"
if USE_SSL:
    with smtplib.SMTP_SSL(SMTP_HOST, 465, context=context, timeout=20) as server:
        ...
else:
    with smtplib.SMTP(SMTP_HOST, 587, timeout=20) as server:
        server.starttls(context=context)
        ...
        
def fmt_hms(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:02}"

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_sent_for_date": None, "last_snapshot": None}
    try:
        with open(STATE_FILE, "rb") as f:
            st = pickle.load(f)
        # 호환성: 기존 키만 있는 경우 채워주기
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
    """Gmail SMTP로 일자별 요약 메일 전송 (진단 로그 강화)."""
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        print("[메일 SKIP] SMTP 환경변수 부족(SMTP_USER/SMTP_PASSWORD/EMAIL_TO)")
        return

    subj = f"[학습 타이머] {summary_date} 공부 보고서"
    body = (
        f" 날짜: {summary_date}\n"
        f" 공부 가동 시간: {fmt_hms(secs)} ({secs}초)\n"
        f" 적립 코인: {coins}\n\n"
        "오늘도 수고했어요."
    )

    # RFC에 맞는 헤더 구성(UTF-8)
    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO
    msg.set_content(body, subtype="plain", charset="utf-8")

    try:
        # 587(TLS) 경로. 만약 465를 쓰면 SMTP_SSL로 교체.
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            print(f"[SMTP] 서버 연결 시도: {SMTP_HOST}:{SMTP_PORT}")
            server.ehlo()
            if SMTP_PORT == 587:
                server.starttls(context=context)
                server.ehlo()
                print("[SMTP] STARTTLS 완료")
            server.login(SMTP_USER, SMTP_PASSWORD)
            print(f"[SMTP] 로그인 성공: user={SMTP_USER}")
            server.send_message(msg)
            print(f"[메일 전송 완료] {summary_date}: {fmt_hms(secs)} / {coins}코인 → {EMAIL_TO}")
    except smtplib.SMTPAuthenticationError as e:
        print("[메일 실패] 인증 오류(앱 비밀번호/SMTP_USER 확인 필요):", e)
        traceback.print_exc()
        raise
    except smtplib.SMTPRecipientsRefused as e:
        print("[메일 실패] 수신자 거부(EMAIL_TO 정책/오타 가능):", e)
        traceback.print_exc()
        raise
    except smtplib.SMTPException as e:
        print("[메일 실패] SMTP 예외:", e)
        traceback.print_exc()
        raise
    except Exception as e:
        print("[메일 실패] 일반 예외:", e)
        traceback.print_exc()
        raise

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, EMAIL_TO, msg)

    print(f"[메일 전송 완료] {summary_date}: {fmt_hms(secs)} / {coins}코인")

def watcher_loop(app):
    print("[watcher] 시작")
    with app.app_context():
        while True:
            data = load_timer_state()  # coin_data.pkl
            if data is not None:
                cur = {
                    "date": data.get("date"),  # studytime.py에서 올라오는 '오늘 날짜'
                    "secs": int(data.get("today_on_seconds", 0)),
                    "coins": int(data.get("coins", 0)),
                }

                state = load_state()
                snap = state.get("last_snapshot")  # {"date": "...", "secs": int, "coins": int} or None
                last_sent = state.get("last_sent_for_date")

                if snap is None:
                    # 최초 진입: 현재 스냅샷 저장만
                    state["last_snapshot"] = cur
                    save_state(state)

                else:
                    if cur["date"] != snap["date"]:
                        # 날짜가 바뀌었음을 감지 (클라이언트가 리셋된 직후 첫 업로드)
                        #  → '어제'의 최종값 = snap 으로 메일 전송
                        if last_sent != snap["date"]:
                            try:
                                send_daily_email(
                                    summary_date=snap["date"],
                                    secs=snap["secs"],
                                    coins=snap["coins"],
                                )
                                state["last_sent_for_date"] = snap["date"]
                                print(f"[watcher] {snap['date']} 메일 전송 완료")
                            except Exception as e:
                                print("[watcher] 메일 전송 실패:", e)

                        # 새 날의 스냅샷으로 교체
                        state["last_snapshot"] = cur
                        save_state(state)

                    else:
                        # 같은 날이면 최신 값으로 스냅샷 갱신(변화 있을 때만)
                        if (cur["secs"] != snap["secs"]) or (cur["coins"] != snap["coins"]):
                            state["last_snapshot"] = cur
                            save_state(state)

            time.sleep(CHECK_INTERVAL_SEC)

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

@app.route("/force_send", methods=["POST"])
def force_send():
    """서버에 저장된 최신 상태로 '오늘 날짜' 기준 즉시 메일 발송(디버그용)."""
    data = load_timer_state()
    if not data:
        return jsonify({"ok": False, "error": "no data (coin_data.pkl 없음)"}), 400

    coins = int(data.get("coins", 0))
    secs = int(data.get("today_on_seconds", 0))
    today_str = date.today().isoformat()

    try:
        send_daily_email(today_str, secs, coins)
        return jsonify({"ok": True, "sent_for": today_str})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

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

# Render에서 gunicorn이 app을 불러올 때 watcher도 시작
def start_watcher():
    t = threading.Thread(target=watcher_loop, args=(app,), daemon=True)
    t.start()

start_watcher()










