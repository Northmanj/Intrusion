from flask import Flask, render_template, request, redirect, send_file, jsonify
import subprocess, threading, logging, os, requests

app = Flask(__name__)

# ---- PATHS ----
PYTHON_SCRIPT = "intrusion.py"   # your OpenCV script
LOG_FILE = "intrusion_log.txt"
WEMOS_IP = "http://192.168.10.33"

python_process = None

# ---- START PYTHON SCRIPT ----
def run_python_script():
    global python_process
    python_process = subprocess.Popen(["python3", PYTHON_SCRIPT])

@app.route("/")
def dashboard():
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            logs = f.readlines()[-30:]  # show last 30 lines
    return render_template("dashboard.html", logs=logs)

@app.route("/start-python")
def start_python():
    threading.Thread(target=run_python_script, daemon=True).start()
    return redirect("/")

@app.route("/stop-python")
def stop_python():
    global python_process
    if python_process and python_process.poll() is None:
        python_process.terminate()
        try:
            python_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            python_process.kill()
        python_process = None
        return "Python script stopped", 200
    return "No script running", 200


@app.route("/download-logs")
def download_logs():
    return send_file(LOG_FILE, as_attachment=True)

# ---- WEMOS ROUTES ----
@app.route("/led/<state>")
def led_control(state):
    try:
        if state == "on":
            requests.get(f"{WEMOS_IP}/led/on", timeout=3)
        else:
            requests.get(f"{WEMOS_IP}/led/off", timeout=3)
        return redirect("/")
    except Exception as e:
        return str(e), 500

@app.route("/servo/<int:angle>")
def servo_control(angle):
    try:
        requests.get(f"{WEMOS_IP}/servo?angle={angle}", timeout=3)
        return redirect("/")
    except Exception as e:
        return str(e), 500

@app.route("/status")
def status():
    try:
        res = requests.get(f"{WEMOS_IP}/status", timeout=3).json()
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(debug=True, port=5000)

