import cv2
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import os
from playsound import playsound
import time

# ========================
# CONFIGURATION
# ========================

# Camera settings
USE_CCTV = True  # Set to False for webcam
CAMERA_URL = "rtsp://user:pass@192.168.1.100:554/Streaming/Channels/101"
WEBCAM_INDEX = 0

# Email settings
EMAIL_SENDER = "gregory@gmail.com"
EMAIL_PASSWORD = "ewjfjksdbfier"
EMAIL_RECEIVER = "gregory@gmail.com"

# Alarm settings
ALARM_SOUND = "alarm.wav"

# ROI Zones (outer → middle → inner)
ZONE1 = (50, 50, 500, 380)   # Motion detection zone
ZONE2 = (150, 100, 300, 280) # Alert + record zone
ZONE3 = (220, 160, 160, 160) # Alarm zone

# Video settings
VIDEO_DURATION = 30  # seconds
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
VIDEO_FOLDER = "motion_clips"

# Motion detection settings
MOTION_MIN_AREA = 5000
MOTION_COOLDOWN = 10  # seconds between triggers

# ========================
# FUNCTIONS
# ========================

def get_camera():
    """Initialize and return camera capture object."""
    if USE_CCTV:
        print("[INFO] Connecting to CCTV stream...")
        cap = cv2.VideoCapture(CAMERA_URL)
    else:
        print("[INFO] Starting webcam...")
        cap = cv2.VideoCapture(WEBCAM_INDEX)

    cap.set(3, FRAME_WIDTH)
    cap.set(4, FRAME_HEIGHT)
    return cap

def send_email_alert(video_path):
    """Send email with video attachment."""
    try:
        subject = "🚨 Motion Detected Alert!"
        body = f"Motion detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nVideo clip attached."

        msg = MIMEMultipart()
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with open(video_path, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(video_path)}")
        msg.attach(part)

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())

        print("[EMAIL] Alert sent with video clip.")
    except Exception as e:
        print(f"[ERROR] Failed to send email: {e}")

def activate_alarm():
    """Play alarm sound."""
    if os.path.exists(ALARM_SOUND):
        print("[ALARM] Playing alarm sound...")
        playsound(ALARM_SOUND)
    else:
        print("[ALARM] Alarm sound file not found!")

def record_video(cap):
    """Record a short video clip and save in folder."""
    if not os.path.exists(VIDEO_FOLDER):
        os.makedirs(VIDEO_FOLDER)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(VIDEO_FOLDER, f"motion_{timestamp}.avi")

    print(f"[VIDEO] Recording clip: {filename}")
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(filename, fourcc, 20.0, (FRAME_WIDTH, FRAME_HEIGHT))

    start_time = time.time()
    while time.time() - start_time < VIDEO_DURATION:
        ret, frame = cap.read()
        if not ret:
            break
        out.write(frame)

    out.release()
    print("[VIDEO] Clip saved.")
    return filename

# ========================
# MAIN PROGRAM
# ========================
cap = get_camera()
time.sleep(2)  # Camera warm-up

ret, frame1 = cap.read()
ret, frame2 = cap.read()

last_detection_time = 0

while True:
    if not ret:
        print("[WARNING] Lost camera connection. Reconnecting...")
        cap.release()
        time.sleep(2)
        cap = get_camera()
        ret, frame1 = cap.read()
        ret, frame2 = cap.read()
        continue

    diff = cv2.absdiff(frame1, frame2)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 20, 255, cv2.THRESH_BINARY)
    dilated = cv2.dilate(thresh, None, iterations=3)
    contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    # Draw zones
    cv2.rectangle(frame1, (ZONE1[0], ZONE1[1]), (ZONE1[0] + ZONE1[2], ZONE1[1] + ZONE1[3]), (255, 0, 0), 2)
    cv2.rectangle(frame1, (ZONE2[0], ZONE2[1]), (ZONE2[0] + ZONE2[2], ZONE2[1] + ZONE2[3]), (0, 255, 255), 2)
    cv2.rectangle(frame1, (ZONE3[0], ZONE3[1]), (ZONE3[0] + ZONE3[2], ZONE3[1] + ZONE3[3]), (0, 0, 255), 2)

    for contour in contours:
        if cv2.contourArea(contour) < MOTION_MIN_AREA:
            continue

        (x, y, w, h) = cv2.boundingRect(contour)

        # Zone 1: Detect motion only
        if ZONE1[0] < x < (ZONE1[0] + ZONE1[2]) and ZONE1[1] < y < (ZONE1[1] + ZONE1[3]):
            print("[ZONE 1] Motion detected.")

        # Zone 2: Record and send email
        if ZONE2[0] < x < (ZONE2[0] + ZONE2[2]) and ZONE2[1] < y < (ZONE2[1] + ZONE2[3]):
            if time.time() - last_detection_time > MOTION_COOLDOWN:
                print("[ZONE 2] Recording + Email alert.")
                last_detection_time = time.time()
                video_path = record_video(cap)
                send_email_alert(video_path)

        # Zone 3: Trigger alarm
        if ZONE3[0] < x < (ZONE3[0] + ZONE3[2]) and ZONE3[1] < y < (ZONE3[1] + ZONE3[3]):
            print("[ZONE 3] Alarm triggered!")
            activate_alarm()

    cv2.imshow("Motion Detector", frame1)
    frame1 = frame2
    ret, frame2 = cap.read()

    if cv2.waitKey(10) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
