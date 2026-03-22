import os
import json
import asyncio
import httpx
import time 
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

from anthropic import AsyncAnthropic

load_dotenv()

app = FastAPI()

# ---------------------------------------------------------
# Configuration & Mock Database
# ---------------------------------------------------------
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ELEVENLABS_VOICE_ID = "EXAVITQu4vr4xnSDxMaL" 

anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

HOTEL_DB = {
    "checkout_time": "11:00 AM",
    "breakfast_hours": "7:00 AM to 10:00 AM",
    "gym_location": "2nd floor, requires room key",
}

# --- Tool Definition ---
def check_room_availability(room_type: str) -> str:
    inventory = {"standard": 5, "ocean view": 0, "suite": 2}
    room_type = room_type.lower()
    if room_type in inventory and inventory[room_type] > 0:
        return f"Database Result: Yes, {inventory[room_type]} '{room_type}' rooms available."
    elif room_type in inventory and inventory[room_type] == 0:
        return f"Database Result: Sorry, '{room_type}' rooms are sold out."
    return f"Database Result: '{room_type}' is not a valid category."

hotel_tools = [{
    "name": "check_room_availability",
    "description": "Check if a specific room type is available in the hotel's live inventory.",
    "input_schema": {
        "type": "object",
        "properties": {"room_type": {"type": "string"}},
        "required": ["room_type"]
    }
}]

# ---------------------------------------------------------
# The Frontend Interface (HTML/JS)
# ---------------------------------------------------------
html = """
<!DOCTYPE html>
<html>
<head>
    <title>Multilingual Concierge</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 650px; margin: 50px auto; padding: 20px; }
        button { padding: 10px 20px; font-size: 16px; cursor: pointer; margin-right: 10px; border-radius: 5px; border: 1px solid #ccc; }
        #startBtn { background-color: #e2f0d9; }
        #stopBtn { background-color: #fce4d6; }
        #replayBtn { background-color: #deebf7; }
        
        #status { font-weight: bold; font-size: 1.2em; transition: color 0.3s; color: #555; }
        #status.listening { color: #28a745; } 
        #status.thinking { color: #fd7e14; }  
        #status.speaking { color: #007bff; }  
        
        .dashboard { background: #1e1e1e; color: #00ff00; font-family: monospace; padding: 15px; border-radius: 8px; margin-top: 20px; }
        .metric-title { color: #fff; font-weight: bold; margin-bottom: 10px; display: block; border-bottom: 1px solid #444; padding-bottom: 5px;}
    </style>
</head>
<body>
    <h2>Front-Desk AI Concierge (End-to-End)</h2>
    <button id="startBtn">Start Listening</button>
    <button id="stopBtn" disabled>Stop</button>
    <button id="replayBtn" disabled>Replay Last Audio</button>
    
    <p id="status">Status: Disconnected</p>
    <p><strong>Transcript:</strong> <span id="transcript"></span></p>
    <p><strong>AI Response:</strong> <span id="response"></span></p>
    
    <div class="dashboard">
        <span class="metric-title">Latency Diagnostics</span>
        ASR Processing (Endpointing) : <span id="asr_metric">--</span> ms<br>
        LLM Time-to-First-Sentence   : <span id="llm_metric">--</span> ms<br>
        TTS Time-to-First-Byte       : <span id="tts_metric">--</span> ms<br>
        <strong>Total Pipeline Latency       : <span id="total_metric">--</span> ms</strong>
    </div>
    
    <script>
        let ws;
        let mediaRecorder;
        let audioQueue = [];
        let isPlaying = false;
        let currentAudio = null; 
        
        let currentRecording = [];
        let lastRecording = [];

        function playNextAudio() {
            if (audioQueue.length === 0) {
                isPlaying = false;
                return;
            }
            isPlaying = true;
            const audioBlob = audioQueue.shift();
            const audioUrl = URL.createObjectURL(audioBlob);
            currentAudio = new Audio(audioUrl);
            currentAudio.onended = playNextAudio; 
            currentAudio.play();
        }
        
        document.getElementById('startBtn').onclick = async () => {
            ws = new WebSocket(`ws://${window.location.host}/ws`);
            
            ws.onopen = async () => {
                document.getElementById('status').innerText = 'Status: Connected...';
                document.getElementById('startBtn').disabled = true;
                document.getElementById('stopBtn').disabled = false;

                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                
                mediaRecorder.ondataavailable = (event) => {
                    if (event.data.size > 0 && ws.readyState === WebSocket.OPEN) {
                        currentRecording.push(event.data); 
                        ws.send(event.data);
                    }
                };
                mediaRecorder.start(500); 
            };

            ws.onmessage = async (event) => {
                if (typeof event.data === "string") {
                    const data = JSON.parse(event.data);
                    
                    if (data.type === "status") {
                        const statusEl = document.getElementById('status');
                        statusEl.innerText = 'Status: ' + data.status;
                        statusEl.className = data.status.toLowerCase().replace('...', '');
                    }
                    if (data.type === "interrupt") {
                        audioQueue = []; 
                        if (currentAudio) { currentAudio.pause(); currentAudio = null; isPlaying = false; }
                    }
                    if (data.type === "transcript") document.getElementById('transcript').innerText = data.text;
                    if (data.type === "clear_response") document.getElementById('response').innerText = ""; 
                    if (data.type === "ai_text") document.getElementById('response').innerText += data.text + " "; 
                    
                    if (data.type === "speech_final") {
                        lastRecording = [...currentRecording];
                        currentRecording = [];
                        if (lastRecording.length > 0) document.getElementById('replayBtn').disabled = false;
                    }
                    
                    if (data.type === "metrics") {
                        document.getElementById('asr_metric').innerText = data.asr;
                        document.getElementById('llm_metric').innerText = data.llm;
                        document.getElementById('tts_metric').innerText = data.tts;
                        document.getElementById('total_metric').innerText = data.total;
                    }
                    
                } else {
                    const audioBlob = new Blob([event.data], { type: 'audio/mp3' });
                    audioQueue.push(audioBlob);
                    if (!isPlaying) playNextAudio();
                }
            };
        };

        document.getElementById('replayBtn').onclick = () => {
            if (lastRecording.length > 0 && ws && ws.readyState === WebSocket.OPEN) {
                document.getElementById('status').innerText = 'Status: Sending Replay...';
                lastRecording.forEach(blob => ws.send(blob));
            }
        };

        document.getElementById('stopBtn').onclick = () => {
            if (mediaRecorder) mediaRecorder.stop();
            if (ws) ws.close();
            audioQueue = [];
            currentRecording = [];
            if (currentAudio) currentAudio.pause();
            isPlaying = false;
            document.getElementById('status').innerText = 'Status: Disconnected';
            document.getElementById('status').className = '';
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
            document.getElementById('replayBtn').disabled = true;
        };
    </script>
</body>
</html>
"""

