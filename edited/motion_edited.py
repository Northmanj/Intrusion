import cv2
import smtplib
import ssl
import threading
import queue
import sqlite3
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta
import os
from playsound import playsound
import time
import requests

# ---------------- CONFIG ----------------
WEMOS_IP = "http://192.168.100.200"
CAMERA_ID = "CAM1"

DB_NAME = "ids_logs.db"
ARCHIVE_DB = "archived_logs.db"
ARCHIVE_DAYS = 7  # rotate logs older than this many days

EMAIL_SENDER = "gregory@gmail.com"
EMAIL_PASSWORD = "sjbuycpxo"
EMAIL_RECEIVER = "gregory@gmail.com"

ALARM_SOUND = "alarm.wav"

ROI_X, ROI_Y, ROI_W, ROI_H = 50, 50, 500, 400
MARGIN_ZONE2 = 0.15
MARGIN_ZONE3 = 0.35

VIDEO_DURATION = 30
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

MOTION_TIMEOUT = 30
EMAIL_COOLDOWN = 60
RECORD_COOLDOWN = 30

# ---------------- STATE -------------------------
armed = False
motion_detected = False
last_detection_time = 0
last_email_time = 0
last_record_zone2_time = 0

# ---------------- RECORDING THREAD ---------------
record_queue = queue.Queue()

# ---------------- DATABASE / LOGGING --------------
# We store: id, timestamp, level, event_type, camera_id, clip_filename
def init_db(db_path=DB_NAME):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            level TEXT,
            event_type TEXT,
            camera_id TEXT,
            clip_filename TEXT
        )
    """)
    conn.commit()
    return conn

db_conn = init_db(DB_NAME)
db_lock = threading.Lock()

def log_event(level, event_type, clip_filename=None):
    """Insert log into DB and print to console. level is one of INFO/ALERT/CRITICAL."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        try:
            db_conn.execute(
                "INSERT INTO events (timestamp, level, event_type, camera_id, clip_filename) VALUES (?,?,?,?,?)",
                (ts, level, event_type, CAMERA_ID, clip_filename)
            )
            db_conn.commit()
        except Exception as e:
            # If DB write fails, still print to console (avoid crashing)
            print(f"[{ts}] CRITICAL DB_WRITE_FAILED: {e} | Original: {level} {event_type} | Clip: {clip_filename or 'N/A'}")
            return
    # Console print with level tag
    print(f"[{ts}] {level} | {event_type} | Camera: {CAMERA_ID} | Clip: {clip_filename or 'N/A'}")

