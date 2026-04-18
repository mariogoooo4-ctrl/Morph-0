
import os
import json
import requests
import google.generativeai as genai
from flask import Flask, request, jsonify, render_template, redirect, session, url_for, flash, send_file
from replit import db
import datetime
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from gtts import gTTS
import base64
import io
import time

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

# Configure Gemini API
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")
genai.configure(api_key=GEMINI_API_KEY)

# Configure Gladia API
GLADIA_API_KEY = os.environ.get("GLADIA_API_KEY")
if not GLADIA_API_KEY:
    raise ValueError("GLADIA_API_KEY environment variable not set.")

# Database structure:
# db["users"] = { "user_id": { "username": "", "password_hash": "", "name": "" } }
# db["fitness_data"] = { "user_id": [ { "timestamp": "", "steps": 0, "calories_burned": 0, "heart_rate": 0, "macros": {} } ] }
# db["chat_history"] = { "user_id": [ { "role": "user", "parts": ["message"] }, { "role": "model", "parts": ["response"] } ] }

def get_user_data(user_id):
    """Retrieve user data from replit.db"""
    users = db.get("users", {})
    return users.get(user_id, {})

def get_user_id_by_username(username):
    """Retrieve user_id by username from replit.db"""
    users = db.get("users", {})
    for uid, user_info in users.items():
        if user_info.get("username") == username:
            return uid
    return None

def save_user_data(user_id, user_data):
    """Save user data to replit.db"""
    users = db.get("users", {})
    users[user_id] = user_data
    db["users"] = users

def get_fitness_data(user_id):
    """Retrieve fitness data for a specific user"""
    fitness_data = db.get("fitness_data", {})
    return fitness_data.get(user_id, [])

def save_fitness_data(user_id, fitness_entry):
    """Save fitness data for a specific user"""
    fitness_data = db.get("fitness_data", {})
    if user_id not in fitness_data:
        fitness_data[user_id] = []
    fitness_data[user_id].append(fitness_entry)
    # Keep only the latest 10 entries for simplicity
    fitness_data[user_id] = fitness_data[user_id][-10:]
    db["fitness_data"] = fitness_data

def get_chat_history(user_id):
    """Retrieve chat history for a specific user"""
    chat_history = db.get("chat_history", {})
    return chat_history.get(user_id, [])

def save_chat_message(user_id, role, message):
    """Save a chat message to the user's history"""
    chat_history = db.get("chat_history", {})
    if user_id not in chat_history:
        chat_history[user_id] = []
    chat_history[user_id].append({"role": role, "parts": [message]})
    db["chat_history"] = chat_history