@app.get("/")
async def get_frontend():
    return HTMLResponse(html)

# ---------------------------------------------------------
# The Brain (LLM Streaming)
# ---------------------------------------------------------
async def generate_claude_stream(transcript: str, language_code: str):
    system_prompt = f"""
    You are a helpful hotel front desk concierge. 
    Identify the language the user is speaking, and reply natively in that exact language.
    Static Hotel Info: {json.dumps(HOTEL_DB)}
    If the user asks about room availability, you MUST use the check_room_availability tool.
    Keep responses to 2 short sentences.
    """
    
    messages = [{"role": "user", "content": transcript}]
    stream = await anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=150, temperature=0.3,
        system=system_prompt, messages=messages, tools=hotel_tools, stream=True
    )
    
    current_sentence = ""
    is_tool_call = False
    tool_use_id = tool_name = tool_input_json = ""

    async for event in stream:
        if event.type == "content_block_start" and event.content_block.type == "tool_use":
            is_tool_call = True
            tool_use_id = event.content_block.id
            tool_name = event.content_block.name
        elif event.type == "content_block_delta" and is_tool_call:
            tool_input_json += event.delta.partial_json
        elif event.type == "content_block_delta" and not is_tool_call:
            text_chunk = event.delta.text
            current_sentence += text_chunk
            if any(punct in text_chunk for punct in ['.', '!', '?', '\n']):
                if current_sentence.strip():
                    yield current_sentence.strip()
                    current_sentence = ""
                    
    if current_sentence.strip(): yield current_sentence.strip()

    if is_tool_call:
        tool_args = json.loads(tool_input_json)
        db_result = check_room_availability(tool_args.get("room_type", "")) if tool_name == "check_room_availability" else "Error."
        messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": tool_args}]})
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": db_result}]})

        follow_up_stream = await anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=150, system=system_prompt, messages=messages, tools=hotel_tools, stream=True
        )

        current_sentence = ""
        async for event in follow_up_stream:
            if event.type == "content_block_delta" and hasattr(event.delta, "text"):
                text_chunk = event.delta.text
                current_sentence += text_chunk
                if any(punct in text_chunk for punct in ['.', '!', '?', '\n']):
                    if current_sentence.strip():
                        yield current_sentence.strip()
                        current_sentence = ""
        if current_sentence.strip(): yield current_sentence.strip()

