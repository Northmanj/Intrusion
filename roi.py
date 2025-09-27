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
import serial

# ==========================
# Email settings
# ==========================
EMAIL_SENDER = "gregory@gmail.com"
EMAIL_PASSWORD = "wkzfpxb"
EMAIL_RECEIVER = "gregory@gmail.com"

# ==========================
# Alarm & ROI settings
# ==========================
ALARM_SOUND = "alarm.wav"
ROI_X, ROI_Y, ROI_W, ROI_H = 50, 50, 500, 400

# ==========================
# Video recording settings
# ==========================
VIDEO_DURATION = 30  # seconds
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

# ==========================
# Wemos serial settings
# ==========================
try:
    wemos = serial.Serial('COM3', 9600, timeout=1)  # Windows
    # wemos = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)  # Linux
    time.sleep(2)  # allow Wemos to reset
    print("[WEMOS] Serial connected")
except Exception as e:
    print(f"[WEMOS ERROR] {e}")
    wemos = None

# ==========================
# Functions
# ==========================
def send_email_alert(video_path):
    subject = "Motion Detected Alert!"
    body = f"Motion detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nVideo clip attached."
    msg = MIMEMultipart()
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
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
        print(f"[EMAIL ERROR] {e}")

def activate_alarm():
    if os.path.exists(ALARM_SOUND):
        print("[ALARM] Playing alarm sound...")
        playsound(ALARM_SOUND)
    else:
        print("[ALARM] Alarm sound file not found!")

def record_video():
    if not os.path.exists("clips"):
        os.makedirs("clips")
    filename = os.path.join("clips", f"motion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.avi")
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

def send_wemos_command(command):
    if wemos:
        wemos.write(f"{command}\n".encode())
        print(f"[WEMOS] Sent command: {command}")

# ==========================
# Initialize webcam
# ==========================
cap = cv2.VideoCapture(2)
cap.set(3, FRAME_WIDTH)
cap.set(4, FRAME_HEIGHT)
ret, frame1 = cap.read()
ret, frame2 = cap.read()

motion_detected = False
last_detection_time = 0

# ==========================
# Main loop
# ==========================
while cap.isOpened():
    diff = cv2.absdiff(frame1, frame2)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 20, 255, cv2.THRESH_BINARY)
    dilated = cv2.dilate(thresh, None, iterations=3)
    contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    # draw ROI
    cv2.rectangle(frame1, (ROI_X, ROI_Y), (ROI_X + ROI_W, ROI_Y + ROI_H), (255, 0, 0), 2)

    motion_in_frame = False
    for contour in contours:
        if cv2.contourArea(contour) < 5000:
            continue
        (x, y, w, h) = cv2.boundingRect(contour)
        if x > ROI_X and y > ROI_Y and (x + w) < (ROI_X + ROI_W) and (y + h) < (ROI_Y + ROI_H):
            cv2.rectangle(frame1, (x, y), (x + w, y + h), (0, 255, 0), 2)
            motion_in_frame = True

    # Motion detected actions
    if motion_in_frame and (not motion_detected or (time.time() - last_detection_time > 10)):
        last_detection_time = time.time()
        motion_detected = True
        send_wemos_command("fast")      # Wemos LED fast blink
        activate_alarm()
        video_file = record_video()
        send_email_alert(video_file)
    elif not motion_in_frame:
        motion_detected = False
        send_wemos_command("slow")      # Wemos LED slow blink

    cv2.imshow("Motion Detector", frame1)
    frame1 = frame2
    ret, frame2 = cap.read()

    if cv2.waitKey(10) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