@app.route("/")
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    user_id = session["user_id"]
    user = get_user_data(user_id)
    return render_template("index.html", user_name=user.get("name", user_id), username=user.get("username"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        name = request.form.get("name", username) # Display name, defaults to username

        users = db.get("users", {})
        for uid, user_info in users.items():
            if user_info["username"] == username:
                flash("Username already exists. Please choose a different one.", "error")
                return render_template("register.html")

        user_id = str(uuid.uuid4()) # Generate a unique user_id
        password_hash = generate_password_hash(password)
        
        users[user_id] = {"username": username, "password_hash": password_hash, "name": name, "webhook_id": str(uuid.uuid4())}
        db["users"] = users
        
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        users = db.get("users", {})
        for user_id, user_info in users.items():
            if user_info["username"] == username and check_password_hash(user_info["password_hash"], password):
                session["user_id"] = user_id
                flash("Logged in successfully!", "success")
                return redirect(url_for("index"))
        
        flash("Invalid username or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

@app.route("/api/user")
def get_current_user():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    user_id = session["user_id"]
    user_data = get_user_data(user_id)
    return jsonify({"user_id": user_id, "name": user_data.get("name", user_id), "username": user_data.get("username"), "webhook_id": user_data.get("webhook_id")})

@app.route("/api/sync/applehealth/<user_id>", methods=["POST"])
def apple_health_webhook(user_id):
    # For simplicity, we'll assume the user_id in the URL is valid for this example
    # In a real app, you might want to add a secret token to the URL or header for authentication

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400

    steps = data.get("steps")
    calories_burned = data.get("calories_burned")
    heart_rate = data.get("heart_rate", 0) 
    macros = data.get("macros", {"protein": 0, "carbs": 0, "fat": 0}) 

    if steps is None or calories_burned is None:
        return jsonify({"error": "Missing fitness data (steps, calories_burned)"}), 400

    timestamp = datetime.datetime.now().isoformat()
    fitness_entry = {
        "timestamp": timestamp,
        "steps": steps,
        "calories_burned": calories_burned,
        "heart_rate": heart_rate,
        "macros": macros
    }

    save_fitness_data(user_id, fitness_entry)
    
    return jsonify({"message": f"Fitness data received and stored for user {user_id}", "data": fitness_entry}), 200

@app.route("/api/sync/stepsapp/<username>", methods=["POST"])
def stepsapp_webhook(username):
    user_id = get_user_id_by_username(username)
    if not user_id:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON data"}), 400

    steps = data.get("steps")
    calories_burned = data.get("calories")

    if steps is None or calories_burned is None:
        return jsonify({"error": "Missing fitness data (steps, calories)"}), 400

    timestamp = datetime.datetime.now().isoformat()
    fitness_entry = {
        "timestamp": timestamp,
        "steps": steps,
        "calories_burned": calories_burned,
        "heart_rate": 0, 
        "macros": {"protein": 0, "carbs": 0, "fat": 0} 
    }

    save_fitness_data(user_id, fitness_entry)
    
    return jsonify({"message": f"Fitness data received and stored for user {username}", "data": fitness_entry}), 200

@app.route("/api/fitness_stats", methods=["GET"])
def get_fitness_stats():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    user_id = session["user_id"]
    user_data = get_fitness_data(user_id)
    
    if user_data:
        latest_data = user_data[-1]
        return jsonify(latest_data), 200
    
    return jsonify({"message": "No fitness data available for this user"}), 404

@app.route("/api/transcribe", methods=["POST"])
def transcribe_audio():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    if audio_file.filename == "::::":
        return jsonify({"error": "No selected file"}), 400

    headers = {
        "x-gladia-key": GLADIA_API_KEY,
        "Content-Type": "audio/wav"
    }
    
    # Gladia expects a file-like object or bytes
    audio_bytes = audio_file.read()

    try:
        response = requests.post("https://api.gladia.io/v2/upload", headers=headers, data=audio_bytes)
        response.raise_for_status()
        transcription_result = response.json()
        
        # Gladia is asynchronous, so we need to poll for the result
        job_id = transcription_result.get("id")
        if not job_id:
            return jsonify({"error": "Gladia job ID not found"}), 500

        status_url = f"https://api.gladia.io/v2/status/{job_id}"
        while True:
            status_response = requests.get(status_url, headers={"x-gladia-key": GLADIA_API_KEY})
            status_response.raise_for_status()
            status_data = status_response.json()
            if status_data.get("status") == "done":
                return jsonify({"transcription": status_data.get("result", "").strip()})
            elif status_data.get("status") == "failed":
                return jsonify({"error": "Gladia transcription failed"}), 500
            time.sleep(2) # Poll every 2 seconds

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Gladia API error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route("/api/text_to_speech", methods=["POST"])
def text_to_speech():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    text = request.json.get("text")
    lang = request.json.get("lang", "bg") # Default to Bulgarian

    if not text:
        return jsonify({"error": "No text provided"}), 400

    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        audio_buffer = io.BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        audio_base64 = base64.b64encode(audio_buffer.read()).decode("utf-8")
        return jsonify({"audio": audio_base64})
    except Exception as e:
        return jsonify({"error": f"gTTS error: {str(e)}"}), 500

@app.route("/api/upload_image", methods=["POST"])
def upload_image():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401

    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    image_file = request.files["image"]
    if image_file.filename == "::::":
        return jsonify({"error": "No selected file"}), 400

    user_id = session["user_id"]
    user_data = get_user_data(user_id)
    user_name = user_data.get("name", user_id)

    try:
        image_bytes = image_file.read()
        image_part = {
            "mime_type": image_file.content_type,
            "data": image_bytes
        }

        vision_model = genai.GenerativeModel("gemini-3-flash-preview")
        prompt = (
            f"Analyze this image for {user_name}\'s fitness data. "
            "If it's a food item, estimate calories, protein, carbs, and fat. "
            "If it's a workout summary, extract steps, calories burned, and heart rate. "
            "Provide the information in a structured JSON format like: "
            "{\"type\": \"food\"/\"workout\", \"steps\": N, \"calories_burned\": N, \"heart_rate\": N, \"macros\": {\"protein\": N, \"carbs\": N, \"fat\": N}, \"summary\": \"text summary\"}. "
            "If no relevant data is found, return {\"type\": \"none\", \"summary\": \"No fitness data found.\"}. "
            "Respond in Bulgarian if the user's last message was in Bulgarian, otherwise in English."
        )
        
        response = vision_model.generate_content([prompt, image_part])
        analysis_text = response.text.strip()
        
        try:
            analysis_json = json.loads(analysis_text)
            if analysis_json.get("type") in ["food", "workout"]:
                latest_fitness_data = get_fitness_data(user_id)
                current_entry = latest_fitness_data[-1] if latest_fitness_data else {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "steps": 0, "calories_burned": 0, "heart_rate": 0, "macros": {"protein": 0, "carbs": 0, "fat": 0}
                }

                current_entry["steps"] += analysis_json.get("steps", 0)
                current_entry["calories_burned"] += analysis_json.get("calories_burned", 0)
                current_entry["heart_rate"] = max(current_entry["heart_rate"], analysis_json.get("heart_rate", 0))
                current_entry["macros"]["protein"] += analysis_json.get("macros", {}).get("protein", 0)
                current_entry["macros"]["carbs"] += analysis_json.get("macros", {}).get("carbs", 0)
                current_entry["macros"]["fat"] += analysis_json.get("macros", {}).get("fat", 0)
                
                save_fitness_data(user_id, current_entry)
                return jsonify({"analysis": analysis_json.get("summary", "Image analyzed and stats updated."), "stats_updated": True})
            else:
                return jsonify({"analysis": analysis_json.get("summary", "Image analyzed, but no fitness data found."), "stats_updated": False})
        except json.JSONDecodeError:
            return jsonify({"analysis": analysis_text, "stats_updated": False, "error": "Gemini did not return valid JSON."})

    except Exception as e:
        return jsonify({"error": f"Gemini Vision API error: {str(e)}"}), 500

@app.route("/chat", methods=["POST"])
def chat():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    
    user_id = session["user_id"]
    user_message = request.json.get("message")
    
    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    
    user_data = get_user_data(user_id)
    user_name = user_data.get("name", user_id)
    
    # Load chat history
    history = get_chat_history(user_id)

    # Fetch latest fitness data for the user
    latest_fitness_data = get_fitness_data(user_id)
    fitness_context = ""
    
    if latest_fitness_data:
        latest_entry = latest_fitness_data[-1]
        macros_str = f"Protein: {latest_entry.get("macros", {}).get("protein", 0)}g, " \
                     f"Carbs: {latest_entry.get("macros", {}).get("carbs", 0)}g, " \
                     f"Fat: {latest_entry.get("macros", {}).get("fat", 0)}g"
        
        fitness_context = (
            f"\n{user_name}\'s latest fitness data: "
            f"Steps: {latest_entry.get("steps")}, "
            f"Calories Burned: {latest_entry.get("calories_burned")}, "
            f"Heart Rate: {latest_entry.get("heart_rate")} bpm, "
            f"Macros: ({macros_str}). "
            "Use this information if relevant to the user\"s query, especially for questions about their daily progress. "
            "If the user asks to update their stats (e.g., \"Add 500 steps\", \"I ate 300 calories\"), acknowledge and confirm the update. "
            "For example, if they say \"Add 500 steps\", you can respond with \"Разбрано! Добавих 500 стъпки към днешния ти резултат.\" or \"Understood! I've added 500 steps to your daily total.\""
        )
    
    system_instruction = (
        f"You are {user_name}\'s personal fitness coach. You are fluent in both Bulgarian and English. "
        f"Your goal is to provide expert advice on workouts and nutrition tailored to {user_name}\'s needs. "
        "Always match the language used by the user. Be motivating and professional. "
        "You have real-time access to their health data. Use these specific numbers in your chat responses when relevant. "
        "If the user asks \"Как съм днес?\" or \"How am I doing today?\", respond based on their latest synced fitness data. "
        "If the user asks to update their stats (e.g., \"Add 500 steps\", \"I ate 300 calories\"), acknowledge and confirm the update. "
        "Do not actually perform the update, just confirm it in your response, as the system will handle the actual database update."
    )
    
    model = genai.GenerativeModel("gemini-3-flash-preview", system_instruction=system_instruction)
    chat_session = model.start_chat(history=history)

    # Save user message to history
    save_chat_message(user_id, "user", user_message)

    try:
        # Natural language processing for stat updates
        updated_stats = False
        if latest_fitness_data:
            current_entry = latest_fitness_data[-1].copy() # Work with a copy
            
            # Simple keyword matching for demonstration. A more robust solution would use NLP.
            if "add" in user_message.lower() or "добави" in user_message.lower():
                if "steps" in user_message.lower() or "стъпки" in user_message.lower():
                    try:
                        steps_to_add = int(''.join(filter(str.isdigit, user_message)))
                        current_entry["steps"] += steps_to_add
                        updated_stats = True
                    except ValueError: pass
                elif "calories" in user_message.lower() or "калории" in user_message.lower():
                    try:
                        calories_to_add = int(''.join(filter(str.isdigit, user_message)))
                        current_entry["calories_burned"] += calories_to_add
                        updated_stats = True
                    except ValueError: pass
            
            if updated_stats:
                save_fitness_data(user_id, current_entry)
                # Gemini will acknowledge the update based on the prompt

        # Combine user message with fitness context for Gemini
        full_prompt = f"{user_message}{fitness_context}"
        response = chat_session.send_message(full_prompt)
        ai_response = response.text
        
        # Save AI response to history
        save_chat_message(user_id, "model", ai_response)

        # Check for specific user query to trigger personalized response
        if "как съм днес" in user_message.lower() or "how am i doing today" in user_message.lower():
            if latest_fitness_data:
                ai_response = f"Здравейте, {user_name}! Виждам, че сте синхронизирали данните си от Apple Health. " \
                              f"Днес сте направили {latest_entry.get("steps")} стъпки, " \
                              f"изгорили сте {latest_entry.get("calories_burned")} калории, " \
                              f"и пулсът ви е {latest_entry.get("heart_rate")} удара в минута. " \
                              f"Вашите макроси са {macros_str}. Продължавайте все така!"
            else:
                ai_response = f"Здравейте, {user_name}! Все още нямам последните ви фитнес данни. " \
                              "Моля, синхронизирайте данните си от Apple Health или StepsApp първо."

        return jsonify({"response": ai_response})
    except Exception as e:
        return jsonify({"error": f"Gemini API error: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
