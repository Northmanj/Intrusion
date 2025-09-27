from flask import Flask, render_template, Response, redirect, url_for, send_from_directory
import threading, os
from detector import IntrusionDetector

app = Flask(__name__)

detector = IntrusionDetector()

@app.route("/")
def dashboard():
    return render_template("dashboard.html", status=detector.status)

@app.route("/start")
def start_system():
    if not detector.running:
        threading.Thread(target=detector.start, daemon=True).start()
    return redirect(url_for("dashboard"))

@app.route("/stop")
def stop_system():
    detector.stop()
    return redirect(url_for("dashboard"))

@app.route("/video_feed")
def video_feed():
    return Response(detector.generate_frames(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/logs")
def logs():
    logs = []
    if os.path.exists("intrusion_log.txt"):
        with open("intrusion_log.txt") as f:
            logs = f.readlines()
    return render_template("logs.html", logs=logs)

@app.route("/clips")
def list_clips():
    clips = os.listdir("static/clips")
    clips = [c for c in clips if c.endswith(".avi")]
    return render_template("clips.html", clips=clips)

@app.route("/clips/<filename>")
def download_clip(filename):
    return send_from_directory("static/clips", filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

