from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
import os
import face_recognition
from datetime import datetime

app = Flask(__name__)
app.secret_key = "supersecret"
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ---------- DATABASE ----------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    image_path = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# ---------- ROUTES ----------
@app.route('/')
def index():
    users = User.query.all()
    return render_template('index.html', users=users)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        name = request.form['name']
        file = request.files['file']
        if not file or not name:
            flash("Please provide a name and an image.", "danger")
            return redirect(url_for('upload'))
        path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(path)
        db.session.add(User(name=name, image_path=path))
        db.session.commit()
        flash(f"User '{name}' added successfully!", "success")
        return redirect(url_for('index'))
    return render_template('upload.html')

@app.route('/delete/<int:id>')
def delete_user(id):
    user = User.query.get(id)
    if user:
        if os.path.exists(user.image_path):
            os.remove(user.image_path)
        db.session.delete(user)
        db.session.commit()
        flash(f"Deleted {user.name}", "warning")
    return redirect(url_for('index'))

# ---------- API ENDPOINT ----------
@app.route('/api/verify', methods=['POST'])
def verify_face():
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No image file provided'})
    
    file = request.files['file']
    temp_path = os.path.join(app.config['UPLOAD_FOLDER'], 'temp.jpg')
    file.save(temp_path)

    try:
        unknown_image = face_recognition.load_image_file(temp_path)
        unknown_encoding = face_recognition.face_encodings(unknown_image)[0]
    except:
        return jsonify({'status': 'unauthorized', 'reason': 'no_face_detected'})

    users = User.query.all()
    for user in users:
        try:
            known_image = face_recognition.load_image_file(user.image_path)
            known_encoding = face_recognition.face_encodings(known_image)[0]
            results = face_recognition.compare_faces([known_encoding], unknown_encoding, tolerance=0.45)
            if True in results:
                return jsonify({'status': 'authorized', 'user': user.name})
        except:
            continue

    return jsonify({'status': 'unauthorized', 'reason': 'unknown_person'})

if __name__ == '__main__':
    os.makedirs('static/uploads', exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=True)
