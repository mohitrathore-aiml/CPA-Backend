import os
import datetime
import json
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import google.generativeai as genai

app = Flask(__name__)

CORS(app, origins=["*"])

API_KEY = os.getenv('GEMINI_API_KEY', 'AIzaSyDoM-RWga4GUGk9ugReXckXORnvtNcDYUo')
MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
PORT = int(os.getenv('PORT', 3000))

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

print("✓ Using model: gemini-2.5-flash")


client = MongoClient(MONGODB_URI)
db = client['cpa']
complaints_col = db['complaints']

def analyze_with_ai(text):
    """Uses Gemini to summarize and prioritize the complaint."""
    
    prompt = f"""Analyze this citizen complaint and determine its priority level.

Complaint: "{text}"

Priority Guidelines:
- Critical: Life-threatening emergencies, major safety hazards, complete infrastructure failures
- High: Significant public health/safety risks, urgent infrastructure damage, major service disruptions
- Medium: Moderate issues affecting daily life, non-urgent repairs needed
- Low: Minor inconveniences, aesthetic issues, general suggestions

Return ONLY a JSON object (no markdown, no extra text):
{{
    "summary": "one sentence summary",
    "priority": "Critical or High or Medium or Low",
    "reason": "brief explanation"
}}"""
    
    try:
        print(f"\n=== ANALYZING: {text[:80]}... ===")
        
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        
        print(f"Raw Response: {raw_text[:200]}...")
        
        clean_text = re.sub(r'^```(?:json)?\s*', '', raw_text)
        clean_text = re.sub(r'\s*```$', '', clean_text)
        clean_text = clean_text.strip()
        
        result = json.loads(clean_text)
        
        priority = result.get('priority', 'Medium').strip()
        valid_priorities = ["Critical", "High", "Medium", "Low"]
        
        if priority not in valid_priorities:
            priority_lower = priority.lower()
            if 'critical' in priority_lower:
                priority = "Critical"
            elif 'high' in priority_lower:
                priority = "High"
            elif 'low' in priority_lower:
                priority = "Low"
            else:
                priority = "Medium"
        
        result['priority'] = priority
        
        print(f"✓ Priority: {priority}")
        print(f"✓ Summary: {result.get('summary')[:80]}...\n")
        
        return result
        
    except Exception as e:
        print(f"!!! ERROR: {type(e).__name__}: {e}")
        
        text_lower = text.lower()
        
        if any(word in text_lower for word in ['emergency', 'urgent', 'critical', 'life threatening', 'death', 'danger', 'fire', 'explosion']):
            priority = "Critical"
            reason = "Emergency keywords detected"
        elif any(word in text_lower for word in ['broken', 'damage', 'leak', 'flood', 'burst', 'collapse']):
            priority = "High"
            reason = "Urgent repair needed"
        elif any(word in text_lower for word in ['repair', 'fix', 'issue', 'problem', 'not working']):
            priority = "Medium"
            reason = "Standard repair request"
        else:
            priority = "Low"
            reason = "General complaint"
        
        return {
            "summary": text[:100] + "..." if len(text) > 100 else text,
            "priority": priority,
            "reason": f"{reason} (AI fallback)"
        }

def convert_to_json(doc):
    """Convert MongoDB document with datetime objects to JSON"""
    if doc is None:
        return None
    doc.pop('_id', None)
    for key, value in doc.items():
        if isinstance(value, datetime.datetime):
            doc[key] = value.isoformat()
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, datetime.datetime):
                            item[k] = v.isoformat()
    return doc

@app.route('/api/complaints', methods=['POST'])
def create_complaint():
    data = request.json
    
    print(f"\n>>> NEW COMPLAINT <<<")
    print(f"Text: {data.get('citizenText')}")
    
    analysis = analyze_with_ai(data.get('citizenText'))
    
    print(f"Final Priority: {analysis.get('priority')}")
    print(f"Reason: {analysis.get('reason')}\n")
    
    new_complaint = {
        "complaintId": f"CPA-{int(datetime.datetime.now().timestamp())}",
        "citizenText": data.get('citizenText'),
        "aiSummary": analysis.get('summary'),
        "priority": analysis.get('priority'),
        "aiPriorityReason": analysis.get('reason'),
        "department": data.get('department'),
        "location": data.get('location'),
        "status": "Submitted",
        "createdAt": datetime.datetime.utcnow(),
        "slaHours": 24,
        "progressTimeline": [{
            "stage": "Submitted",
            "message": "Complaint analyzed by AI",
            "updatedBy": "system",
            "timestamp": datetime.datetime.utcnow()
        }]
    }
    
    complaints_col.insert_one(new_complaint)
    return jsonify(convert_to_json(new_complaint)), 201

@app.route('/api/complaints', methods=['GET'])
def get_all_complaints():
    complaints = list(complaints_col.find().sort("createdAt", -1))
    return jsonify([convert_to_json(c) for c in complaints])

@app.route('/api/complaints/<id>', methods=['GET'])
def get_one_complaint(id):
    complaint = complaints_col.find_one({"complaintId": id})
    if complaint:
        return jsonify(convert_to_json(complaint))
    return jsonify({"error": "Not found"}), 404

@app.route('/api/complaints/<id>/progress', methods=['PATCH'])
def update_progress(id):
    data = request.json
    update_data = {
        "status": data.get('status'),
        "updatedAt": datetime.datetime.utcnow()
    }
    
    new_step = {
        "stage": data.get('status'),
        "message": data.get('note'),
        "updatedBy": data.get('updatedBy', 'authority'),
        "timestamp": datetime.datetime.utcnow()
    }
    
    complaints_col.update_one(
        {"complaintId": id},
        {"$set": update_data, "$push": {"progressTimeline": new_step}}
    )
    return jsonify({"success": True})

@app.route('/api/complaints/<id>/resolve', methods=['PATCH'])
def resolve_complaint(id):
    res_type = request.json.get('resolutionType', 'Permanent')
    now = datetime.datetime.utcnow()
    
    complaints_col.update_one(
        {"complaintId": id},
        {"$set": {
            "status": "Resolved",
            "resolutionType": res_type,
            "resolvedAt": now
        }, "$push": {"progressTimeline": {
            "stage": "Resolved",
            "message": f"Resolved as {res_type}",
            "updatedBy": "authority",
            "timestamp": now
        }}}
    )
    return jsonify({"success": True})

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "database": "connected"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
