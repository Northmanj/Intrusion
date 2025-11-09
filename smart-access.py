# smart_access.py
import os
import cv2
import time
import numpy as np
import serial
from datetime import datetime
from playsound import playsound
import ssl, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.mime.text import MIMEText
import json

# ---------------- CONFIG ----------------
SERIAL_PORT = 'COM3'         # change to your serial port
BAUDRATE = 9600
VIDEO_DEVICE = 0             # camera index
DATASET_DIR = 'dataset'
TRAINER_FILE = 'trainer.yml'
LABELS_FILE = 'labels.json'
CASCADE_FILE = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
FRAME_W, FRAME_H = 640, 480
VIDEO_DURATION = 15          # seconds to record after event
ALARM_FILE = 'alarm.wav'

# recognition threshold: LBPH lower = more confident
RECOG_THRESHOLD = 70        # tune this: lower = stricter match

# email settings - replace with safe method in production
EMAIL_SENDER = "gregory@gmail.com"
EMAIL_PASSWORD = "YOUR_APP_PASSWORD"  # use app password!
EMAIL_RECEIVER = "gregory@gmail.com"

ALERT_COOLDOWN = 20  # seconds between alerts to avoid spam
# ----------------------------------------

def open_serial():
    try:
        ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
        time.sleep(2)
        print("[SERIAL] Opened", SERIAL_PORT)
        return ser
    except Exception as e:
        print("[SERIAL ERROR]", e)
        return None

def send_wemos(ser, cmd):
    if ser and ser.isOpen():
        ser.write((cmd + '\n').encode())
        print("[SERIAL TX]", cmd)

def send_email_with_attachment(subject, body, filepath):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    try:
        with open(filepath, 'rb') as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(filepath)}')
        msg.attach(part)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print("[EMAIL] Sent:", filepath)
    except Exception as e:
        print("[EMAIL ERROR]", e)

def record_clip(duration=VIDEO_DURATION):
    cap = cv2.VideoCapture(VIDEO_DEVICE)
    cap.set(3, FRAME_W)
    cap.set(4, FRAME_H)
    os.makedirs('clips', exist_ok=True)
    fname = os.path.join('clips', f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}.avi")
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(fname, fourcc, 20.0, (FRAME_W, FRAME_H))
    start = time.time()
    while time.time() - start < duration:
        ret, frame = cap.read()
        if not ret:
            break
        out.write(frame)
    out.release()
    cap.release()
    print("[VIDEO] Saved:", fname)
    return fname

