import os
import time
import json
import requests
from zk import ZK
from datetime import datetime, timedelta
from collections import defaultdict
from pytz import timezone, utc

# ---------------- إعدادات ----------------
DEVICE_IP = '192.168.6.251'
LAST_SENT_FILE = r"C:\AttendanceApp\last_sent.txt"
PENDING_FILE = r"C:\AttendanceApp\pending_logs.json"
SLEEP_INTERVAL = 600  # كل 10 دقايق
odoo_url = "URL"
db = "DataBase"
login = "Username"
password = "Password"

# ---------------- دوال أساسية ----------------
def authenticate():
    url = f"{odoo_url}/web/session/authenticate"
    payload = {"jsonrpc": "2.0", "method": "call", "params": {"db": db, "login": login, "password": password}}
    response = requests.post(url, json=payload)
    return response.cookies.get("session_id")

def send_to_odoo(logs, session_id):
    url = f"{odoo_url}/api/attendance"
    headers = {"Content-Type": "application/json", "Cookie": f"session_id={session_id}"}
    requests.post(url, headers=headers, json={"params": {"logs": logs}})

def to_utc(dt):
    local_tz = timezone('Africa/Cairo')
    return local_tz.localize(dt).astimezone(utc)

def load_last_timestamp():
    if os.path.exists(LAST_SENT_FILE):
        with open(LAST_SENT_FILE, "r") as f:
            return datetime.strptime(f.read().strip(), "%Y-%m-%d %H:%M:%S")
    return datetime(2025, 1, 1)  # أول تشغيل

def save_last_timestamp(ts):
    with open(LAST_SENT_FILE, "w") as f:
        f.write(ts.strftime("%Y-%m-%d %H:%M:%S"))

def load_pending():
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, "r") as f:
            return json.load(f)
    return {}

def save_pending(pending):
    with open(PENDING_FILE, "w") as f:
        json.dump(pending, f, indent=2)

# ---------------- البرنامج الرئيسي ----------------
zk = ZK(DEVICE_IP, port=4370, timeout=60)

while True:
    try:
        conn = zk.connect()
        attendance = conn.get_attendance()
        conn.disconnect()

        last_sent_time = load_last_timestamp()
        pending_logs = load_pending()

        if not os.path.exists(LAST_SENT_FILE):
            start_date = datetime(2025, 1, 1)  # أول تشغيل
        else:
            start_date = datetime.now() - timedelta(days=1)

        end_date = datetime.now()

        # تصفية الحركات الجديدة
        new_logs = [
            rec for rec in attendance
            if rec.timestamp > last_sent_time and start_date <= rec.timestamp <= end_date
        ]

        if not new_logs:
            time.sleep(SLEEP_INTERVAL)
            continue

        raw_logs = defaultdict(list)
        for rec in new_logs:
            raw_logs[str(rec.user_id)].append({
                "timestamp": rec.timestamp,
                "punch": rec.punch
            })

        final_logs = []
        latest_time = last_sent_time

        for user_id, logs in raw_logs.items():
            logs = sorted(logs, key=lambda x: x["timestamp"])
            last_punch_time = {}

            for log in logs:
                punch = log["punch"]
                ts = log["timestamp"]

                # تجاهل البصمات المكررة في أقل من ساعة
                if punch not in last_punch_time or (ts - last_punch_time[punch]).total_seconds() > 3600:
                    last_punch_time[punch] = ts

                    if ts > latest_time:
                        latest_time = ts

                    if punch == 0:  # Check-in
                        # ابعته مباشرة وسجله في pending
                        final_logs.append({
                            "user_id": int(user_id),
                            "timestamp": to_utc(ts).strftime("%Y-%m-%d %H:%M:%S"),
                            "status": "check_in"
                        })
                        pending_logs[user_id] = {"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S")}

                    elif punch == 1:  # Check-out
                        if user_id in pending_logs:
                            in_time = datetime.strptime(pending_logs[user_id]["timestamp"], "%Y-%m-%d %H:%M:%S")
                            if 0 < (ts - in_time).total_seconds() <= 86400:  # <= 24 ساعة
                                final_logs.append({
                                    "user_id": int(user_id),
                                    "timestamp": to_utc(ts).strftime("%Y-%m-%d %H:%M:%S"),
                                    "status": "check_out"
                                })
                            del pending_logs[user_id]
                        else:
                            # مفيش دخول → دخول وهمي
                            fake_in = datetime.combine(ts.date(), datetime.min.time())
                            final_logs.append({
                                "user_id": int(user_id),
                                "timestamp": to_utc(fake_in).strftime("%Y-%m-%d %H:%M:%S"),
                                "status": "check_in"
                            })
                            final_logs.append({
                                "user_id": int(user_id),
                                "timestamp": to_utc(ts).strftime("%Y-%m-%d %H:%M:%S"),
                                "status": "check_out"
                            })

        # تنظيف pending من أي دخول عدى عليه أكتر من 24 ساعة
        now = datetime.now()
        pending_logs = {
            uid: data for uid, data in pending_logs.items()
            if (now - datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S")).total_seconds() <= 86400
        }

        # إرسال لأودو
        if final_logs:
            session_id = authenticate()
            if session_id:
                send_to_odoo(final_logs, session_id)
                save_last_timestamp(latest_time)

        save_pending(pending_logs)

    except Exception as e:
        print("Error:", e)

    time.sleep(SLEEP_INTERVAL)

