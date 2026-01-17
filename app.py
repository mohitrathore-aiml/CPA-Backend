import os
import datetime
import json
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import google.generativeai as genai


app = Flask(__name__)
CORS(app)


GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
MONGODB_URI = os.environ["MONGODB_URI"]


genai.configure(api_key=GEMINI_API_KEY)

mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
db = mongo_client["cpa"]
complaints_col = db["complaints"]


def get_model():
    return genai.GenerativeModel("gemini-2.5-flash")


def analyze_with_ai(text: str):
    prompt = f"""
Analyze this citizen complaint and determine its priority level.

Complaint: "{text}"

Return ONLY valid JSON:
{{
  "summary": "one sentence summary",
  "priority": "Critical | High | Medium | Low",
  "reason": "brief explanation"
}}
"""
    try:
        model = get_model()
        response = model.generate_content(prompt)
        clean = response.text.strip()
        clean = re.sub(r"^```(?:json)?", "", clean)
        clean = re.sub(r"```$", "", clean)
        return json.loads(clean)
    except Exception:
        return {
            "summary": text[:100],
            "priority": "Medium",
            "reason": "AI fallback"
        }


def serialize(doc):
    doc.pop("_id", None)
    for k, v in doc.items():
        if isinstance(v, datetime.datetime):
            doc[k] = v.isoformat()
        if isinstance(v, list):
            for i in v:
                if isinstance(i, dict):
                    for kk, vv in i.items():
                        if isinstance(vv, datetime.datetime):
                            i[kk] = vv.isoformat()
    return doc


@app.route("/")
def root():
    return "CPA Backend is running"


@app.route("/health")
def health():
    try:
        mongo_client.admin.command("ping")
        return jsonify({"status": "healthy"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@app.route("/api/complaints", methods=["POST"])
def create_complaint():
    data = request.get_json(silent=True)
    if not data or "citizenText" not in data:
        return jsonify({"error": "citizenText required"}), 400

    analysis = analyze_with_ai(data["citizenText"])

    doc = {
        "complaintId": f"CPA-{int(datetime.datetime.utcnow().timestamp())}",
        "citizenText": data["citizenText"],
        "aiSummary": analysis["summary"],
        "priority": analysis["priority"],
        "aiPriorityReason": analysis["reason"],
        "department": data.get("department"),
        "location": data.get("location"),
        "status": "Submitted",
        "createdAt": datetime.datetime.utcnow(),
        "progressTimeline": [{
            "stage": "Submitted",
            "message": "Complaint analyzed",
            "updatedBy": "system",
            "timestamp": datetime.datetime.utcnow()
        }]
    }

    complaints_col.insert_one(doc)
    return jsonify(serialize(doc)), 201


@app.route("/api/complaints", methods=["GET"])
def list_complaints():
    docs = complaints_col.find().sort("createdAt", -1)
    return jsonify([serialize(d) for d in docs])


@app.route("/api/complaints/<cid>", methods=["GET"])
def get_complaint(cid):
    doc = complaints_col.find_one({"complaintId": cid})
    if not doc:
        return jsonify({"error": "Not found"}), 404
    return jsonify(serialize(doc))
