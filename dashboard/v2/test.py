import cv2
import time
import os
import threading
import logging
import requests
import sqlite3
from collections import deque
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from playsound import playsound
import smtplib

# ---------------- CONFIG ----------------
CAMERA_INDEX = 0
WEMOS_IP = "http://192.168.10.33"

# Email (fill these)

EMAIL_SENDER = "johngregory6400@gmail.com"
EMAIL_PASSWORD = "wkzfpxbsjbuycpxo"
EMAIL_RECEIVER = "johngregory6400@gmail.com"

# Upload endpoint (optional) - server that accepts file uploads
UPLOAD_URL = "http://192.168.1.100:5000/upload"  # set to None to disable upload

ALARM_SOUND = "alarm.wav"
LOG_FILE = "intrusion_log.txt"
DB_FILE = "intrusion.db"

CLIP_DIR = "clips"
SNAP_DIR = "snapshots"
os.makedirs(CLIP_DIR, exist_ok=True)
os.makedirs(SNAP_DIR, exist_ok=True)

# Buffer/clip settings
FPS = 20                      # frames per second (adjust to your camera)
PRE_SECONDS = 15              # seconds before event to include
POST_SECONDS = 15             # seconds after event to include
PRE_FRAMES = PRE_SECONDS * FPS
POST_FRAMES = POST_SECONDS * FPS
MAX_PREBUFFER = PRE_FRAMES

# motion thresholds
MIN_CONTOUR_AREA = 500

# logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s",
                    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])

# ---------------- DB helpers ----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            zone INTEGER,
            duration REAL,
            snapshot_path TEXT,
            clip_path TEXT,
            uploaded INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def save_event_to_db(zone, duration, snapshot_path, clip_path, uploaded=0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO events (ts, zone, duration, snapshot_path, clip_path, uploaded) VALUES (?, ?, ?, ?, ?, ?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), zone, duration, snapshot_path, clip_path, uploaded))
    conn.commit()
    conn.close()

# ---------------- utilities ----------------
def send_email(subject, body, snapshot_path=None, clip_path=None):
    def _send():
        try:
            msg = MIMEMultipart()
            msg["From"] = EMAIL_SENDER
            msg["To"] = EMAIL_RECEIVER
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            if snapshot_path and os.path.exists(snapshot_path):
                with open(snapshot_path, "rb") as f:
                    img = MIMEImage(f.read())
                    img.add_header("Content-Disposition", "attachment", filename=os.path.basename(snapshot_path))
                    msg.attach(img)

            if clip_path and os.path.exists(clip_path):
                with open(clip_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(clip_path)}")
                    msg.attach(part)

            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
            server.quit()
            logging.info("[EMAIL] Sent: %s", subject)
        except Exception as e:
            logging.error("[EMAIL ERROR] %s", e)
    threading.Thread(target=_send, daemon=True).start()

def activate_alarm():
    if os.path.exists(ALARM_SOUND):
        threading.Thread(target=playsound, args=(ALARM_SOUND,), daemon=True).start()

def notify_wemos(path):
    try:
        requests.get(f"{WEMOS_IP}/{path}", timeout=2)
        logging.info("[WEMOS] %s", path)
    except Exception as e:
        logging.error("[WEMOS ERROR] %s", e)

