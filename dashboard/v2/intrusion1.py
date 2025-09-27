import cv2, time, os, smtplib, threading, logging, requests
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.text import MIMEText
from email import encoders
from playsound import playsound

# ---------------- CONFIG ----------------
CAMERA_INDEX = 0
WEMOS_IP = "http://192.168.10.33"
ALARM_SOUND = "alarm.wav"
LOG_FILE = "intrusion_log.txt"

EMAIL_SENDER = "johngregory6400@gmail.com"
EMAIL_PASSWORD = "wkzfpxbsjbuycpxo"
EMAIL_RECEIVER = "johngregory6400@gmail.com"


CLIP_DIR = "clips"
if not os.path.exists(CLIP_DIR):
    os.makedirs(CLIP_DIR)

# ---------------- LOGGING ----------------
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ---------------- UTILITIES ----------------
def send_email(subject, body, snapshot=None, video=None):
    """Send email with snapshot and optional video."""
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain"))

        if snapshot and os.path.exists(snapshot):
            with open(snapshot, "rb") as f:
                img = MIMEImage(f.read())
                img.add_header("Content-Disposition", "attachment", filename=os.path.basename(snapshot))
                msg.attach(img)

        if video and os.path.exists(video):
            with open(video, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(video)}")
                msg.attach(part)

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        server.quit()

        logging.info(f"Email sent with subject: {subject}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

def activate_alarm():
    if os.path.exists(ALARM_SOUND):
        threading.Thread(target=playsound, args=(ALARM_SOUND,), daemon=True).start()

def record_video(frames, path):
    """Save buffered frames as video."""
    if not frames:
        return
    h, w, _ = frames[0].shape
    fourcc = cv2.VideoWriter_fourcc(*"XVID")
    out = cv2.VideoWriter(path, fourcc, 20.0, (w, h))
    for f in frames:
        out.write(f)
    out.release()
    logging.info(f"Video saved: {path}")

def send_wemos(path):
    try:
        requests.get(f"{WEMOS_IP}/{path}", timeout=2)
    except Exception as e:
        logging.error(f"Wemos request failed: {e}")

# ---------------- MAIN ----------------
cap = cv2.VideoCapture(CAMERA_INDEX)
fgbg = cv2.createBackgroundSubtractorMOG2()

roi_x, roi_y, roi_w, roi_h = 100, 100, 400, 300
zone1 = (roi_x, roi_y, roi_w, roi_h//3)
zone2 = (roi_x, roi_y+roi_h//3, roi_w, roi_h//3)
zone3 = (roi_x, roi_y+2*(roi_h//3), roi_w, roi_h//3)

intruder_in_zone3 = False
intruder_start_time = None
last_snapshot_time = 0

buffer_frames = []
MAX_BUFFER = 600  # ~30 seconds at 20fps

while True:
    ret, frame = cap.read()
    if not ret:
        break

    roi = frame[roi_y:roi_y+roi_h, roi_x:roi_x+roi_w]
    fgmask = fgbg.apply(roi)
    contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    motion_detected = False
    for cnt in contours:
        if cv2.contourArea(cnt) < 500:
            continue
        x,y,w,h = cv2.boundingRect(cnt)
        cv2.rectangle(frame, (roi_x+x, roi_y+y), (roi_x+x+w, roi_y+y+h), (0,0,255), 2)
        motion_detected = True

        # Check zones
        if y+h//2 > 2*(roi_h//3):  # Zone 3
            if not intruder_in_zone3:
                intruder_in_zone3 = True
                intruder_start_time = time.time()
                logging.info("Intruder entered Zone 3")
                send_wemos("fast")
                activate_alarm()

                # Send snapshot immediately
                if time.time() - last_snapshot_time > 5:
                    snap_path = os.path.join(CLIP_DIR, f"snapshot_{int(time.time())}.jpg")
                    cv2.imwrite(snap_path, frame)
                    send_email("Intrusion Alert - Zone 3", "Intruder detected in Zone 3", snapshot=snap_path)
                    last_snapshot_time = time.time()

        elif y+h//2 > roi_h//3:  # Zone 2
            logging.info("Motion in Zone 2")
            send_wemos("fast")

    # Track intruder duration
    if intruder_in_zone3:
        if not motion_detected:  # intruder left
            duration = time.time() - intruder_start_time
            logging.info(f"Intruder stayed {duration:.2f} seconds in Zone 3")
            
            # Save video evidence
            clip_path = os.path.join(CLIP_DIR, f"intrusion_{int(time.time())}.avi")
            threading.Thread(target=record_video, args=(buffer_frames.copy(), clip_path), daemon=True).start()
            
            # Send follow-up email with video if >10s
            if duration > 10:
                send_email("Intrusion Video Evidence", f"Intruder stayed {duration:.1f} seconds in Zone 3", video=clip_path)

            intruder_in_zone3 = False
            intruder_start_time = None
            buffer_frames.clear()

    # Maintain rolling buffer
    buffer_frames.append(frame.copy())
    if len(buffer_frames) > MAX_BUFFER:
        buffer_frames.pop(0)

    # Draw ROI + Zones
    cv2.rectangle(frame, (roi_x, roi_y), (roi_x+roi_w, roi_y+roi_h), (255,255,255), 2)
    cv2.line(frame, (roi_x, roi_y+roi_h//3), (roi_x+roi_w, roi_y+roi_h//3), (0,255,0), 2)
    cv2.line(frame, (roi_x, roi_y+2*(roi_h//3)), (roi_x+roi_w, roi_y+2*(roi_h//3)), (0,255,0), 2)

    cv2.imshow("Intrusion Detection", frame)
    if cv2.waitKey(30) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