# ---------------------------------------------------------
# Voice Output
# ---------------------------------------------------------
async def get_elevenlabs_audio(text: str) -> bytes:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": ELEVENLABS_API_KEY}
    data = {"text": text, "model_id": "eleven_multilingual_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=data, headers=headers)
        return response.content

# ---------------------------------------------------------
# WebSocket Orchestration (Phase 5: Full Latency Tracking)
# ---------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(client_ws: WebSocket):
    await client_ws.accept()
    dg_url = "wss://api.deepgram.com/v1/listen?model=nova-3&smart_format=true&language=multi&endpointing=500"
    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}

    is_processing = False 
    cancel_response = False 

    try:
        async with websockets.connect(dg_url, additional_headers=headers) as dg_ws:
            
            async def sender():
                try:
                    while True:
                        data = await client_ws.receive_bytes()
                        await dg_ws.send(data)
                except WebSocketDisconnect: pass
                except Exception as e: print(f"Sender Error: {e}")

            async def receiver():
                nonlocal is_processing, cancel_response
                transcript_buffer = "" 
                last_word_time = time.time()
                
                try:
                    while True:
                        result = await dg_ws.recv()
                        res_json = json.loads(result)
                        
                        if res_json.get("is_final"):
                            alternatives = res_json.get("channel", {}).get("alternatives", [])
                            if alternatives:
                                chunk_text = alternatives[0].get("transcript", "")
                                if chunk_text.strip():
                                    
                                    # Record the time the user actually stopped speaking
                                    last_word_time = time.time()
                                    
                                    if is_processing:
                                        cancel_response = True
                                        await client_ws.send_text(json.dumps({"type": "interrupt"}))
                                        
                                    await client_ws.send_text(json.dumps({"type": "status", "status": "Listening..."}))
                                    transcript_buffer += chunk_text + " "
                                    
                        if res_json.get("speech_final"):
                            await client_ws.send_text(json.dumps({"type": "speech_final"}))
                            
                            full_sentence = transcript_buffer.strip()
                            transcript_buffer = "" 
                            
                            if full_sentence:
                                is_processing = True 
                                cancel_response = False
                                
                                # Calculate how long Deepgram waited before firing speech_final
                                asr_latency_ms = round((time.time() - last_word_time) * 1000)
                                
                                llm_start_time = time.time()
                                first_metric_sent = False
                                
                                await client_ws.send_text(json.dumps({"type": "status", "status": "Thinking..."}))
                                
                                try:
                                    detected_lang = alternatives[0].get("languages", ["en"])[0] if alternatives else "en"
                                    await client_ws.send_text(json.dumps({"type": "transcript", "text": full_sentence}))
                                    await client_ws.send_text(json.dumps({"type": "clear_response"}))
                                    
                                    async for sentence in generate_claude_stream(full_sentence, detected_lang):
                                        if cancel_response: break
                                        
                                        llm_time = time.time()
                                        
                                        await client_ws.send_text(json.dumps({"type": "status", "status": "Speaking..."}))
                                        await client_ws.send_text(json.dumps({"type": "ai_text", "text": sentence}))
                                        
                                        tts_start = time.time()
                                        audio_bytes = await get_elevenlabs_audio(sentence)
                                        tts_time = time.time()
                                        
                                        await client_ws.send_bytes(audio_bytes)
                                        
                                        if not first_metric_sent:
                                            llm_latency_ms = round((llm_time - llm_start_time) * 1000)
                                            tts_latency_ms = round((tts_time - tts_start) * 1000)
                                            
                                            # Total latency from the moment you stopped speaking to the first audio byte playing
                                            total_latency_ms = asr_latency_ms + llm_latency_ms + tts_latency_ms
                                            
                                            await client_ws.send_text(json.dumps({
                                                "type": "metrics",
                                                "asr": asr_latency_ms,
                                                "llm": llm_latency_ms,
                                                "tts": tts_latency_ms,
                                                "total": total_latency_ms
                                            }))
                                            first_metric_sent = True
                                        
                                except Exception as ai_error:
                                    print(f"AI Pipeline Error: {ai_error}")
                                    
                                finally:
                                    is_processing = False
                                    if not cancel_response:
                                        await client_ws.send_text(json.dumps({"type": "status", "status": "Listening..."}))
                                        
                except websockets.exceptions.ConnectionClosed: pass
                except Exception as e: print(f"Receiver Error: {e}")

            await asyncio.gather(sender(), receiver())

    except Exception as e:
        print(f"WebSocket Error: {e}")