# ---------------- LOG ROTATION ------------------
def rotate_logs():
    """
    Move rows older than ARCHIVE_DAYS from DB_NAME to ARCHIVE_DB.
    This function is safe to call repeatedly.
    """
    cutoff_dt = datetime.now() - timedelta(days=ARCHIVE_DAYS)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Open a new connection for archive DB (separate file)
    try:
        with db_lock:
            cur = db_conn.cursor()
            # fetch rows to archive
            cur.execute("SELECT id, timestamp, level, event_type, camera_id, clip_filename FROM events WHERE timestamp <= ?", (cutoff_str,))
            rows = cur.fetchall()
            if not rows:
                log_event("INFO", f"ROTATE: Nothing to archive before {cutoff_str}")
                return

            # Insert into archive DB
            arch_conn = sqlite3.connect(ARCHIVE_DB)
            arch_cur = arch_conn.cursor()
            arch_cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    level TEXT,
                    event_type TEXT,
                    camera_id TEXT,
                    clip_filename TEXT
                )
            """)
            arch_conn.commit()

            arch_cur.executemany(
                "INSERT INTO events (timestamp, level, event_type, camera_id, clip_filename) VALUES (?,?,?,?,?)",
                [(r[1], r[2], r[3], r[4], r[5]) for r in rows]
            )
            arch_conn.commit()
            arch_conn.close()

            # Remove from main DB
            ids_to_delete = [str(r[0]) for r in rows]
            placeholders = ",".join(["?"] * len(ids_to_delete))
            cur.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids_to_delete)
            db_conn.commit()

            log_event("INFO", f"ROTATE: Archived {len(rows)} events older than {cutoff_str} to {ARCHIVE_DB}")
    except Exception as e:
        log_event("CRITICAL", f"ROTATE_FAILED: {e}")

def rotation_worker():
    """Run rotate_logs at startup and then once every 24 hours in background."""
    try:
        rotate_logs()  # run immediately on startup
    except Exception as e:
        log_event("CRITICAL", f"ROTATE_STARTUP_FAILED: {e}")
    while True:
        try:
            # Sleep 24 hours (86400 seconds)
            time.sleep(86400)
            rotate_logs()
        except Exception as e:
            log_event("CRITICAL", f"ROTATION_WORKER_ERROR: {e}")
            # short sleep to avoid tight loop on failure
            time.sleep(60)

rotation_thread = threading.Thread(target=rotation_worker, daemon=True)
rotation_thread.start()

# ---------------- RECORDING WORKER ----------------
def recording_worker():
    """Background writer: receives (frame, overlay_text, start_flag)."""
    current_out = None
    current_filename = None
    end_time = 0
    try:
        while True:
            item = record_queue.get()
            if item is None:
                # flush current writer if any
                if current_out:
                    current_out.release()
                    log_event("INFO", "VIDEO_END", current_filename)
                break
            frame, overlay_text, start_flag = item

            if start_flag:
                # start a new recording file
                date_folder = os.path.join("clips", datetime.now().strftime("%Y-%m-%d"))
                os.makedirs(date_folder, exist_ok=True)
                current_filename = os.path.join(date_folder, f"motion_{datetime.now().strftime('%H%M%S')}.avi")
                fourcc = cv2.VideoWriter_fourcc(*'XVID')
                current_out = cv2.VideoWriter(current_filename, fourcc, 20.0, (FRAME_WIDTH, FRAME_HEIGHT))
                end_time = time.time() + VIDEO_DURATION
                log_event("INFO", "VIDEO_START", current_filename)

            # write current frame (if writer exists)
            if current_out is not None:
                if frame is not None:
                    # annotate overlay and timestamp (burned into clip)
                    cv2.putText(frame, overlay_text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50, 255, 50), 2)
                    cv2.putText(frame, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                (10, FRAME_HEIGHT - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200,200,200), 1)
                    current_out.write(frame)

            # try to pull extra frames without blocking too long until end_time
            while time.time() < end_time:
                try:
                    frame, overlay_text, new_start = record_queue.get(timeout=0.1)
                    if new_start:
                        # finish current file, push back new_start as immediate processing
                        break
                    if current_out is not None and frame is not None:
                        cv2.putText(frame, overlay_text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50, 255, 50), 2)
                        cv2.putText(frame, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    (10, FRAME_HEIGHT - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200,200,200), 1)
                        current_out.write(frame)
                except queue.Empty:
                    continue

            # close current recording if it exists
            if current_out:
                current_out.release()
                log_event("INFO", "VIDEO_END", current_filename)
                current_out = None
                current_filename = None
    except Exception as e:
        log_event("CRITICAL", f"RECORD_THREAD_ERROR: {e}")

record_thread = threading.Thread(target=recording_worker, daemon=True)
record_thread.start()

# ---------------- HELPERS -----------------------
def rect_from_margin(x, y, w, h, margin_ratio):
    mx = int(w * margin_ratio)
    my = int(h * margin_ratio)
    return (x + mx, y + my, w - 2 * mx, h - 2 * my)

def point_in_rect(px, py, rx, ry, rw, rh):
    return (px >= rx) and (py >= ry) and (px <= rx + rw) and (py <= ry + rh)

def get_zone_for_point(cx, cy):
    z2 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE2)
    z3 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE3)
    if point_in_rect(cx, cy, *z3): return 3
    elif point_in_rect(cx, cy, *z2): return 2
    elif point_in_rect(cx, cy, ROI_X, ROI_Y, ROI_W, ROI_H): return 1
    else: return 0

def get_side_relative_to_roi(cx, cy):
    left_third = ROI_X + ROI_W // 3
    right_third = ROI_X + (2 * ROI_W) // 3
    top_third = ROI_Y + ROI_H // 3
    if cx < left_third: return "Left"
    elif cx > right_third: return "Right"
    elif cy < top_third: return "Top"
    else: return "Bottom"

def draw_zones(frame):
    cv2.rectangle(frame, (ROI_X, ROI_Y), (ROI_X + ROI_W, ROI_Y + ROI_H), (255, 0, 0), 2)
    z2 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE2)
    z3 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE3)
    cv2.rectangle(frame, (z2[0], z2[1]), (z2[0]+z2[2], z2[1]+z2[3]), (0,255,255), 2)
    cv2.rectangle(frame, (z3[0], z3[1]), (z3[0]+z3[2], z3[1]+z3[3]), (0,0,255), 2)

# ---------------- WEMOS ----------------
def notify_wemos_fast():
    try:
        requests.get(f"{WEMOS_IP}/fast", timeout=2)
        log_event("INFO", "WEMOS_FAST")
    except Exception as e:
        log_event("ALERT", f"WEMOS_ERROR_FAST: {e}")

def notify_wemos_slow():
    try:
        requests.get(f"{WEMOS_IP}/slow", timeout=2)
        log_event("INFO", "WEMOS_SLOW")
    except Exception as e:
        log_event("ALERT", f"WEMOS_ERROR_SLOW: {e}")

# ---------------- EMAIL + ALARM -----------------
def send_email_alert(video_path, zone, side):
    subject = f"Intrusion Alert - Zone {zone} ({side})"
    body = f"Motion detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nZone: {zone}\nEntry Side: {side}"
    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Subject"] = EMAIL_SENDER, EMAIL_RECEIVER, subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with open(video_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(video_path)}")
        msg.attach(part)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        log_event("INFO", "EMAIL_SENT", video_path)
    except Exception as e:
        log_event("ALERT", f"EMAIL_ERROR: {e}")

def activate_alarm():
    if os.path.exists(ALARM_SOUND):
        playsound(ALARM_SOUND)
        log_event("ALERT", "ALARM_TRIGGERED")
    else:
        log_event("ALERT", "ALARM_MISSING")

# ---------------- UTILS: quick fetcher ----------------
def get_last_events(n=10):
    """Return last n events from active DB."""
    with db_lock:
        cur = db_conn.cursor()
        cur.execute("SELECT timestamp, level, event_type, camera_id, clip_filename FROM events ORDER BY id DESC LIMIT ?", (n,))
        rows = cur.fetchall()
    print(f"--- Last {n} events ---")
    for r in rows:
        ts, level, event_type, cam, clip = r
        print(f"[{ts}] {level} | {event_type} | Camera: {cam} | Clip: {clip or 'N/A'}")
    print("------------------------")
    return rows

# ---------------- MAIN LOOP ---------------------
# Log camera start
cap = cv2.VideoCapture(3)
cap.set(3, FRAME_WIDTH)
cap.set(4, FRAME_HEIGHT)
if not cap.isOpened():
    log_event("CRITICAL", "CAMERA_OPEN_FAILED")
    raise SystemExit
else:
    log_event("INFO", "CAMERA_STARTED")

# start reading initial frames
ret, frame1 = cap.read()
ret2, frame2 = cap.read()
if not ret or not ret2:
    log_event("CRITICAL", "CAMERA_READ_FAILED")
    cap.release()
    raise SystemExit

try:
    while cap.isOpened():
        diff = cv2.absdiff(frame1, frame2)
        gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, thresh = cv2.threshold(blur, 20, 255, cv2.THRESH_BINARY)
        dilated = cv2.dilate(thresh, None, iterations=3)
        contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        overlay_frame = frame1.copy()
        draw_zones(overlay_frame)

        detected = False
        highest_zone_hit, zone_side, largest_area, chosen_box = 0, "Unknown", 0, None

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 5000:
                continue
            (x, y, w, h) = cv2.boundingRect(contour)
            cx, cy = x+w//2, y+h//2
            if point_in_rect(cx, cy, ROI_X, ROI_Y, ROI_W, ROI_H):
                detected = True
                zone = get_zone_for_point(cx, cy)
                if area > largest_area:
                    largest_area = area
                    highest_zone_hit, zone_side, chosen_box = zone, get_side_relative_to_roi(cx, cy), (x,y,w,h)

        if chosen_box:
            x,y,w,h = chosen_box
            color = (0,255,0) if highest_zone_hit==1 else (0,255,255) if highest_zone_hit==2 else (0,0,255)
            cv2.rectangle(overlay_frame, (x,y), (x+w,y+h), color, 2)
            annotate_info = f"ZONE {highest_zone_hit} | {zone_side}"
            cv2.putText(overlay_frame, annotate_info, (10,25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50,255,50), 2)

        now = time.time()
        if detected:
            last_detection_time = now
            if highest_zone_hit == 2 and now - last_record_zone2_time > RECORD_COOLDOWN:
                notify_wemos_fast()
                # enqueue recording start (frame copy to avoid race)
                record_queue.put((frame1.copy(), f"ARMED Z2 | {zone_side}", True))
                log_event("ALERT", "ZONE2_INTRUSION")
                last_record_zone2_time = now
            elif highest_zone_hit == 3 and now - last_email_time > EMAIL_COOLDOWN:
                notify_wemos_fast()
                activate_alarm()
                # enqueue recording start then later emailing should attach that clip; this code logs the intrusion
                record_queue.put((frame1.copy(), f"ALERT Z3 | {zone_side}", True))
                log_event("CRITICAL", "ZONE3_INTRUSION")
                last_email_time = now
            armed, motion_detected = True, True
        elif motion_detected and now - last_detection_time > MOTION_TIMEOUT:
            motion_detected, armed = False, False
            notify_wemos_slow()
            log_event("INFO", "STATE_IDLE")

        # Two windows: Live and Detection overlay
        cv2.imshow("Live Feed", frame1)
        cv2.imshow("Detection Overlay", overlay_frame)

        # read next frames
        frame1, ret, frame2 = frame2, *cap.read()
        if not ret or cv2.waitKey(10) == ord('q'):
            log_event("INFO", "USER_REQUEST_EXIT")
            break

except Exception as e:
    log_event("CRITICAL", f"MAIN_LOOP_ERROR: {e}")

finally:
    # Shutdown sequence
    try:
        cap.release()
        cv2.destroyAllWindows()
        log_event("INFO", "CAMERA_STOPPED")
    except Exception as e:
        log_event("ALERT", f"CAMERA_CLOSE_ERROR: {e}")

    # Stop recording thread
    record_queue.put(None)
    record_thread.join(timeout=5)

    # Close DB
    try:
        db_conn.close()
    except Exception as e:
        print(f"Failed to close DB connection: {e}")

    # Optionally print last 10 events at exit for quick debugging
    get_last_events(10)
