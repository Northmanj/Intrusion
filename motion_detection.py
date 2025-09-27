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
import requests

# ---------------- WEMOS SETTINGS ----------------
WEMOS_IP = "http://192.168.100.200"  # change to your Wemos IP

def notify_wemos_fast():
    try:
        requests.get(f"{WEMOS_IP}/fast", timeout=2)
        print("[WEMOS] Set to FAST blink")
    except Exception as e:
        print(f"[WEMOS ERROR] {e}")

def notify_wemos_slow():
    try:
        requests.get(f"{WEMOS_IP}/slow", timeout=2)
        print("[WEMOS] Set to SLOW blink")
    except Exception as e:
        print(f"[WEMOS ERROR] {e}")

# ---------------- EMAIL SETTINGS ----------------
EMAIL_SENDER = "gregory@gmail.com"
EMAIL_PASSWORD = "wbuycpxo"   # consider env var later
EMAIL_RECEIVER = "gregory@gmail.com"

# ---------------- ALARM SETTINGS ----------------
ALARM_SOUND = "alarm.wav"

# ---------------- ROI SETTINGS ------------------
# Base ROI (outer boundary)
ROI_X, ROI_Y, ROI_W, ROI_H = 50, 50, 500, 400

# Zone margins (as % of ROI size) to create 3 concentric rectangles
MARGIN_ZONE2 = 0.15  # middle zone starts after 15% inset
MARGIN_ZONE3 = 0.35  # inner zone starts after 35% inset

# ---------------- VIDEO SETTINGS ----------------
VIDEO_DURATION = 30  # seconds for recorded clip
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

# ---------------- TIMING / COOLDOWNS ------------
MOTION_TIMEOUT = 30  # keep Wemos fast for 30s after last motion
EMAIL_COOLDOWN = 60  # minimum seconds between emails
RECORD_COOLDOWN = 30 # minimum seconds between auto recordings for zone 2

# ---------------- STATE -------------------------
armed = False                 # entered zone 2
motion_detected = False
last_detection_time = 0
last_email_time = 0
last_record_zone2_time = 0

# ---------------- HELPERS -----------------------
def rect_from_margin(x, y, w, h, margin_ratio):
    mx = int(w * margin_ratio)
    my = int(h * margin_ratio)
    return (x + mx, y + my, w - 2 * mx, h - 2 * my)

def point_in_rect(px, py, rx, ry, rw, rh):
    return (px >= rx) and (py >= ry) and (px <= rx + rw) and (py <= ry + rh)

def get_zone_for_point(cx, cy):
    # Outer (Zone 1) is ROI, Middle (Zone 2) is ROI shrunk by MARGIN_ZONE2,
    # Inner (Zone 3) is ROI shrunk by MARGIN_ZONE3.
    z2 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE2)
    z3 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE3)
    if point_in_rect(cx, cy, *z3):
        return 3
    elif point_in_rect(cx, cy, *z2):
        return 2
    elif point_in_rect(cx, cy, ROI_X, ROI_Y, ROI_W, ROI_H):
        return 1
    else:
        return 0  # outside ROI

def get_side_relative_to_roi(cx, cy):
    # Determine which side of ROI the object is closest to
    # by splitting ROI into thirds
    left_third = ROI_X + ROI_W // 3
    right_third = ROI_X + (2 * ROI_W) // 3
    top_third = ROI_Y + ROI_H // 3
    bottom_third = ROI_Y + (2 * ROI_H) // 3

    if cx < left_third:
        return "Left"
    elif cx > right_third:
        return "Right"
    elif cy < top_third:
        return "Top"
    else:
        return "Bottom"