# -------------- dataset collect --------------
def collect_images(label):
    os.makedirs(os.path.join(DATASET_DIR, label), exist_ok=True)
    cam = cv2.VideoCapture(VIDEO_DEVICE)
    cam.set(3, FRAME_W)
    cam.set(4, FRAME_H)
    detector = cv2.CascadeClassifier(CASCADE_FILE)
    count = len(os.listdir(os.path.join(DATASET_DIR, label)))
    print("[COLLECT] Press SPACE to capture image, ESC to exit.")
    while True:
        ret, frame = cam.read()
        if not ret:
            print("[COLLECT] Camera error.")
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5)
        for (x,y,w,h) in faces:
            cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)
        cv2.imshow(f"Collect - {label}", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            break
        if key == 32:  # space
            if len(faces) == 0:
                print("[COLLECT] No face detected.")
                continue
            faces_sorted = sorted(faces, key=lambda r: r[2]*r[3], reverse=True)
            x,y,w,h = faces_sorted[0]
            face = gray[y:y+h, x:x+w]
            face = cv2.resize(face, (200,200))
            fname = os.path.join(DATASET_DIR, label, f"{label}_{count:03d}.jpg")
            cv2.imwrite(fname, face)
            print("[COLLECT] Saved", fname)
            count += 1
    cam.release()
    cv2.destroyAllWindows()

# -------------- train model --------------
def train_model():
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    detector = cv2.CascadeClassifier(CASCADE_FILE)
    faces = []
    ids = []
    label_map = {}
    idx = 0
    for label in sorted(os.listdir(DATASET_DIR)):
        labdir = os.path.join(DATASET_DIR, label)
        if not os.path.isdir(labdir):
            continue
        label_map[idx] = label
        for f in os.listdir(labdir):
            path = os.path.join(labdir, f)
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            faces.append(img)
            ids.append(idx)
        idx += 1
    if len(faces) == 0:
        print("[TRAIN] No data to train.")
        return
    recognizer.train(faces, np.array(ids))
    recognizer.write(TRAINER_FILE)
    with open(LABELS_FILE, 'w') as fh:
        json.dump(label_map, fh)
    print("[TRAIN] Done. Model saved to", TRAINER_FILE)

# -------------- runtime --------------
def run_mode():
    ser = open_serial()
    cap = cv2.VideoCapture(VIDEO_DEVICE)
    cap.set(3, FRAME_W)
    cap.set(4, FRAME_H)
    cascade = cv2.CascadeClassifier(CASCADE_FILE)

    # load model
    if not os.path.exists(TRAINER_FILE) or not os.path.exists(LABELS_FILE):
        print("[RUN] Train model first.")
        return
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(TRAINER_FILE)
    with open(LABELS_FILE, 'r') as fh:
        label_map = json.load(fh)
    inv_label_map = {int(k): v for k, v in label_map.items()}

    last_alert = 0
    print("[RUN] Waiting for MOTION from Wemos...")

    while True:
        try:
            # read serial
            if ser and ser.in_waiting:
                line = ser.readline().decode(errors='ignore').strip()
                if not line:
                    pass
                else:
                    print("[SERIAL RX]", line)
                if line == "MOTION":
                    print("[RUN] Motion received.")
                    # capture a few frames for recognition
                    frames = []
                    for i in range(5):
                        ret, frame = cap.read()
                        if not ret: break
                        frames.append(frame)
                        time.sleep(0.05)
                    if len(frames) == 0:
                        print("[RUN] No frames.")
                        continue
                    frame = frames[-1]
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5)
                    if len(faces) == 0:
                        print("[RUN] No faces found - ignoring (or record if you prefer).")
                        # optionally record unknown event here
                        continue

                    authorized_present = False
                    unauthorized_present = False
                    face_details = []

                    for (x,y,w,h) in faces:
                        roi = gray[y:y+h, x:x+w]
                        roi_resized = cv2.resize(roi, (200,200))
                        label_id, conf = recognizer.predict(roi_resized)
                        label_name = inv_label_map.get(label_id, "unknown")
                        face_details.append((label_name, conf))
                        print(f"[RECOG] {label_name} conf={conf:.2f}")
                        if conf < RECOG_THRESHOLD:
                            authorized_present = True
                        else:
                            unauthorized_present = True

                    # Decision policy:
                    # If any authorized face present and no sole unauthorized -> treat as authorized event
                    # If unauthorized present and not accompanied by any authorized -> intruder
                    # If both present: treat as authorized (person accompanied by authorized) — change policy as you wish.
                    if authorized_present and not (unauthorized_present and not authorized_present):
                        # AUTHORIZED CASE
                        print("[ACTION] Authorized person(s) detected -> OPEN")
                        send_wemos(ser, "open")
                        # record short clip & email
                        clip = record_clip(duration=10)
                        subj = "Authorized Access - " + datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        body = f"Authorized access by: {', '.join([f'{n}({c:.1f})' for n,c in face_details])}"
                        send_email_with_attachment(subj, body, clip)
                        # after event, optionally close again after a short delay
                        time.sleep(5)
                        send_wemos(ser, "close")

                    elif unauthorized_present and not authorized_present:
                        # INTRUDER CASE
                        # avoid spamming
                        if time.time() - last_alert < ALERT_COOLDOWN:
                            print("[ACTION] Intruder detected but on cooldown.")
                            continue
                        last_alert = time.time()
                        print("[ACTION] Intruder detected -> CLOSE, ALARM, RECORD, EMAIL")
                        send_wemos(ser, "close")
                        # play alarm non-blocking (playsound may be blocking depending on implementation)
                        if os.path.exists(ALARM_FILE):
                            try:
                                playsound(ALARM_FILE, block=False)
                            except Exception as e:
                                print("[ALARM ERROR]", e)
                        clip = record_clip(duration=VIDEO_DURATION)
                        subj = "Intruder Alert - " + datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        body = f"Intruder detected. Faces: {face_details}"
                        send_email_with_attachment(subj, body, clip)
                        # optionally ensure lock remains closed
                        send_wemos(ser, "close")
                    else:
                        # fallback
                        print("[ACTION] No decisive classification, no action.")
            # check keyboard to break
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("[RUN ERROR]", e)
            time.sleep(0.5)

    cap.release()
    if ser:
        ser.close()
    cv2.destroyAllWindows()


# --------------- CLI ---------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python smart_access.py collect <label>")
        print("  python smart_access.py train")
        print("  python smart_access.py run")
        sys.exit(0)

    mode = sys.argv[1].lower()
    if mode == "collect":
        if len(sys.argv) < 3:
            print("Provide label name")
            sys.exit(1)
        collect_images(sys.argv[2])
    elif mode == "train":
        train_model()
    elif mode == "run":
        run_mode()
    else:
        print("Unknown mode.")