def save_clip(frames, path):
    if not frames:
        logging.warning("[CLIP] No frames to save")
        return False
    h, w, _ = frames[0].shape
    fourcc = cv2.VideoWriter_fourcc(*"XVID")
    out = cv2.VideoWriter(path, fourcc, FPS, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    logging.info("[CLIP] Saved %s (%d frames)", path, len(frames))
    return True

def upload_clip(filepath):
    if not UPLOAD_URL:
        return False, "Upload disabled"
    try:
        files = {"file": (os.path.basename(filepath), open(filepath,"rb"), "video/avi")}
        resp = requests.post(UPLOAD_URL, files=files, timeout=30)
        logging.info("[UPLOAD] status %s", resp.status_code)
        if resp.status_code == 200:
            return True, resp.text
        else:
            return False, f"Status {resp.status_code}"
    except Exception as e:
        logging.error("[UPLOAD ERROR] %s", e)
        return False, str(e)

# ---------------- main detection logic ----------------
init_db()
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    logging.critical("Camera not available. Exiting.")
    raise SystemExit

# rolling buffer for pre-event frames
pre_buffer = deque(maxlen=MAX_PREBUFFER)

intruder_in_zone3 = False
intruder_start_time = None
clip_worker_lock = threading.Lock()
last_snapshot_time = 0

def clip_worker(pre_frames, post_collect_seconds, zone, snapshot_path):
    """
    Combine pre_frames + post_frames recorded from camera in real time (post_collect_seconds),
    save clip, upload, email and add DB record.
    """
    try:
        logging.info("[WORKER] Starting clip worker for zone %s", zone)
        post_frames = []
        collect_count = int(post_collect_seconds * FPS)
        # collect post frames by reading from the live camera stream
        for i in range(collect_count):
            ret, frame = cap.read()
            if not ret:
                logging.error("[WORKER] failed to read post-frame")
                break
            post_frames.append(frame.copy())
            time.sleep(1.0 / FPS)  # pace to approximate real fps

        frames = list(pre_frames) + post_frames
        timestamp = int(time.time())
        clip_path = os.path.join(CLIP_DIR, f"intrusion_{timestamp}.avi")
        success = save_clip(frames, clip_path)
        if not success:
            logging.error("[WORKER] failed to write clip")
            return

        # optionally upload
        uploaded = 0
        upload_msg = ""
        ok, upl_resp = upload_clip(clip_path)
        if ok:
            uploaded = 1
            upload_msg = upl_resp

        # save DB record
        duration = len(frames) / FPS
        save_event_to_db(zone=zone, duration=duration, snapshot_path=snapshot_path, clip_path=clip_path, uploaded=uploaded)

        # send follow-up email with clip (background)
        if duration >= 5:  # only send large clips if reasonably long to avoid spam
            subj = f"Intrusion Video Evidence - Zone {zone}"
            body = f"Attached is the {duration:.1f}s clip around the intrusion in Zone {zone}."
            send_email(subj, body, snapshot_path=snapshot_path, clip_path=clip_path)

        logging.info("[WORKER] Clip worker finished. uploaded=%s resp=%s", uploaded, upload_msg)
    except Exception as e:
        logging.error("[WORKER ERROR] %s", e)

# ROI config (example - adapt to your feed)
ROI_X, ROI_Y, ROI_W, ROI_H = 50, 50, 500, 400
MARGIN_ZONE2 = 0.15
MARGIN_ZONE3 = 0.35

def rect_from_margin(x, y, w, h, margin_ratio):
    mx = int(w * margin_ratio)
    my = int(h * margin_ratio)
    return (x + mx, y + my, w - 2 * mx, h - 2 * my)

def point_in_rect(px, py, rx, ry, rw, rh):
    return (px >= rx) and (py >= ry) and (px <= rx + rw) and (py <= ry + rh)

def get_zone_for_point(cx, cy):
    z2 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE2)
    z3 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE3)
    if point_in_rect(cx, cy, *z3):
        return 3
    elif point_in_rect(cx, cy, *z2):
        return 2
    elif point_in_rect(cx, cy, ROI_X, ROI_Y, ROI_W, ROI_H):
        return 1
    else:
        return 0

# main loop
fgbg = cv2.createBackgroundSubtractorMOG2()
while True:
    ret, frame = cap.read()
    if not ret:
        logging.error("Frame read failed in main loop.")
        break

    # push frame to pre buffer
    pre_buffer.append(frame.copy())

    # crop to ROI for processing
    roi = frame[ROI_Y:ROI_Y+ROI_H, ROI_X:ROI_X+ROI_W]
    fgmask = fgbg.apply(roi)
    contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    detected = False
    highest_zone = 0
    largest_area = 0
    chosen_box = None

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_CONTOUR_AREA:
            continue
        x,y,w,h = cv2.boundingRect(cnt)
        cx, cy = x + w//2, y + h//2
        if point_in_rect(cx, cy, 0, 0, ROI_W, ROI_H):
            zone = get_zone_for_point(cx + ROI_X, cy + ROI_Y)  # convert local ROI coords to frame coords
            if area > largest_area:
                largest_area = area
                highest_zone = zone
                chosen_box = (x, y, w, h)
            detected = True

    now = time.time()

    # Draw ROI and chosen box for debugging
    cv2.rectangle(frame, (ROI_X, ROI_Y), (ROI_X+ROI_W, ROI_Y+ROI_H), (255,255,255), 2)
    if chosen_box:
        x,y,w,h = chosen_box
        cv2.rectangle(frame, (ROI_X+x, ROI_Y+y), (ROI_X+x+w, ROI_Y+y+h), (0,0,255), 2)
        cv2.putText(frame, f"Zone {highest_zone}", (ROI_X+x, ROI_Y+y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

    # Zone logic: if Zone 3 triggered, create snapshot, start clip_worker (pre+post)
    if detected and highest_zone == 3:
        # immediate actions (non-blocking)
        notify_wemos("fast")
        activate_alarm()

        # snapshot
        if time.time() - last_snapshot_time > 2:  # throttle snapshots a bit
            snap_path = os.path.join(SNAP_DIR, f"snapshot_{int(time.time())}.jpg")
            cv2.imwrite(snap_path, frame)
            logging.info("[SNAP] Saved snapshot %s", snap_path)
            # send immediate snapshot email
            send_email(f"Intrusion Snapshot - Zone 3", "Immediate snapshot of intrusion in Zone 3", snapshot_path=snap_path)
            last_snapshot_time = time.time()
        else:
            snap_path = None

        # launch clip worker thread to collect post frames and combine with pre_buffer
        # copy pre-buffer contents safely
        with clip_worker_lock:
            pre_frames_copy = list(pre_buffer)  # will contain up to PRE_FRAMES
        t = threading.Thread(target=clip_worker, args=(pre_frames_copy, POST_SECONDS, 3, snap_path), daemon=True)
        t.start()

        # optionally: mark intruder state or cooldown to avoid repeated workers; for simplicity we sleep a short bit
        time.sleep(0.2)

    # show frame
    cv2.imshow("Intrusion", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# cleanup
cap.release()
cv2.destroyAllWindows()
