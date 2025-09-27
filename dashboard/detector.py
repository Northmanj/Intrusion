import cv2, time, logging, os, threading
from datetime import datetime

class IntrusionDetector:
    def __init__(self, camera_url=0):  
        # 0 = default USB cam, or replace with wireless URL
        self.cap = cv2.VideoCapture(camera_url)
        self.running = False
        self.status = "Idle"
        self.frame = None

        if not os.path.exists("static/clips"):
            os.makedirs("static/clips")

    def start(self):
        self.running = True
        self.status = "Running"
        logging.info("[SYSTEM] Intrusion detection started.")

        ret, frame1 = self.cap.read()
        ret, frame2 = self.cap.read()

        while self.running and self.cap.isOpened():
            diff = cv2.absdiff(frame1, frame2)
            gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            blur = cv2.GaussianBlur(gray, (5, 5), 0)
            _, thresh = cv2.threshold(blur, 20, 255, cv2.THRESH_BINARY)
            dilated = cv2.dilate(thresh, None, iterations=3)
            contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                if cv2.contourArea(contour) < 5000:
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                cv2.rectangle(frame1, (x, y), (x + w, y + h), (0, 0, 255), 2)
                self.status = "Motion Detected"
                logging.info(f"[MOTION] at {datetime.now().strftime('%H:%M:%S')}")
                break

            self.frame = frame1.copy()
            frame1 = frame2
            ret, frame2 = self.cap.read()
            if not ret: break

        self.cap.release()
        self.status = "Stopped"
        logging.info("[SYSTEM] Intrusion detection stopped.")

    def stop(self):
        self.running = False

    def generate_frames(self):
        while True:
            if self.frame is None:
                continue
            _, buffer = cv2.imencode('.jpg', self.frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