def draw_zones(frame):
    # Draw ROI and zone rectangles
    cv2.rectangle(frame, (ROI_X, ROI_Y), (ROI_X + ROI_W, ROI_Y + ROI_H), (255, 0, 0), 2)
    z2 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE2)
    z3 = rect_from_margin(ROI_X, ROI_Y, ROI_W, ROI_H, MARGIN_ZONE3)
    cv2.rectangle(frame, (z2[0], z2[1]), (z2[0] + z2[2], z2[1] + z2[3]), (0, 255, 255), 2)
    cv2.rectangle(frame, (z3[0], z3[1]), (z3[0] + z3[2], z3[1] + z3[3]), (0, 0, 255), 2)
    cv2.putText(frame, "Zone1", (ROI_X+5, ROI_Y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,0,0), 1)
    cv2.putText(frame, "Zone2", (z2[0]+5, z2[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,255), 1)
    cv2.putText(frame, "Zone3", (z3[0]+5, z3[1]-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,255), 1)

def annotate_frame(frame, text):
    cv2.putText(frame, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (50, 255, 50), 2)
    cv2.putText(frame, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                (10, FRAME_HEIGHT - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

# ---------------- EMAIL + ALARM + RECORD --------
def send_email_alert(video_path, zone, side):
    subject = f"Intrusion Alert - Zone {zone} ({side})"
    body = (
        f"Motion detected at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Zone: {zone}\n"
        f"Entry Side: {side}\n"
        f"ROI: x={ROI_X}, y={ROI_Y}, w={ROI_W}, h={ROI_H}\n"
        f"Clip: {os.path.basename(video_path)}\n"
    )

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

def record_video(overlay_text=""):
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
        # Burn overlay info into each frame
        annotate_frame(frame, overlay_text)
        out.write(frame)

    out.release()
    print("[VIDEO] Clip saved.")
    return filename

# ---------------- MOTION PIPELINE ----------------
cap = cv2.VideoCapture(2)  # adjust index as needed
cap.set(3, FRAME_WIDTH)
cap.set(4, FRAME_HEIGHT)
ret, frame1 = cap.read()
ret, frame2 = cap.read()

if not ret:
    print("[ERROR] Camera not available.")
    cap.release()
    raise SystemExit

fgbg = cv2.createBackgroundSubtractorMOG2()  # more robust than raw diff

while cap.isOpened():
    # Compute motion mask
    diff = cv2.absdiff(frame1, frame2)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 20, 255, cv2.THRESH_BINARY)
    dilated = cv2.dilate(thresh, None, iterations=3)
    contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    draw_zones(frame1)

    detected = False
    highest_zone_hit = 0
    zone_side = "Unknown"
    largest_area = 0
    chosen_box = None

    # Evaluate contours inside ROI
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 5000:
            continue

        (x, y, w, h) = cv2.boundingRect(contour)
        cx, cy = x + w // 2, y + h // 2

        # Only consider motion whose center is inside ROI (any zone)
        if point_in_rect(cx, cy, ROI_X, ROI_Y, ROI_W, ROI_H):
            detected = True
            zone = get_zone_for_point(cx, cy)

            # Choose the dominant (largest) target this frame
            if area > largest_area:
                largest_area = area
                highest_zone_hit = zone
                zone_side = get_side_relative_to_roi(cx, cy)
                chosen_box = (x, y, w, h)

    # Draw chosen target and annotate current status
    if chosen_box is not None:
        x, y, w, h = chosen_box
        color = (0, 255, 0) if highest_zone_hit == 1 else (0, 255, 255) if highest_zone_hit == 2 else (0, 0, 255)
        cv2.rectangle(frame1, (x, y), (x + w, y + h), color, 2)
        info = f"ZONE {highest_zone_hit} | {zone_side}"
        annotate_frame(frame1, info)

    now = time.time()

    # --- Zone Actions ---
    if detected:
        last_detection_time = now
        if highest_zone_hit == 1:
            # Zone 1: detect only (no actions)
            pass

        elif highest_zone_hit == 2:
            # Zone 2: Arm & record small clip (cooldown to avoid spam), Wemos fast
            if not armed:
                print("[STATE] System ARMED (Zone 2).")
            armed = True
            notify_wemos_fast()

            if now - last_record_zone2_time > RECORD_COOLDOWN:
                overlay = f"ARMED Z2 | {zone_side}"
                _ = record_video(overlay_text=overlay)
                last_record_zone2_time = now

        elif highest_zone_hit == 3:
            # Zone 3: Alarm + record + email (respect cooldown)
            if not armed:
                print("[STATE] Auto-arming due to Zone 3.")
            armed = True
            notify_wemos_fast()
            activate_alarm()

            if now - last_email_time > EMAIL_COOLDOWN:
                overlay = f"ALERT Z3 | {zone_side}"
                video_file = record_video(overlay_text=overlay)
                send_email_alert(video_file, zone=3, side=zone_side)
                last_email_time = now

        motion_detected = True

    # If no current detection but we were previously in motion:
    if not detected and motion_detected:
        # Keep Wemos fast blinking for MOTION_TIMEOUT after last detection
        if now - last_detection_time > MOTION_TIMEOUT:
            motion_detected = False
            armed = False
            notify_wemos_slow()
            print("[STATE] Idle (no motion).")

    # Show live
    cv2.imshow("Motion Detector - 3 Zone", frame1)

    # Advance frames
    frame1 = frame2
    ret, frame2 = cap.read()
    if not ret:
        print("[ERROR] Camera feed lost.")
        break

    if cv2.waitKey(10) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
